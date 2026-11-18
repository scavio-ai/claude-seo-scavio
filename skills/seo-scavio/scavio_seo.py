#!/usr/bin/env python3
"""Scavio for Claude SEO: a small command-line client for the Scavio API.

Every command prints one JSON document on stdout and one status line on
stderr. Commands that would spend more than CONFIRM_ABOVE credits print the
planned calls and exit 2 unless --yes is passed. --dry-run prints the plan for
any command and spends nothing.

Standard library only (Python 3.9+). The API key is read from SCAVIO_API_KEY,
else from ~/.config/claude-seo/scavio.json (written by install.sh). The key is
sent only in the Authorization header to https://api.scavio.dev and is never
printed.

Exit codes: 0 ok, 1 unexpected error, 2 bad arguments or confirmation needed,
3 key rejected or out of credits, 4 rate limited, 5 upstream unavailable.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

VERSION = "1.0.0"
API_BASE = "https://api.scavio.dev"
USER_AGENT = f"claude-seo-scavio/{VERSION}"
CONFIG_PATH = Path.home() / ".config" / "claude-seo" / "scavio.json"
CONFIRM_ABOVE = 5
TEXT_LIMIT = 4000
MD_LINK = re.compile(r"\]\((https?://[^\s)]+)\)")

# name -> (method, path, credits, timeout seconds)
ENDPOINTS: Dict[str, Tuple[str, str, int, int]] = {
    "usage": ("GET", "/api/v1/usage", 0, 30),
    "google": ("POST", "/api/v2/google", 1, 90),
    "aimode": ("POST", "/api/v2/google/ai-mode", 1, 90),
    "chatgpt": ("POST", "/api/v1/chatgpt/prompt", 2, 180),
    "gemini": ("POST", "/api/v1/gemini/prompt", 5, 180),
    "reddit_posts": ("POST", "/api/v1/reddit/search", 1, 60),
    "reddit_comments": ("POST", "/api/v1/reddit/search/comments", 1, 60),
    "youtube": ("POST", "/api/v1/youtube/search", 2, 60),
    "maps": ("POST", "/api/v2/google/maps/search", 1, 90),
    "reviews": ("POST", "/api/v2/google/maps/reviews", 1, 90),
    "grid": ("POST", "/api/v2/google/maps/geo-grid", 0, 240),  # N*N, see grid_cost
}

ENGINES = {
    "chatgpt": "ChatGPT",
    "aimode": "Google AI Mode",
    "gemini": "Gemini",
    "aio": "Google AI Overview",
}
DEFAULT_ENGINES = ["chatgpt", "aimode", "gemini", "aio"]


class ScavioError(Exception):
    def __init__(self, message: str, status: Optional[int] = None, exit_code: int = 1):
        super().__init__(message)
        self.status = status
        self.exit_code = exit_code


# --------------------------------------------------------------------------
# Key and HTTP
# --------------------------------------------------------------------------

def load_key(env: Optional[Dict[str, str]] = None, config_path: Path = CONFIG_PATH) -> str:
    env = os.environ if env is None else env
    key = (env.get("SCAVIO_API_KEY") or "").strip()
    if key:
        return key
    if config_path.exists():
        try:
            data = json.loads(config_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise ScavioError(f"{config_path} is not readable JSON. Rerun install.sh.", exit_code=3)
        key = str(data.get("api_key") or "").strip() if isinstance(data, dict) else ""
        if key:
            return key
    raise ScavioError(
        "No Scavio API key. Run install.sh (or install.ps1), or set SCAVIO_API_KEY. "
        "Free key (50 credits, no card): https://dashboard.scavio.dev/sign-up",
        exit_code=3,
    )


def _error_message(body: bytes) -> str:
    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError:
        return body.decode("utf-8", "replace")[:300].strip()
    if isinstance(data, dict):
        err = data.get("error") or data.get("message") or data.get("detail")
        if isinstance(err, dict):
            err = err.get("message") or json.dumps(err)
        if err:
            billing = data.get("billing_url")
            return str(err)[:300] + (f" Top up: {billing}" if isinstance(billing, str) else "")
    return json.dumps(data)[:300]


def _exit_code_for(status: int) -> int:
    if status in (401, 402, 403):
        return 3
    if status == 429:
        return 4
    if status >= 500:
        return 5
    return 1


Transport = Callable[[urllib.request.Request, int], Tuple[int, Dict[str, str], bytes]]


def _urllib_transport(req: urllib.request.Request, timeout: int) -> Tuple[int, Dict[str, str], bytes]:
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read() or b""


class Client:
    def __init__(self, key: str, transport: Transport = _urllib_transport,
                 sleep: Callable[[float], None] = time.sleep, base: str = API_BASE):
        self.key = key
        self.transport = transport
        self.sleep = sleep
        self.base = base
        self.credits_used = 0
        self.credits_remaining: Optional[int] = None
        self._lock = threading.Lock()

    def call(self, name: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        method, path, _, timeout = ENDPOINTS[name]
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "Authorization": f"Bearer {self.key}",
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
        # 502 and 503 are not charged; retry once after a short wait.
        for attempt in (1, 2):
            req = urllib.request.Request(self.base + path, data=data, method=method, headers=headers)
            try:
                status, resp_headers, raw = self.transport(req, timeout)
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                if attempt == 1:
                    self.sleep(3)
                    continue
                raise ScavioError(f"Network error calling {path}: {e}", exit_code=5)
            if status in (502, 503) and attempt == 1:
                wait = 3.0
                retry_after = {k.lower(): v for k, v in resp_headers.items()}.get("retry-after")
                if retry_after and retry_after.isdigit():
                    wait = min(float(retry_after), 30.0)
                self.sleep(wait)
                continue
            break
        if status >= 400:
            raise ScavioError(f"HTTP {status} from {path}: {_error_message(raw)}",
                              status=status, exit_code=_exit_code_for(status))
        try:
            payload = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise ScavioError(f"Non-JSON response from {path}", status=status, exit_code=5)
        if isinstance(payload, dict):
            used = payload.get("credits_used")
            remaining = payload.get("credits_remaining")
            with self._lock:
                if isinstance(used, int):
                    self.credits_used += used
                if isinstance(remaining, int) and (self.credits_remaining is None
                                                   or remaining < self.credits_remaining):
                    self.credits_remaining = remaining
        return payload


# --------------------------------------------------------------------------
# Text and brand matching
# --------------------------------------------------------------------------

def normalize_domain(value: str) -> str:
    """'https://www.Example.com/x' or 'example.com' -> 'example.com'."""
    v = (value or "").strip().lower()
    if not v:
        return ""
    if "://" not in v:
        v = "http://" + v
    host = urllib.parse.urlsplit(v).hostname or ""
    return host[4:] if host.startswith("www.") else host


def domain_of(url: Optional[str]) -> str:
    """Host of a URL. Google's displayed_link ('https://x.com › Blog') works too."""
    if not url:
        return ""
    first = str(url).split(" ")[0].split("›")[0].strip()
    return normalize_domain(first)


def domain_matches(host: str, domain: str) -> bool:
    return bool(host and domain) and (host == domain or host.endswith("." + domain))


def collect_text(obj: Any) -> List[str]:
    """Every 'snippet', 'text' and 'title' string in a nested block list."""
    out: List[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("snippet", "text", "title") and isinstance(v, str):
                out.append(v)
            elif isinstance(v, (dict, list)):
                out.extend(collect_text(v))
    elif isinstance(obj, list):
        for item in obj:
            out.extend(collect_text(item))
    return out


def collect_links(obj: Any) -> List[str]:
    out: List[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("link", "url") and isinstance(v, str) and v.startswith("http"):
                out.append(v)
            elif isinstance(v, (dict, list)):
                out.extend(collect_links(v))
    elif isinstance(obj, list):
        for item in obj:
            out.extend(collect_links(item))
    return out


def _brand_pattern(term: str) -> re.Pattern:
    return re.compile(r"(?<![\w])" + re.escape(term) + r"(?![\w])", re.IGNORECASE)


def find_mention(text: str, terms: List[str]) -> Optional[str]:
    """The line around the first mention of any term, or None."""
    best: Optional[Tuple[int, int]] = None
    for term in terms:
        if not term:
            continue
        m = _brand_pattern(term).search(text)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), m.end())
    if best is None:
        return None
    start = text.rfind("\n", 0, best[0]) + 1
    end = text.find("\n", best[1])
    end = len(text) if end == -1 else end
    line = text[start:end].strip()
    if len(line) > 240:
        lo = max(0, best[0] - start - 100)
        line = ("..." if lo else "") + line[lo:lo + 240] + "..."
    return line


def brand_terms(brand: Optional[str], domain: Optional[str]) -> List[str]:
    terms = []
    if brand:
        terms.append(brand.strip())
    if domain:
        terms.append(normalize_domain(domain))
    return [t for t in terms if t]


def analyze(text: str, cited_urls: List[str], brand: Optional[str], domain: Optional[str]) -> Dict[str, Any]:
    terms = brand_terms(brand, domain)
    line = find_mention(text, terms) if terms else None
    nd = normalize_domain(domain) if domain else ""
    cited = [u for u in cited_urls if nd and domain_matches(domain_of(u), nd)]
    return {
        "mentioned": line is not None,
        "first_mention": line,
        "cited": bool(cited),
        "cited_urls": cited[:5],
    }


def _truncate(text: str, limit: int = TEXT_LIMIT) -> str:
    return text if len(text) <= limit else text[:limit] + f"... [truncated, {len(text)} chars]"


def _dedupe(seq: List[str]) -> List[str]:
    seen, out = set(), []
    for s in seq:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


# --------------------------------------------------------------------------
# Normalizers (one per engine / endpoint)
# --------------------------------------------------------------------------

def norm_llm(payload: Dict[str, Any]) -> Dict[str, Any]:
    d = payload.get("data") or {}
    answer = ((d.get("answer") or {}).get("markdown")) or ""
    sources = [
        {"url": s.get("url"), "title": s.get("title"), "source": s.get("source"),
         "published_at": s.get("published_at")}
        for s in (d.get("sources") or []) if isinstance(s, dict) and s.get("url")
    ]
    source_urls = {x["url"] for x in sources}
    inline = [u.rstrip(".,;") for u in MD_LINK.findall(answer)]
    return {
        "model": d.get("model"),
        "answer": answer,
        "sources": sources,
        "inline_links": _dedupe([u for u in inline if u not in source_urls]),
        "search_queries": d.get("search_queries") or [],
        "products": [p.get("title") for p in (d.get("shopping") or []) if isinstance(p, dict) and p.get("title")],
    }


def norm_blocks(text_blocks: Any, references: Any) -> Dict[str, Any]:
    text = "\n".join(collect_text(text_blocks or []))
    refs = [
        {"url": r.get("link"), "title": r.get("title"), "source": r.get("source")}
        for r in (references or []) if isinstance(r, dict) and r.get("link")
    ]
    inline = [u for u in collect_links(text_blocks or []) if u not in {r["url"] for r in refs}]
    return {"answer": text, "sources": refs, "inline_links": _dedupe(inline)}


def norm_aimode(payload: Dict[str, Any]) -> Dict[str, Any]:
    out = norm_blocks(payload.get("text_blocks"), payload.get("references"))
    out["products"] = [p.get("title") for p in (payload.get("shopping_results") or [])
                       if isinstance(p, dict) and p.get("title")]
    return out


def norm_serp(payload: Dict[str, Any], limit: int = 10) -> Dict[str, Any]:
    organic = []
    for r in (payload.get("organic_results") or [])[:limit]:
        if not isinstance(r, dict):
            continue
        link = r.get("link") or ""
        # Some links come back as google.com/goto redirects; the displayed link
        # still carries the real host.
        host = domain_of(r.get("displayed_link")) if "google.com/goto" in link else domain_of(link)
        organic.append({
            "position": r.get("position"),
            "title": r.get("title"),
            "domain": host,
            "link": None if "google.com/goto" in link else link,
            "displayed_link": r.get("displayed_link"),
            "snippet": r.get("snippet"),
        })
    aio = payload.get("ai_overview")
    return {
        "organic": organic,
        "ai_overview": norm_blocks(aio.get("text_blocks"), aio.get("references")) if isinstance(aio, dict) else None,
        "people_also_ask": [q.get("question") for q in (payload.get("related_questions") or [])
                            if isinstance(q, dict) and q.get("question")],
        "has_local_pack": bool(payload.get("local_results")),
    }


def serp_brand(serp: Dict[str, Any], domain: Optional[str]) -> Dict[str, Any]:
    nd = normalize_domain(domain) if domain else ""
    positions = [r["position"] for r in serp["organic"] if nd and domain_matches(r["domain"], nd)]
    return {"organic_positions": positions, "best_position": min(positions) if positions else None}


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------

def grid_cost(size: int) -> int:
    return size * size


def plan_for(args: argparse.Namespace) -> List[Dict[str, Any]]:
    c = args.command
    if c == "check":
        return [{"endpoint": "usage", "credits": 0}]
    if c == "serp":
        return [{"endpoint": "google", "credits": 1}]
    if c == "ask":
        name = "google" if args.engine == "aio" else args.engine
        return [{"endpoint": name, "engine": args.engine, "credits": ENDPOINTS[name][2]}]
    if c == "visibility":
        calls = []
        for e in args.engines:
            name = "google" if e == "aio" else e
            calls.append({"endpoint": name, "engine": e, "credits": ENDPOINTS[name][2]})
        return calls
    if c == "mentions":
        calls = [{"endpoint": "reddit_posts", "credits": 1}, {"endpoint": "reddit_comments", "credits": 1}]
        if not args.no_youtube:
            calls.append({"endpoint": "youtube", "credits": 2})
        return calls
    if c == "maps":
        return [{"endpoint": "maps", "credits": 1}]
    if c == "reviews":
        return [{"endpoint": "reviews", "credits": 1}]
    if c == "grid":
        return [{"endpoint": "grid", "credits": grid_cost(args.size), "note": "billed per grid point"}]
    raise ScavioError(f"Unknown command {c}", exit_code=2)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def run_engine(client: Client, engine: str, prompt: str, country: Optional[str],
               brand: Optional[str], domain: Optional[str]) -> Dict[str, Any]:
    if engine == "chatgpt":
        body: Dict[str, Any] = {"prompt": prompt}
        if country:
            body["country"] = country.lower()
        res = norm_llm(client.call("chatgpt", body))
    elif engine == "gemini":
        res = norm_llm(client.call("gemini", {"prompt": prompt}))
        res["country_note"] = "Gemini has no country parameter; answer is not localized."
    elif engine == "aimode":
        body = {"query": prompt}
        if country:
            body["gl"] = country.lower()
        res = norm_aimode(client.call("aimode", body))
    elif engine == "aio":
        body = {"query": prompt}
        if country:
            body["gl"] = country.lower()
        serp = norm_serp(client.call("google", body))
        aio = serp["ai_overview"]
        if aio is None:
            res = {"answer": "", "sources": [], "inline_links": [], "no_ai_overview": True}
        else:
            res = aio
        res["organic_top10"] = [{"position": r["position"], "domain": r["domain"]} for r in serp["organic"]]
        if domain:
            res["organic"] = serp_brand(serp, domain)
    else:
        raise ScavioError(f"Unknown engine {engine}", exit_code=2)
    if brand or domain:
        cited = [s["url"] for s in res.get("sources", [])] + res.get("inline_links", [])
        res["brand"] = analyze(res.get("answer", ""), cited, brand, domain)
    res["answer"] = _truncate(res.get("answer", ""))
    return res


def cmd_check(client: Client, args: argparse.Namespace) -> Dict[str, Any]:
    u = client.call("usage")
    return {
        "ok": True,
        "plan": u.get("plan"),
        "credit_balance": u.get("credit_balance"),
        "period_start": u.get("period_start"),
    }


def cmd_serp(client: Client, args: argparse.Namespace) -> Dict[str, Any]:
    body: Dict[str, Any] = {"query": args.query}
    for k in ("gl", "hl", "location", "device"):
        v = getattr(args, k)
        if v:
            body[k] = v
    if args.start:
        body["start"] = args.start
    if args.web_only:
        body["web_only"] = True
    serp = norm_serp(client.call("google", body))
    if args.domain:
        serp["brand"] = serp_brand(serp, args.domain)
        if serp["ai_overview"]:
            cited = [s["url"] for s in serp["ai_overview"]["sources"]] + serp["ai_overview"]["inline_links"]
            serp["brand"]["ai_overview"] = analyze(serp["ai_overview"]["answer"], cited, args.brand, args.domain)
    if serp["ai_overview"]:
        serp["ai_overview"]["answer"] = _truncate(serp["ai_overview"]["answer"])
    return {"query": args.query, **serp}


def cmd_ask(client: Client, args: argparse.Namespace) -> Dict[str, Any]:
    res = run_engine(client, args.engine, args.prompt, args.country, args.brand, args.domain)
    return {"engine": ENGINES[args.engine], "prompt": args.prompt, "country": args.country, **res}


def cmd_visibility(client: Client, args: argparse.Namespace) -> Dict[str, Any]:
    def one(e: str) -> Tuple[str, Any]:
        try:
            return e, run_engine(client, e, args.prompt, args.country, args.brand, args.domain)
        except ScavioError as err:
            return e, err

    # Engines run in parallel; a 429 means the plan allows fewer concurrent
    # requests (the free plan runs one at a time), so those rerun one by one.
    with ThreadPoolExecutor(max_workers=len(args.engines)) as pool:
        results = dict(pool.map(one, args.engines))
    for e in args.engines:
        r = results[e]
        if isinstance(r, ScavioError) and r.status == 429:
            results[e] = one(e)[1]
    rows, failed = [], 0
    for e in args.engines:
        r = results[e]
        if isinstance(r, ScavioError):
            if r.exit_code == 3:
                raise r
            failed += 1
            rows.append({"engine": ENGINES[e], "status": "error", "error": str(r)})
        else:
            rows.append({"engine": ENGINES[e], "status": "ok", **r})
    if failed == len(rows):
        raise ScavioError("Every engine failed: " + "; ".join(r["error"] for r in rows), exit_code=5)
    summary = [
        {"engine": r["engine"], "mentioned": r.get("brand", {}).get("mentioned"),
         "cited": r.get("brand", {}).get("cited")}
        for r in rows if r["status"] == "ok"
    ]
    return {"prompt": args.prompt, "country": args.country, "brand": args.brand,
            "domain": args.domain, "summary": summary, "engines": rows}


def cmd_mentions(client: Client, args: argparse.Namespace) -> Dict[str, Any]:
    q = f'"{args.brand}"'
    out: Dict[str, Any] = {"brand": args.brand}
    posts = (client.call("reddit_posts", {"query": q}).get("data") or {}).get("results") or []
    out["reddit_posts"] = [
        {"title": p.get("title"), "subreddit": p.get("subreddit"), "url": p.get("url"),
         "score": p.get("score"), "num_comments": p.get("num_comments"), "created_at": p.get("created_at"),
         "text": _truncate(p.get("text") or "", 400)}
        for p in posts if isinstance(p, dict)
    ][: args.limit]
    comments = (client.call("reddit_comments", {"query": q, "sort": "NEW"}).get("data") or {}).get("results") or []
    out["reddit_comments"] = [
        {"subreddit": c.get("subreddit"), "url": c.get("permalink"), "score": c.get("score"),
         "created_at": c.get("created_at"), "text": _truncate(c.get("text") or "", 400)}
        for c in comments if isinstance(c, dict)
    ][: args.limit]
    if not args.no_youtube:
        yt_body: Dict[str, Any] = {"search": args.brand}
        if args.since != "any":
            yt_body["upload_date"] = args.since
        yt = client.call("youtube", yt_body).get("data") or {}
        vids = [v for v in (yt.get("results") or []) if isinstance(v, dict) and v.get("type") == "video"]
        out["youtube_videos"] = [
            {"title": v.get("title"), "url": v.get("url"), "channel": (v.get("channel") or {}).get("name"),
             "view_count": v.get("view_count"), "published": v.get("published_time")}
            for v in vids
        ][: args.limit]
        brand_re = _brand_pattern(args.brand)
        out["youtube_title_mentions"] = sum(1 for v in out["youtube_videos"] if brand_re.search(v["title"] or ""))
    subs: Dict[str, int] = {}
    for item in out["reddit_posts"] + out["reddit_comments"]:
        if item.get("subreddit"):
            subs[item["subreddit"]] = subs.get(item["subreddit"], 0) + 1
    out["top_subreddits"] = sorted(subs.items(), key=lambda kv: -kv[1])[:10]
    return out


def _parse_ll(args: argparse.Namespace) -> Optional[str]:
    if args.ll:
        return args.ll if args.ll.startswith("@") else "@" + args.ll
    if args.lat is not None and args.lng is not None:
        return f"@{args.lat},{args.lng},{args.zoom}z"
    return None


def cmd_maps(client: Client, args: argparse.Namespace) -> Dict[str, Any]:
    body: Dict[str, Any] = {"query": args.query}
    ll = _parse_ll(args)
    if ll:
        body["ll"] = ll
    for k in ("gl", "hl"):
        if getattr(args, k):
            body[k] = getattr(args, k)
    res = client.call("maps", body)
    places = []
    for p in res.get("local_results") or []:
        if not isinstance(p, dict):
            continue
        places.append({
            "position": p.get("position"), "title": p.get("title"), "place_id": p.get("place_id"),
            "rating": p.get("rating"), "reviews": p.get("reviews"), "type": p.get("type"),
            "types": p.get("types"), "address": p.get("address"), "phone": p.get("phone"),
            "website": p.get("website"), "gps": p.get("gps_coordinates"),
        })
    out: Dict[str, Any] = {"query": args.query, "ll": ll, "places": places}
    if args.name:
        pat = _brand_pattern(args.name)
        hits = [p["position"] for p in places if pat.search(p["title"] or "")]
        out["target"] = {"name": args.name, "positions": hits}
    return out


def cmd_reviews(client: Client, args: argparse.Namespace) -> Dict[str, Any]:
    body: Dict[str, Any] = {"place_id": args.place_id, "sort_by": args.sort, "num": args.num}
    res = client.call("reviews", body)
    reviews = [
        {"rating": r.get("rating"), "date": r.get("iso_date"), "text": r.get("snippet"),
         "owner_replied": bool(r.get("response")), "likes": r.get("likes")}
        for r in (res.get("reviews") or []) if isinstance(r, dict)
    ]
    return {
        "place": res.get("place_info"),
        "topics": [{"keyword": t.get("keyword"), "mentions": t.get("mentions")}
                   for t in (res.get("topics") or []) if isinstance(t, dict)],
        "reviews": reviews,
        "next_page_token": (res.get("pagination") or {}).get("next_page_token"),
    }


def cmd_grid(client: Client, args: argparse.Namespace) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "query": args.query, "center": {"lat": args.lat, "lng": args.lng},
        "grid_size": args.size, "radius_km": args.radius_km,
    }
    if args.place_id:
        body["target_place_id"] = args.place_id
    if args.name:
        body["target_name"] = args.name
    if args.gl:
        body["gl"] = args.gl
    d = client.call("grid", body).get("data") or {}
    grid = d.get("grid") or []
    n = args.size
    # Rows north to south, columns west to east; "-" = not in the results.
    rows = []
    pts = sorted(grid, key=lambda p: (-p.get("lat", 0), p.get("lng", 0)))
    for i in range(0, len(pts), n):
        rows.append(" ".join(str(p["rank"]) if p.get("found") else "-" for p in pts[i:i + n]))
    return {"query": args.query, "center": body["center"], "grid_size": n, "radius_km": args.radius_km,
            "summary": d.get("summary"), "map": rows, "points": grid}


COMMANDS = {
    "check": cmd_check, "serp": cmd_serp, "ask": cmd_ask, "visibility": cmd_visibility,
    "mentions": cmd_mentions, "maps": cmd_maps, "reviews": cmd_reviews, "grid": cmd_grid,
}


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _engines(value: str) -> List[str]:
    items = [v.strip().lower() for v in value.split(",") if v.strip()]
    bad = [v for v in items if v not in ENGINES]
    if bad or not items:
        raise argparse.ArgumentTypeError(f"engines must be from {','.join(ENGINES)}")
    return _dedupe(items)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="scavio_seo.py", description="Scavio for Claude SEO")
    p.add_argument("--version", action="version", version=VERSION)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--yes", action="store_true", help=f"confirm a call over {CONFIRM_ABOVE} credits")
    common.add_argument("--dry-run", action="store_true", help="print the planned calls, spend nothing")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("check", parents=[common], help="key and balance (free)")

    s = sub.add_parser("serp", parents=[common], help="Google SERP with AI Overview (1 credit)")
    s.add_argument("query")
    s.add_argument("--gl"); s.add_argument("--hl"); s.add_argument("--location")
    s.add_argument("--device", choices=["desktop", "mobile"])
    s.add_argument("--start", type=int, default=0)
    s.add_argument("--web-only", action="store_true")
    s.add_argument("--brand"); s.add_argument("--domain")

    a = sub.add_parser("ask", parents=[common], help="one AI engine")
    a.add_argument("engine", choices=list(ENGINES))
    a.add_argument("prompt")
    a.add_argument("--country", default="us")
    a.add_argument("--brand"); a.add_argument("--domain")

    v = sub.add_parser("visibility", parents=[common], help="one prompt on every AI engine")
    v.add_argument("prompt")
    v.add_argument("--brand"); v.add_argument("--domain")
    v.add_argument("--country", default="us")
    v.add_argument("--engines", type=_engines, default=list(DEFAULT_ENGINES))

    m = sub.add_parser("mentions", parents=[common], help="brand mentions on Reddit and YouTube")
    m.add_argument("brand")
    m.add_argument("--no-youtube", action="store_true")
    m.add_argument("--limit", type=int, default=15)
    m.add_argument("--since", default="this_year",
                   choices=["today", "this_week", "this_month", "this_year", "any"],
                   help="YouTube upload window (Reddit search has no date filter)")

    mp = sub.add_parser("maps", parents=[common], help="Google Maps search (1 credit)")
    mp.add_argument("query")
    mp.add_argument("--ll", help="@lat,lng,zoomz")
    mp.add_argument("--lat", type=float); mp.add_argument("--lng", type=float)
    mp.add_argument("--zoom", type=int, default=13)
    mp.add_argument("--gl"); mp.add_argument("--hl")
    mp.add_argument("--name", help="business to locate in the results")

    r = sub.add_parser("reviews", parents=[common], help="Google Maps reviews (1 credit)")
    r.add_argument("place_id")
    r.add_argument("--sort", default="newest", choices=["relevance", "newest", "highest_rating", "lowest_rating"])
    r.add_argument("--num", type=int, default=20)

    g = sub.add_parser("grid", parents=[common], help="local-pack geo-grid rank (size*size credits)")
    g.add_argument("query")
    g.add_argument("--lat", type=float, required=True)
    g.add_argument("--lng", type=float, required=True)
    g.add_argument("--place-id"); g.add_argument("--name")
    g.add_argument("--size", type=int, default=3, choices=[3, 5, 7])
    g.add_argument("--radius-km", type=float, default=2.0)
    g.add_argument("--gl")
    return p


def validate(args: argparse.Namespace) -> None:
    if args.command == "visibility" and not (args.brand or args.domain):
        raise ScavioError("visibility needs --brand, --domain or both", exit_code=2)
    if args.command == "grid" and not (args.place_id or args.name):
        raise ScavioError("grid needs --place-id or --name", exit_code=2)
    if args.command == "reviews" and not 1 <= args.num <= 20:
        raise ScavioError("--num must be 1-20", exit_code=2)
    if args.command == "grid" and not 0 < args.radius_km <= 100:
        raise ScavioError("--radius-km must be above 0 and at most 100", exit_code=2)


def main(argv: Optional[List[str]] = None, client: Optional[Client] = None,
         stdout=sys.stdout, stderr=sys.stderr) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as e:
        return int(e.code or 0) if e.code in (0, None) else 2
    try:
        validate(args)
        calls = plan_for(args)
        total = sum(c["credits"] for c in calls)
        plan = {"command": args.command, "calls": calls, "credits": total}
        if args.dry_run:
            print(json.dumps({"dry_run": True, **plan}, indent=2), file=stdout)
            print(f"scavio: dry run, {total} credits planned, nothing spent", file=stderr)
            return 0
        if total > CONFIRM_ABOVE and not args.yes:
            print(json.dumps({"needs_confirmation": True, **plan}, indent=2), file=stdout)
            print(f"scavio: {total} credits planned; rerun with --yes after the user confirms", file=stderr)
            return 2
        if client is None:
            client = Client(load_key())
        result = COMMANDS[args.command](client, args)
        print(json.dumps(result, indent=2, ensure_ascii=False), file=stdout)
        remaining = "" if client.credits_remaining is None else f", {client.credits_remaining} remaining"
        print(f"scavio: OK, {client.credits_used} credits used{remaining}", file=stderr)
        return 0
    except ScavioError as e:
        print(f"scavio: ERROR {e}", file=stderr)
        return e.exit_code


if __name__ == "__main__":
    sys.exit(main())
