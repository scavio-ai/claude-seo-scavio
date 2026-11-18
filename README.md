# Scavio for Claude SEO

A community integration for [Claude SEO](https://github.com/AgriciDaniel/claude-seo)
that measures AI search visibility with live data from [Scavio](https://scavio.dev).

`/seo-scavio visibility` asks ChatGPT, Gemini, Google AI Mode and Google's AI
Overview the same prompt and reports, per engine, whether the answer mentions
your brand and whether it cites your domain. `/seo-scavio mentions` finds the
brand on Reddit and YouTube, the off-site mentions that AI answers draw on and
that Claude SEO's `seo-geo` skill asks about in its Brand Mention Analysis. It
also returns live Google SERPs, Google Maps results and reviews, and a
local-pack geo-grid rank.

```
$ /seo-scavio visibility "What is the best rank tracking software for SEO agencies?" --brand AccuRanker --domain accuranker.com

engine              mentioned  cited  first mention
ChatGPT             yes        yes    "2. AccuRanker"
Google AI Mode      yes        no     "...AccuRanker leads in speed and on-demand precision..."
Gemini              yes        no     "2. AccuRanker - Best for Speed and Precision"
Google AI Overview  yes        no     "AccuRanker: Best for dedicated position-tracking specialists..."
9 credits
```

(A live run from 2026-11-18. One run is one sample: AI answers change between
runs.)

## Who maintains it

Scavio maintains this repository, and Scavio sells the API it calls. It is not
part of Claude SEO, and the Claude SEO maintainer does not audit, ship or
support it. Report problems in
[this repository's issues](https://github.com/scavio-ai/claude-seo-scavio/issues).

## Cost

Each call uses Scavio credits. The free plan includes 50 credits, with no card.
Paid plans start at $15 a month for 3,000 credits, and pay-as-you-go credits
are $0.01 each (smallest purchase 2,500 credits). Current prices:
[scavio.dev/pricing](https://scavio.dev/pricing).

| Command | Calls | Credits |
|---|---|---|
| `check` | key and balance | 0 |
| `visibility` (all four engines) | ChatGPT 2 + Google AI Mode 1 + Gemini 5 + Google AI Overview 1 | 9 |
| `ask chatgpt` / `gemini` / `aimode` / `aio` | one engine | 2 / 5 / 1 / 1 |
| `mentions` | Reddit posts 1 + Reddit comments 1 + YouTube 2 | 4 |
| `serp` | Google search | 1 |
| `maps` | Google Maps search | 1 |
| `reviews` | Google Maps reviews | 1 |
| `grid` | geo-grid, one credit per point | 9 / 25 / 49 |

Failed ChatGPT and Gemini prompts are not charged. The script will not run a
command over 5 credits without `--yes`. The skill shows you the planned calls
and the total and waits for your answer before it passes `--yes`. `--dry-run`
prints the plan and spends nothing.

The free and Hobby plans run one request at a time. `visibility` sends its
engines in parallel and runs any that are turned away one after another, so it
takes about 1 minute on a plan with parallel requests and up to about 2 on the
free plan.

## Prerequisites

- Claude Code
- Python 3.9 or later (standard library only; nothing is installed with pip)
- A Scavio API key: sign up at [dashboard.scavio.dev](https://dashboard.scavio.dev/sign-up),
  and the key is on the dashboard under API Keys
- Optional: [Claude SEO](https://github.com/AgriciDaniel/claude-seo). The skill
  also works without it.

## Install

### macOS and Linux

```bash
git clone https://github.com/scavio-ai/claude-seo-scavio
cd claude-seo-scavio
./install.sh
```

### Windows (PowerShell)

```powershell
git clone https://github.com/scavio-ai/claude-seo-scavio
cd claude-seo-scavio
.\install.ps1
```

The installer takes the key from `SCAVIO_API_KEY` when it is set, and asks for
it otherwise (the input is hidden). It then:

1. checks the key with a free call, and changes nothing if the key is rejected;
2. writes the key to `~/.config/claude-seo/scavio.json`, readable only by you
   (mode 0600 on macOS and Linux, an owner-only ACL on Windows);
3. copies `SKILL.md` and `scavio_seo.py` to `~/.claude/skills/seo-scavio/`
   (or `$CLAUDE_SKILLS_DIR/seo-scavio/`).

It does not change Claude SEO's files or dependencies, and it does not touch
`~/.claude.json` or `~/.claude/settings.json`. The key is not written to this
repository and is not printed. Pass `--no-check` (`-NoCheck` on Windows) to
skip the key check for an offline install.

Open a new Claude Code session and run `/seo-scavio check`.

## Commands

| Command | What it does |
|---|---|
| `/seo-scavio check` | Key accepted, plan and balance (free) |
| `/seo-scavio visibility "<prompt>" --brand <name> --domain <domain> [--country us]` | One prompt on ChatGPT, Google AI Mode, Gemini and Google AI Overview: mentioned, first mention, cited, cited URLs |
| `/seo-scavio ask <chatgpt\|gemini\|aimode\|aio> "<prompt>"` | One engine, with the full answer and its sources |
| `/seo-scavio mentions "<brand>" [--since this_year]` | Reddit posts and comments that name the brand, recent YouTube videos, top subreddits |
| `/seo-scavio serp "<query>" [--gl us] [--location "<city>"] [--domain <domain>]` | Google top 10, AI Overview and People Also Ask; with `--domain`, its positions and whether the AI Overview cites it |
| `/seo-scavio maps "<query>" --lat <lat> --lng <lng> [--name <business>]` | Google Maps results around a point |
| `/seo-scavio reviews <place_id>` | Newest Google reviews, review topics, whether the owner replied |
| `/seo-scavio grid "<query>" --lat <lat> --lng <lng> --place-id <id> [--size 3\|5\|7]` | Local-pack rank at every point of a grid, with a text map |

Claude also uses the skill on its own when you ask whether an AI engine
recommends or cites a brand, or where a brand is discussed on Reddit or YouTube.

`/seo-scavio` is not a subcommand of Claude SEO's `/seo`, and Claude SEO's
skills do not call it. When Claude SEO is installed, the skill hands content
changes to `seo-geo`, keyword volume and backlinks to `seo-dataforseo`, and
tracked share of voice to `seo-seranking` or `seo-profound`. For `seo-maps`
and `seo-local`, `grid`, `maps` and `reviews` provide live local data when
DataForSEO is not installed.

## What leaves your machine

`scavio_seo.py` calls only `https://api.scavio.dev`, with your key in the
`Authorization` header and `User-Agent: claude-seo-scavio/<version>`. Each
request carries the prompt, query, brand name or place ID you asked about,
and nothing else. The script sends no analytics and makes no other calls. The
endpoints it uses are listed at the top of the script.

## Security

- The skill tells the agent to treat every response as untrusted data: AI
  answers and the pages they cite, SERP titles and snippets, Reddit and YouTube
  text, business names and review text. Text in them that addresses the agent
  is reported to you, never followed.
- The key stays in `~/.config/claude-seo/scavio.json` on your machine. The
  installer passes it to Python in the environment, not on the command line.
- The skill and docs carry no tracking parameters, referral codes or affiliate
  links.

## Tests

```bash
python3 -m unittest discover -s tests     # parser and client, no network
bash tests/test_install.sh                # install/uninstall in a temporary HOME
```

The fixtures in `tests/fixtures/` are trimmed from live responses captured on
2026-11-18, with user names removed. CI runs both suites on Linux and macOS,
and `tests/test_install.ps1` on Windows.

## Uninstall

```bash
./uninstall.sh       # macOS and Linux
.\uninstall.ps1      # Windows
```

This removes `~/.claude/skills/seo-scavio/` and `~/.config/claude-seo/scavio.json`.
Other integrations' files in `~/.config/claude-seo/` are left alone.

## License

MIT
