<div align="center">

<img src="https://img.shields.io/badge/-🔍_AgentSearch-1f2937?style=for-the-badge" alt="AgentSearch" height="48"/>

### The search engine for AI agents.

# **Free. Local. Private. Bypasses Cloudflare.**

**One Python package. 80 websites. Zero API keys. Zero data leakage.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![Sites: 80](https://img.shields.io/badge/Sites-80-success.svg)]()
[![No API Key](https://img.shields.io/badge/No%20API%20Key-Required-success.svg)]()
[![Local Only](https://img.shields.io/badge/Data-Stays%20on%20Your%20Machine-orange.svg)]()
[![Bypasses Cloudflare](https://img.shields.io/badge/Bypasses-Cloudflare%20%2F%20PerimeterX%20%2F%20Akamai-red.svg)]()

[![GitHub Stars](https://img.shields.io/github/stars/t0ken-ai/AgentSearch?style=social)](https://github.com/t0ken-ai/AgentSearch/stargazers)
[![GitHub Forks](https://img.shields.io/github/forks/t0ken-ai/AgentSearch?style=social)](https://github.com/t0ken-ai/AgentSearch/network/members)

**[English](README.md)** · [中文](README_CN.md)

</div>

---

## ⚡ TL;DR

```bash
pip install cloakbrowser && pip install -e .

# Search any of 80 sites
agentsearch search "what's new in transformers" --engine google --json
agentsearch search "react hooks tutorial"     --engine youtube --limit 10
agentsearch search "best laptop 2025"         --engine reddit
agentsearch search "transformer attention"    --engine arxiv

# One-shot: SERP + auto-extract markdown body of the top 3 hits
agentsearch search "MCP web search" --engine hackernews --limit 5 --depth 3 --json

# Multi-engine fan-out (parallel + URL-deduped)
agentsearch search-many "AgentSearch project" --engines google,reddit,hackernews,arxiv --merged

# Preset bundles for common agent tasks
agentsearch jobs    "data engineer"            # linkedin_jobs + indeed + ziprecruiter + glassdoor
agentsearch travel  "kyoto"                    # booking + expedia
agentsearch news    "fed rate decision"        # reuters + ap + bbc + guardian + npr
agentsearch code    "kubernetes ingress"       # github + stackoverflow + HN
agentsearch research "diffusion transformer"   # ddg + google + reddit + HN

# Login-walled sites: log in once, reuse the session forever
agentsearch login twitter
agentsearch search "from:openai" --engine twitter --profile twitter --limit 20

# Extract a URL as clean Markdown
agentsearch extract "https://news.ycombinator.com/item?id=43936992" --json

# Run as an MCP server for Cursor / Cline / Claude Desktop / OpenClaw / Continue
python -m agent_search.mcp_server

# Or as a self-hosted HTTP API for cloud / Docker agents
python -m agent_search.serve --port 8088
```

80 stealth sites · CLI · MCP server · HTTP API · runs entirely on your machine. **Bypasses Cloudflare, PerimeterX, Akamai, DataDome, and every fingerprint test we know of.**

---

## 🛡️ The CloakBrowser Edge — Why This Works When Others Don't

> Stock Selenium / Puppeteer / Playwright get **blocked instantly** on Cloudflare-protected sites, Amazon, Google CAPTCHA, Reddit, Twitter, and basically every site that matters. AgentSearch doesn't, because it's built on **[CloakBrowser](https://github.com/CloakHQ/CloakBrowser)** — an open-source Chromium fork with **49 C++ source-level fingerprint patches**.
>
> **This is not a JS injection. Not a config tweak. Not a UA spoof.** It's a real Chromium binary, modified at the C++ source level so anti-bot systems cannot tell the difference between AgentSearch and a real human browser — *because there is no difference*.

### What it transparently bypasses

| Layer | System | Status |
|:------|:-------|:------:|
| 🛡️ WAF / CDN | **Cloudflare Turnstile / Bot Fight Mode** | ✅ |
| 🛡️ WAF / CDN | **PerimeterX / HUMAN Security** | ✅ |
| 🛡️ WAF / CDN | **Akamai Bot Manager** | ✅ |
| 🛡️ WAF / CDN | **DataDome** | ✅ |
| 🛡️ WAF / CDN | **Imperva / Incapsula** | ✅ |
| 🔬 Fingerprint test | **bot.sannysoft.com** | ✅ All checks |
| 🔬 Fingerprint test | **CreepJS** (abrahamjuliot) | ✅ |
| 🔬 Fingerprint test | **PixelScan** | ✅ |
| 🔬 Fingerprint test | **BrowserLeaks** | ✅ |
| 🔬 Fingerprint test | **fingerprint.com** | ✅ |
| 🤖 reCAPTCHA v3 | Score | **≥ 0.7** |

> **Why it matters for agents:** an AI agent running a "free" web search tool that gets blocked by Cloudflare 30% of the time is broken. AgentSearch routinely runs hundreds of consecutive queries on Cloudflare-protected sites without a single block.

---

## 💡 Why AgentSearch (vs the alternatives)

The web search landscape for AI agents in 2026 is unpleasant. Hosted APIs are getting **more expensive while LLMs get cheaper** ([HN discussion](https://news.ycombinator.com/item?id=43921238)), and many of them now explicitly **prohibit AI-agent use in their ToS** ([Brave Search API](https://news.ycombinator.com/item?id=46822822)). Browser scrapers without stealth get blocked by Cloudflare instantly. AgentSearch is the alternative.

|                                          | **AgentSearch** | Tavily / Serper | Firecrawl-MCP<br>(6.3k⭐) | Exa-MCP<br>(4.5k⭐) | Brave Search API | DDG-MCP<br>(1.2k⭐) | SearXNG | Selenium /<br>Puppeteer |
|:-----------------------------------------|:-----------:|:---------------:|:------------------------:|:------------------:|:----------------:|:-------------------:|:-------:|:----------------------:|
| 💰 **Price**                             | **Free** | Tavily $30+/mo, Serper $50+/mo | $19+/mo | $10+/mo | **$9 / 1k queries** | Free | Free | Free |
| 🔑 **API key required**                  | **No** | Yes | Yes | Yes | Yes | No | No | No |
| 🚦 **Rate limit**                        | **None** | 1k/mo free → paid | 500/mo free → paid | 1k/mo free → paid | 2k/mo @ 1 TPS | None (DDG-side) | None | None |
| ⚖️ **TOS allows AI use**                 | ✅ Yes | ✅ | ✅ | ✅ | ❌ **Forbidden** | ✅ | ✅ | ✅ |
| 🔌 **MCP server included**               | ✅ Yes | ⚠️ Third-party | ✅ Official | ✅ Official | ⚠️ Third-party | ✅ | ❌ | ❌ |
| 🌍 **Sites supported**                   | **80** | 1 (web index) | 1 (web index) | 1 (neural index) | 1 (web index) | 1 | ~10 SE aggregator | DIY |
| 🛡️ **Bypasses Cloudflare**               | ✅ **C++ patches** | N/A (uses APIs) | N/A | N/A | N/A | ❌ | ❌ HTTP-only | ❌ Detected instantly |
| 🐍 **JS rendering**                      | ✅ Full Chromium | ❌ API-only | ✅ | ❌ | ❌ | ❌ | ❌ | ✅ |
| 🌐 **Data leaves your machine**          | **Never** | Always | Always | Always | Always | Always | Depends | Never |
| 📰 **Engine-specific fields**            | ✅ IMDB rating, arXiv ID,<br>YouTube views, Reddit score… | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | DIY |

> **For agent builders specifically**: Brave Search's TOS explicitly forbids using results "for AI inference" — so every Cursor/Cline/Claude/OpenClaw/Continue user wired to brave-search-mcp is technically in violation. AgentSearch sidesteps this entirely: we don't call any third-party API. Each user's Chromium hits Google/Bing/Reddit/etc. directly from their own machine, the same way their browser does.

---

## 🌍 The 80 Sites — Categorized

> Every site is implemented as a self-contained adapter (`agent_search/engines/<name>.py`) with a runnable test (`tests/test_<name>.py`).

<table>
<tr>
<td valign="top" width="50%">

**🔍 Search Engines (11)**
Google · Bing · DuckDuckGo · Yandex · Brave · Baidu · Sogou · 360 Search · Startpage · Ecosia · Qwant

**💻 Code & Dev (5)**
GitHub · StackOverflow · Hacker News · NPM · dev.to

**🤖 AI & Research (3)**
HuggingFace · arXiv · Semantic Scholar

**📚 Knowledge (5)**
Wikipedia · Wikivoyage · PubMed · Wolfram Alpha · MDN Web Docs

**💬 Social & Forum (7)**
Reddit · Reddit Subreddit (JSON) · Twitter/X · LinkedIn · Quora · BlackHatWorld · Instagram

**🇨🇳 Chinese Platforms (6)**
Zhihu · Weibo · Xiaohongshu · Douyin · Toutiao · Bilibili

**🌍 Western News (10)**
BBC · The Guardian · Reuters · AP News · CNN · NPR · Al Jazeera · TechCrunch · The Verge · Ars Technica

**🎬 Video & Streaming (4)**
YouTube · Twitch · Netflix · TikTok

**🎵 Music, Audio & Podcasts (4)**
Spotify · SoundCloud · Apple Podcasts · Xiaoyuzhou FM

</td>
<td valign="top" width="50%">

**🎥 Movies & Books (2)**
IMDB · Goodreads

**📰 News & Content (2)**
Medium · Product Hunt

**🛒 E-commerce & Shopping (4)**
Amazon · eBay · Icecat · Steam

**💼 Jobs & Local (5)**
LinkedIn Jobs · Indeed · Yelp · ZipRecruiter · Glassdoor

**🗺️ Maps & Travel (3)**
Google Maps · Booking · Expedia

**💰 Finance (1)**
Yahoo Finance

**📜 Patents & Security (2)**
Google Patents · VirusTotal

**📦 Archive & Files (2)**
Internet Archive · 1337x

**🖼️ Images (4)**
Unsplash · Pixabay · Pexels · Pinterest

</td>
</tr>
</table>

> *And growing — new adapters are added continuously. Run `agentsearch list-engines` to see the live count.*

---

## 🚀 Quick Start

### 1. Install

```bash
pip install cloakbrowser
git clone https://github.com/t0ken-ai/AgentSearch.git
cd AgentSearch
pip install -e .
```

### 2. Use it from the CLI

```bash
# General web search
agentsearch search "latest AI news" --engine google

# Code lookup on StackOverflow
agentsearch search "TypeError pandas groupby" --engine stackoverflow

# Latest research papers
agentsearch search "transformer scaling laws" --engine arxiv

# Reddit discussion
agentsearch search "best linux laptop 2025" --engine reddit

# Video tutorials
agentsearch search "react hooks" --engine youtube --limit 10

# Shopping
agentsearch search "mechanical keyboard" --engine amazon

# Chinese platform
agentsearch search "机器学习" --engine zhihu

# JSON output for piping into other tools
agentsearch search "open source" --engine github --json | jq .

# List every available engine
agentsearch list-engines
```

### 3. Or use it from Python

```python
from agent_search.core import launch, BrowserConfig, new_page
from agent_search.engines.google import GoogleEngine

browser = launch(BrowserConfig(headless=True, humanize=True))
try:
    page = new_page(browser)
    results = GoogleEngine(page).search("open source AI models", limit=5)
    for r in results:
        print(f"{r.title}\n  {r.url}\n  {r.snippet[:120]}\n")
finally:
    browser.close()
```

### 4. Or install as an OpenClaw skill

```bash
cp -r skills/agent-search ~/.openclaw/workspace/skills/
```

Now your OpenClaw / Codex / Kiro agent natively knows how to search 80 sites — no plumbing, no prompts.

---

## 🔌 Use as an MCP Server (Cursor / Cline / Claude Desktop / Continue / Roo Code)

AgentSearch ships with a **Model Context Protocol** server, so any MCP-compatible client gets `search` / `extract` / `list_engines` tools out of the box — no glue code, no API keys.

### Install

```bash
pip install -e ".[mcp]"      # adds the `mcp` Python SDK
```

### Configure your client

<details open>
<summary><b>Claude Desktop</b> · <code>~/Library/Application Support/Claude/claude_desktop_config.json</code></summary>

```json
{
  "mcpServers": {
    "agent-search": {
      "command": "/path/to/venv/bin/python",
      "args": ["-m", "agent_search.mcp_server"]
    }
  }
}
```
</details>

<details>
<summary><b>Cursor</b> · <code>.cursor/mcp.json</code> in your repo (or <code>~/.cursor/mcp.json</code> globally)</summary>

```json
{
  "mcpServers": {
    "agent-search": {
      "command": "/path/to/venv/bin/python",
      "args": ["-m", "agent_search.mcp_server"]
    }
  }
}
```
</details>

<details>
<summary><b>Cline / Continue / Roo Code</b> · their MCP settings UI</summary>

Same shape — point ``command`` at the venv's Python and ``args`` at ``-m agent_search.mcp_server``. The exact config file path varies; consult each client's docs.
</details>

<details>
<summary><b>OpenClaw</b> · already supported via the bundled <code>agent-search</code> skill</summary>

```bash
cp -r skills/agent-search ~/.openclaw/workspace/skills/
```

OpenClaw will auto-load the skill and the agent will reach for AgentSearch whenever a query needs live web data.
</details>

### What your agent gets

| Tool | What it does | When to call |
|---|---|---|
| ``search(query, engine, limit)`` | Run one of 80 search engines | Any time you need fresh web hits |
| ``extract(url, paginate, max_scrolls)`` | Fetch a URL, return Markdown + metadata | After ``search`` returns a hit you want to read |
| ``list_engines()`` | Enumerate engines + categories | When you're not sure which engine to use |

The server keeps a single Chromium alive across calls and recycles it every 25 calls (configurable via ``AGENTSEARCH_RECYCLE_AFTER``), so each tool call after the first costs <100ms of overhead instead of the full ~1.5s startup.

---

## 🌐 Self-hosted HTTP API (`agentsearch.serve`)

When MCP isn't available — cloud workers, Docker containers, scripts on remote machines, custom HTTP-only frameworks — run AgentSearch as a tiny HTTP server. Same engine pool, same stealth, simple JSON API.

```bash
# Localhost only (no auth needed):
python -m agent_search.serve --port 8088

# Bound to the network (auth required):
AGENTSEARCH_TOKEN=mysecret python -m agent_search.serve --host 0.0.0.0 --port 8088
```

Endpoints:

| Method · path | Body / params | Returns |
|---|---|---|
| `GET  /health` | — | `{"status": "ok"}` |
| `GET  /list-engines` | — | `{count, engines[]}` |
| `POST /search` | `{query, engine?, limit?, depth?, profile?}` | array of results |
| `POST /search-many` | `{query, engines[], limit?, timeout?}` | per-engine + merged feed |
| `POST /extract` | `{url, paginate?, max_scrolls?, links?, images?, profile?}` | extracted markdown |

```bash
# Quick examples
curl localhost:8088/health
curl -X POST -H 'Content-Type: application/json' \
     -d '{"query":"transformer","engine":"arxiv","limit":3}' \
     localhost:8088/search
```

> **Why single-threaded?** CloakBrowser uses Playwright's *sync* API, which binds each browser to its launching thread. A multi-threaded server would cross-thread the Browser. The self-hosted single-user use case doesn't need concurrency anyway. Concurrent agents → run multiple instances on different ports.
>
> **Network safety**: the server refuses to bind `0.0.0.0` without a bearer token to prevent accidental network exposure.

---

## 🧪 Quality monitoring — nightly canary

Every adapter depends on the live DOM of its target site. Sites change every day, so we built `tests/nightly_canary.py` to detect regressions automatically:

- Runs one canary search through every adapter in parallel
- Classifies each as **PASS** (≥1 result), **EMPTY** (clean run, no hits), or **FAIL** (exception)
- Writes `canary_report.json` for downstream tooling
- Exits non-zero when more than 20% of engines are unhealthy

The companion GitHub Action (`.github/workflows/nightly-canary.yml`) runs this **every day at 06:00 UTC** and **auto-files a GitHub issue** (label: `canary-regression`) when the threshold trips. If an issue already exists, it appends a comment instead of creating duplicates.

Run locally:

```bash
# Full sweep (~5 min, 80 engines, parallel=4)
python tests/nightly_canary.py

# Targeted subset
python tests/nightly_canary.py --engines duckduckgo,reddit,arxiv

# Tighten/loosen the regression threshold
python tests/nightly_canary.py --fail-threshold 0.10
```

Trigger manually on GitHub: **repo → Actions → nightly-canary → Run workflow**.

---

## 🍳 Cookbook — Common Recipes

<details>
<summary><b>🔐 Log into a site once, reuse the session forever</b></summary>

Sites like Twitter/X, LinkedIn, Glassdoor, Discord, and Instagram return
nothing useful when accessed anonymously. Run `agentsearch login` once,
log in via the headed CloakBrowser window, and every subsequent search
or extract picks up the session — *without* losing stealth (the
CloakBrowser C++ patches still apply, unlike Chrome-CDP-based tools
that use the user's vanilla Chrome).

```bash
# Open a headed window, log in interactively, press Enter to save:
agentsearch login twitter
agentsearch login linkedin
agentsearch login glassdoor

# Use the persisted profile in any follow-up call:
agentsearch search "from:elonmusk AI" --engine twitter --profile twitter --limit 10
agentsearch extract "https://www.linkedin.com/in/<handle>/" --profile linkedin --json

# Custom site? Override the login URL:
agentsearch login mysite --url https://mysite.com/auth/signin
```

Profiles live in `~/.cache/agentsearch/profiles/<name>/` (override with
`AGENTSEARCH_PROFILES_DIR`). `--profile <name>` defaults to the site's
own name when you ran `login`. Profiles preserve cookies, localStorage,
IndexedDB, and service workers — the same shape as a real Chrome
profile.
</details>

<details>
<summary><b>📰 Extract a URL as clean Markdown (readability + auto-pagination)</b></summary>

```bash
# Get the title, author, date, and full article body — paginates lazy content,
# strips ads/nav/chrome, returns Markdown ready for an LLM context.
agentsearch extract \
  "https://news.ycombinator.com/item?id=43936992" --json | jq .

# Returns:
# {
#   "url": "...",
#   "status": "ok",
#   "title": "Updated rate limits for unauthenticated requests",
#   "author": "...",
#   "date": "2025-05-09",
#   "content_markdown": "...",          # full thread, ~7900 words
#   "content_text": "...",
#   "word_count": 7936,
#   "extractor": "trafilatura",
#   "scrolls": 1,
#   "load_more_clicks": 0
# }

# Skip auto-scroll for fast static pages
agentsearch extract "https://example.com/blog" --json --no-paginate

# Pretty-print as Markdown to stdout
agentsearch extract "https://example.com/blog" --format markdown
```
</details>

<details>
<summary><b>📚 Research a topic across multiple sources</b></summary>

```bash
# Fan out across complementary engines and merge
agentsearch search "X" --engine google     --limit 5 --json > /tmp/g.json
agentsearch search "X" --engine reddit     --limit 5 --json > /tmp/r.json
agentsearch search "X" --engine arxiv      --limit 3 --json > /tmp/a.json
agentsearch search "X" --engine hackernews --limit 5 --json > /tmp/h.json
```
</details>

<details>
<summary><b>🛒 Compare prices across e-commerce sites</b></summary>

```bash
agentsearch search "AirPods Pro 2" --engine amazon --json
agentsearch search "AirPods Pro 2" --engine ebay   --json
```
</details>

<details>
<summary><b>🎬 Look up a movie + its book + its podcast</b></summary>

```bash
agentsearch search "Dune"        --engine imdb       --json
agentsearch search "Dune"        --engine goodreads  --json
agentsearch search "Frank Herbert" --engine apple_podcasts --json
```
</details>

<details>
<summary><b>🇨🇳 Search Chinese platforms (auto-fallback to Google site:)</b></summary>

```bash
# These adapters ship with a transparent Google → Bing → DDG fallback for
# their walled SERPs — no auth, no cookies, just works.
agentsearch search "旅行攻略" --engine xiaohongshu
agentsearch search "美食"     --engine douyin
agentsearch search "科技"     --engine weibo
```
</details>

<details>
<summary><b>🤖 Find AI models / datasets on HuggingFace</b></summary>

```bash
agentsearch search "llama" --engine huggingface --json
# Returns model_id, author, downloads, likes, pipeline_tag, library, tags
```
</details>

<details>
<summary><b>⚡ Multi-engine fan-out (parallel + URL-deduped)</b></summary>

```bash
# Run 3-5 engines concurrently. Wall-clock ≈ slowest engine, not sum.
# URLs surfaced by multiple engines float to the top of the merged feed.
agentsearch search-many "open source MCP web search" \
    --engines duckduckgo,hackernews,github --limit 5 --merged --json

# Or use a preset bundle (built-in shortcuts):
agentsearch jobs    "data engineer"             # linkedin_jobs+indeed+ziprecruiter+glassdoor
agentsearch travel  "kyoto"                     # booking + expedia
agentsearch news    "fed rate decision"         # reuters+ap+bbc+guardian+npr
agentsearch code    "kubernetes ingress yaml"   # github+stackoverflow+HN
agentsearch research "diffusion transformer"    # ddg+google+reddit+HN
```
</details>

<details>
<summary><b>📰 One-shot SERP + body (--depth N)</b></summary>

```bash
# Top N results come back with body_markdown / body_word_count already
# attached — no follow-up extract calls needed.
agentsearch search "Brave Search API forbids AI" \
    --engine hackernews --limit 5 --depth 3 --json
```
</details>

<details>
<summary><b>🚦 Health-aware fallback (--fallback)</b></summary>

```bash
# Try the chosen engine; if it returns empty / errors, walk down a
# chain ranked by recent success rate. Engine health is recorded in
# ~/.cache/agentsearch/health.json across calls.
agentsearch search "X" --engine google --fallback --json

# Custom chain:
agentsearch search "X" --engine google \
    --fallback --fallback-chain duckduckgo,bing,startpage --json

# Inspect the local health table (sorted by composite score):
agentsearch status
```
</details>

<details>
<summary><b>💼 Compare jobs across LinkedIn / Indeed / ZipRecruiter / Glassdoor</b></summary>

```bash
agentsearch jobs "site reliability engineer in Berlin" --limit 5 --json | jq .
# Top of the merged feed = consensus picks (URL surfaced by multiple boards)
```
</details>

<details>
<summary><b>🗺️ Local business search via Google Maps</b></summary>

```bash
agentsearch search "ramen tokyo" --engine google_maps --limit 5 --json
# Returns name, url, rating, review_count, address, category, phone, website
```
</details>

<details>
<summary><b>📈 Quick ticker lookup via Yahoo Finance</b></summary>

```bash
agentsearch search "apple" --engine yahoo_finance --limit 3 --json
# Returns symbol, name, last_price, exchange, asset_type
```
</details>

---

## 🔒 Privacy & Security

```
┌─ What runs locally ────────────────────────────────────────────┐
│                                                                │
│  ✅ Browser instance (CloakBrowser/Chromium on YOUR machine)    │
│  ✅ Search queries                                              │
│  ✅ Result parsing                                              │
│  ✅ All data processing                                         │
│                                                                │
└────────────────────────────────────────────────────────────────┘

┌─ What goes to the internet ────────────────────────────────────┐
│                                                                │
│  🔍 Only the direct request to the target website              │
│     (e.g., google.com/search?q=...)                            │
│  ❌ No middleware                                               │
│  ❌ No proxy                                                    │
│  ❌ No analytics                                                │
│  ❌ No telemetry                                                │
│                                                                │
└────────────────────────────────────────────────────────────────┘

┌─ What never happens ───────────────────────────────────────────┐
│                                                                │
│  ❌ Queries sent to any third-party API                         │
│  ❌ Usage tracking or analytics                                 │
│  ❌ Cloud storage of search history                             │
│  ❌ API keys stored or transmitted                              │
│  ❌ Data collection of any kind                                 │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

**Your searches are yours. Period.**

---

## 🏗️ Architecture

```
AgentSearch/
├── agent_search/
│   ├── core.py               ← Browser launch & config
│   ├── cli.py                ← Command-line interface
│   ├── engines/              ← 60+ site adapters (one file per site)
│   │   ├── base.py           ← BaseEngine + SearchResult dataclass
│   │   ├── google.py         ← Google with consent / sorry / CAPTCHA handling
│   │   ├── youtube.py        ← YouTube with view/duration/upload-date parsing
│   │   ├── arxiv.py          ← arXiv via Atom API
│   │   ├── huggingface.py    ← HuggingFace Hub via REST API
│   │   ├── douyin.py         ← Walled site → Google/Bing/DDG site: fallback chain
│   │   └── ...               ← 55 more
│   ├── stealth/
│   │   └── enhance.py        ← Anti-detection JS layer on top of CloakBrowser
│   └── tests/                ← Standalone runnable test per engine
├── skills/agent-search/
│   └── SKILL.md              ← OpenClaw / Codex / Kiro skill metadata
├── README.md  /  README_CN.md
├── LEGAL.md  /  LICENSE
└── pyproject.toml
```

### Adding a new site adapter

```python
from .base import BaseEngine, SearchResult

class MySiteEngine(BaseEngine):
    name = "mysite"

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        self.page.goto(f"https://mysite.com/search?q={query}")
        # parse the DOM, return list[SearchResult]
        ...
```

That's it. `BaseEngine` already handles retries, blocked-page detection, and human-like timing.

---

## 🙏 Acknowledgments

Built on top of **[CloakBrowser](https://github.com/CloakHQ/CloakBrowser)** — an open-source stealth Chromium that modifies fingerprint signals at the **C++ source level** (49 patches across V8, Blink, and content shell). This project would be impossible without their work on browser-level anti-detection.

> *"CloakBrowser — Stealth Chromium that passes every bot detection test. Not a patched config. Not a JS injection. A real Chromium binary with fingerprints modified at the C++ source level."*
>
> — [github.com/CloakHQ/CloakBrowser](https://github.com/CloakHQ/CloakBrowser) · MIT License

---

## ⚖️ Disclaimer

This project is provided for **educational research and personal-use purposes only**. The authors are **not responsible** for any misuse.

| | |
|---|---|
| ✅ DO | Respect `robots.txt`, rate limits, and Terms of Service |
| ✅ DO | Use this for legitimate research, learning, personal use |
| ❌ DON'T | Violate laws or regulations in your jurisdiction |
| ❌ DON'T | Scrape sites that explicitly prohibit automation in their ToS |
| ❌ DON'T | Use for unauthorized data collection or privacy violation |

By using this software you agree to bear full responsibility for your actions. See [LEGAL.md](LEGAL.md) for full notices.

---

## 📜 License

**MIT** — use it however you want. Just don't blame us if you somehow get blocked. (You won't.)

---

## 🤝 Contributing

PRs welcome — especially for:

- 🆕 New site adapters (browse `agent_search/engines/` for examples)
- 🐛 Bug fixes for existing adapters
- 🎯 Improved anti-detection techniques
- 🌐 Documentation and translations

PRs are reviewed promptly. Open an issue first if your change is large.

---

<div align="center">

**One skill. The whole web. Local. Free. Bypasses Cloudflare.**

[![GitHub](https://img.shields.io/badge/GitHub-t0ken--ai%2FAgentSearch-181717?logo=github)](https://github.com/t0ken-ai/AgentSearch)

</div>
