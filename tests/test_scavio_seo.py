"""Unit tests for scavio_seo.py. Standard library only, no network.

Fixtures in tests/fixtures/ are trimmed from live Scavio responses captured
on 2026-11-18 (user names removed).
"""

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "seo-scavio"))

import scavio_seo as s  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures"
PATH_TO_FIXTURE = {path: name for name, (_, path, _, _) in s.ENDPOINTS.items()}
PATH_TO_FIXTURE["/api/v2/google"] = "google"
FAKE_KEY = "sk_test_do_not_print_0123456789"


def fixture(name):
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))


class FakeTransport:
    """Serves fixtures by path. `script` maps a path to a list of status codes
    to return before the fixture (e.g. [502] = fail once, then succeed)."""

    def __init__(self, script=None, usage=None):
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.requests = []
        self.usage = usage or {"plan": "free", "credit_balance": 50, "period_start": "2026-11-01T00:00:00Z"}

    def __call__(self, req, timeout):
        path = req.full_url.replace(s.API_BASE, "")
        body = json.loads(req.data.decode()) if req.data else None
        self.requests.append({"path": path, "body": body, "headers": dict(req.header_items()),
                              "method": req.get_method(), "timeout": timeout})
        queue = self.script.get(path)
        if queue:
            status = queue.pop(0)
            return status, {}, json.dumps({"error": f"scripted {status}"}).encode()
        if path == "/api/v1/usage":
            return 200, {}, json.dumps(self.usage).encode()
        return 200, {}, json.dumps(fixture(PATH_TO_FIXTURE[path])).encode()

    def paths(self):
        return [r["path"] for r in self.requests]


def run(argv, transport=None):
    transport = transport or FakeTransport()
    client = s.Client(FAKE_KEY, transport=transport, sleep=lambda _: None)
    out, err = io.StringIO(), io.StringIO()
    code = s.main(argv, client=client, stdout=out, stderr=err)
    text = out.getvalue()
    data = json.loads(text) if text.strip() else None
    return code, data, err.getvalue(), transport


class TestDomains(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(s.normalize_domain("https://www.SERanking.com/blog/"), "seranking.com")
        self.assertEqual(s.normalize_domain("seranking.com"), "seranking.com")
        self.assertEqual(s.normalize_domain(""), "")

    def test_displayed_link(self):
        self.assertEqual(s.domain_of("https://seranking.com › Blog › Best"), "seranking.com")

    def test_match_is_suffix_on_label_boundary(self):
        self.assertTrue(s.domain_matches("blog.seranking.com", "seranking.com"))
        self.assertFalse(s.domain_matches("notseranking.com", "seranking.com"))
        self.assertFalse(s.domain_matches("", "seranking.com"))


class TestMentions(unittest.TestCase):
    def test_word_boundary_and_case(self):
        text = "Intro.\nWe like se ranking for agencies.\nEnd."
        self.assertEqual(s.find_mention(text, ["SE Ranking"]), "We like se ranking for agencies.")
        self.assertIsNone(s.find_mention("SERankingPro is different", ["SE Ranking"]))

    def test_earliest_term_wins(self):
        text = "a\nfirst seranking.com here\nthen SE Ranking"
        self.assertEqual(s.find_mention(text, ["SE Ranking", "seranking.com"]), "first seranking.com here")

    def test_long_line_is_windowed(self):
        line = "x " * 300 + "AccuRanker" + " y" * 300
        out = s.find_mention(line, ["AccuRanker"])
        self.assertIn("AccuRanker", out)
        self.assertLess(len(out), 260)


class TestLlm(unittest.TestCase):
    def test_inline_markdown_links_count_as_citations(self):
        res = s.norm_llm({"data": {"answer": {"markdown": "Try [SE Ranking](https://seranking.com/)."},
                                   "sources": []}})
        self.assertEqual(res["inline_links"], ["https://seranking.com/"])
        brand = s.analyze(res["answer"], res["inline_links"], "SE Ranking", "seranking.com")
        self.assertTrue(brand["cited"] and brand["mentioned"])


class TestSerp(unittest.TestCase):
    def test_goto_links_use_displayed_host(self):
        serp = s.norm_serp(fixture("google"))
        goto = [r for r in serp["organic"] if r["link"] is None]
        self.assertTrue(goto, "fixture should contain google.com/goto links")
        for r in goto:
            self.assertTrue(r["domain"] and "google.com" not in r["domain"])
        self.assertEqual(s.serp_brand(serp, "seranking.com")["best_position"], 2)

    def test_serp_command_brand(self):
        code, data, err, t = run(["serp", "best rank tracking software for agencies",
                                  "--brand", "SE Ranking", "--domain", "seranking.com"])
        self.assertEqual(code, 0)
        self.assertEqual(data["brand"]["organic_positions"], [2])
        self.assertTrue(data["brand"]["ai_overview"]["mentioned"])
        self.assertTrue(data["brand"]["ai_overview"]["cited"])
        self.assertEqual(data["people_also_ask"], ["How to check search ranking?"])
        self.assertIn("1 credits used", err)


class TestVisibility(unittest.TestCase):
    ARGS = ["visibility", "What is the best rank tracking software for SEO agencies?",
            "--brand", "SE Ranking", "--domain", "seranking.com"]

    def test_needs_confirmation_and_spends_nothing(self):
        code, data, err, t = run(self.ARGS)
        self.assertEqual(code, 2)
        self.assertTrue(data["needs_confirmation"])
        self.assertEqual(data["credits"], 9)
        self.assertEqual(t.requests, [])

    def test_dry_run(self):
        code, data, err, t = run(["grid", "plumber", "--lat", "30.2", "--lng", "-97.7",
                                  "--name", "Radiant", "--size", "5", "--dry-run"])
        self.assertEqual(code, 0)
        self.assertEqual(data["credits"], 25)
        self.assertEqual(t.requests, [])

    def test_full_run(self):
        code, data, err, t = run(self.ARGS + ["--yes"])
        self.assertEqual(code, 0)
        self.assertEqual([r["engine"] for r in data["engines"]],
                         ["ChatGPT", "Google AI Mode", "Gemini", "Google AI Overview"])
        by = {r["engine"]: r for r in data["summary"]}
        self.assertTrue(all(r["mentioned"] for r in data["summary"]))
        self.assertTrue(by["ChatGPT"]["cited"])
        self.assertTrue(by["Google AI Overview"]["cited"])
        self.assertIn("9 credits used", err)
        self.assertEqual(sorted(t.paths()), sorted(["/api/v1/chatgpt/prompt", "/api/v2/google/ai-mode",
                                                    "/api/v1/gemini/prompt", "/api/v2/google"]))
        chat = next(r for r in t.requests if r["path"] == "/api/v1/chatgpt/prompt")
        self.assertEqual(chat["body"]["country"], "us")
        self.assertGreaterEqual(chat["timeout"], 90)

    def test_one_engine_fails_twice_others_report(self):
        t = FakeTransport(script={"/api/v1/gemini/prompt": [502, 502]})
        code, data, err, t = run(self.ARGS + ["--yes"], t)
        self.assertEqual(code, 0)
        gem = next(r for r in data["engines"] if r["engine"] == "Gemini")
        self.assertEqual(gem["status"], "error")
        self.assertEqual(t.paths().count("/api/v1/gemini/prompt"), 2)  # retried once
        self.assertEqual(len(data["summary"]), 3)

    def test_502_once_is_retried(self):
        t = FakeTransport(script={"/api/v1/gemini/prompt": [502]})
        code, data, err, t = run(self.ARGS + ["--yes"], t)
        self.assertEqual(code, 0)
        self.assertTrue(all(r["status"] == "ok" for r in data["engines"]))

    def test_429_reruns_sequentially(self):
        t = FakeTransport(script={"/api/v1/gemini/prompt": [429], "/api/v2/google": [429]})
        code, data, err, t = run(self.ARGS + ["--yes"], t)
        self.assertEqual(code, 0)
        self.assertTrue(all(r["status"] == "ok" for r in data["engines"]))

    def test_all_fail(self):
        t = FakeTransport(script={p: [500] for p in ("/api/v1/chatgpt/prompt", "/api/v2/google/ai-mode",
                                                     "/api/v1/gemini/prompt", "/api/v2/google")})
        code, data, err, t = run(self.ARGS + ["--yes"], t)
        self.assertEqual(code, 5)

    def test_bad_key_stops(self):
        t = FakeTransport(script={p: [401] for p in ("/api/v1/chatgpt/prompt", "/api/v2/google/ai-mode",
                                                     "/api/v1/gemini/prompt", "/api/v2/google")})
        code, data, err, t = run(self.ARGS + ["--yes"], t)
        self.assertEqual(code, 3)
        self.assertNotIn(FAKE_KEY, err)

    def test_requires_brand_or_domain(self):
        code, data, err, t = run(["visibility", "q", "--yes"])
        self.assertEqual(code, 2)

    def test_engine_subset(self):
        code, data, err, t = run(["visibility", "q", "--domain", "seranking.com", "--engines", "aimode,aio"])
        self.assertEqual(code, 0)  # 2 credits, under the confirmation threshold
        self.assertEqual(len(data["engines"]), 2)


class TestOtherCommands(unittest.TestCase):
    def test_check_is_free(self):
        code, data, err, t = run(["check"])
        self.assertEqual(code, 0)
        self.assertEqual(data["credit_balance"], 50)
        self.assertEqual(t.requests[0]["method"], "GET")

    def test_headers(self):
        code, data, err, t = run(["check"])
        h = {k.lower(): v for k, v in t.requests[0]["headers"].items()}
        self.assertEqual(h["authorization"], f"Bearer {FAKE_KEY}")
        self.assertTrue(h["user-agent"].startswith("claude-seo-scavio/"))

    def test_mentions(self):
        code, data, err, t = run(["mentions", "AccuRanker", "--limit", "3"])
        self.assertEqual(code, 0)
        self.assertEqual(t.requests[0]["body"]["query"], '"AccuRanker"')
        yt = next(r for r in t.requests if r["path"] == "/api/v1/youtube/search")
        self.assertEqual(yt["body"], {"search": "AccuRanker", "upload_date": "this_year"})
        self.assertLessEqual(len(data["reddit_posts"]), 3)
        self.assertTrue(data["youtube_videos"])
        self.assertIn("4 credits used", err)

    def test_mentions_no_youtube(self):
        code, data, err, t = run(["mentions", "AccuRanker", "--no-youtube"])
        self.assertNotIn("/api/v1/youtube/search", t.paths())
        self.assertNotIn("youtube_videos", data)

    def test_maps_target(self):
        code, data, err, t = run(["maps", "plumber", "--lat", "30.2672", "--lng", "-97.7431", "--name", "Radiant"])
        self.assertEqual(code, 0)
        self.assertEqual(t.requests[0]["body"]["ll"], "@30.2672,-97.7431,13z")
        self.assertEqual(data["target"]["positions"], [1])

    def test_reviews(self):
        code, data, err, t = run(["reviews", "ChIJgaWi-H21RIYRSO43JeIAhSY", "--num", "3"])
        self.assertEqual(code, 0)
        self.assertEqual(data["place"]["rating"], 4.8)
        self.assertTrue(data["reviews"][0]["owner_replied"])
        self.assertTrue(data["next_page_token"])

    def test_grid_map(self):
        code, data, err, t = run(["grid", "plumber", "--lat", "30.2672", "--lng", "-97.7431",
                                  "--place-id", "ChIJgaWi-H21RIYRSO43JeIAhSY", "--radius-km", "3"])
        self.assertEqual(code, 2)  # 9 credits
        code, data, err, t = run(["grid", "plumber", "--lat", "30.2672", "--lng", "-97.7431",
                                  "--place-id", "ChIJgaWi-H21RIYRSO43JeIAhSY", "--radius-km", "3", "--yes"])
        self.assertEqual(code, 0)
        # Fixture: the southern row was not found, the rest rank 1. Map rows run north to south.
        self.assertEqual(data["map"], ["1 1 1", "1 1 1", "- - -"])
        self.assertEqual(data["summary"]["points_found"], 6)

    def test_grid_requires_target(self):
        code, data, err, t = run(["grid", "plumber", "--lat", "1", "--lng", "2", "--yes"])
        self.assertEqual(code, 2)


class TestKey(unittest.TestCase):
    def test_env_first(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "scavio.json"
            cfg.write_text(json.dumps({"api_key": "from_file"}))
            self.assertEqual(s.load_key({"SCAVIO_API_KEY": "from_env"}, cfg), "from_env")
            self.assertEqual(s.load_key({}, cfg), "from_file")

    def test_missing(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(s.ScavioError) as ctx:
                s.load_key({}, Path(d) / "absent.json")
            self.assertEqual(ctx.exception.exit_code, 3)

    def test_bad_config(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "scavio.json"
            cfg.write_text("{not json")
            with self.assertRaises(s.ScavioError):
                s.load_key({}, cfg)


if __name__ == "__main__":
    unittest.main()
