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

**📣 Ad Intelligence (5) + App Store search** 🆕
Meta Ad Library · Instagram Ad Library · Google Ads Transparency · TikTok Creative Center · TikTok Ad Library · Apple App Store · Google Play

**📚 Developer Docs (142 platforms)** 🆕
Meta / Facebook · Stripe · OpenAI · Anthropic · AWS · Google Cloud · Azure · GitHub · Kubernetes · Docker · React · Vue · Next.js · Python · Node · Rust · Go · MDN · Apple · Android · Flutter · MongoDB · Redis · Postgres · HuggingFace · Datadog · Sentry · Notion · Slack · Discord · Twilio · Shopify · Vercel · Supabase · …

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

## 🧪 Quality monitoring — local canary

Every adapter depends on the live DOM of its target site. Sites change every day, so we built `agentsearch canary` to detect regressions automatically.

**Run it locally, not on CI.** GitHub Actions runners use Azure datacenter IPs that Reddit / Cloudflare / DataDome already pre-block, so a CI canary produces noise, not signal. The recommended pattern is a daily launchd / systemd-timer / cron job on the user's own machine that calls:

```bash
agentsearch canary --gh-issue
```

The canary:

- Runs one canary search through every adapter in parallel
- Classifies each as **PASS** (≥1 hit), **EMPTY** (clean run, 0 hits = likely DOM drift), or **FAIL** (exception)
- Writes `canary_report.json` for downstream tooling
- When `(EMPTY + FAIL) / total > 20%`, opens (or comments on) a GitHub issue tagged `canary-regression` via the `gh` CLI

```bash
# Full sweep (~5 min, 80 engines, parallel=4)
agentsearch canary

# Targeted subset
agentsearch canary --engines duckduckgo,reddit,arxiv

# File a GitHub issue automatically when threshold trips
agentsearch canary --gh-issue

# No `gh` CLI? Generate a markdown body to paste manually:
agentsearch canary --issue-md /tmp/canary-issue.md
```

See [`docs/CANARY.md`](docs/CANARY.md) for ready-made `launchd` / `systemd` / `cron` templates and the [`skills/agentsearch-canary/`](skills/agentsearch-canary/) OpenClaw skill that does the daily run for you.

> The repo also keeps a manual-dispatch workflow (`.github/workflows/canary-on-demand.yml`) as a "click button to double-check" fallback. It's not on a schedule — that's deliberate.

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

<details>
<summary><b>🌐 Route through HTTP / SOCKS proxies (rotate when rate-limited)</b></summary>

When your home IP gets throttled by Instagram / YouTube / Reddit, switch
to proxies. Supports HTTP / HTTPS / SOCKS4 / SOCKS5 with auth, rotation
strategies, and on-disk caching.

```bash
# 1. Pull a free list from GitHub (proxifly / roosterkid / TheSpeedX / Zaeem20)
agentsearch proxies fetch --sources socks5 --limit 200    # bundle: socks5
agentsearch proxies fetch --sources proxifly_http --limit 100  # one source

# 2. Health-check the cached pool (HTTP/HTTPS only — SOCKS verified at use)
agentsearch proxies test --workers 50 --timeout 8 --max-test 200

# 3. Inspect the pool
agentsearch proxies list --limit 30

# 4. Use a single static proxy
agentsearch search "rate-limited query" --engine google \
  --proxy http://user:pass@1.2.3.4:8080

# 5. Rotate from the pool (each invocation picks one via the pool's strategy)
agentsearch search "..." --engine instagram --proxy pool          # any scheme
agentsearch search "..." --engine youtube  --proxy pool:socks5    # filter
agentsearch search "..." --engine reddit   --proxy pool:/path/list.json
agentsearch search "..." --engine google   --proxy file:/path/proxies.txt

# 6. Add a paid residential proxy by hand (recommended for production)
agentsearch proxies add http://user:pw@proxy.webshare.io:80 \
                       socks5://user:pw@gate.bright.com:33335
```

> **Note:** Free public proxies have very low hit rates (most listed are
> dead within minutes). For serious automation buy a residential pool from
> Webshare / Bright Data / Oxylabs / IPRoyal — the same `--proxy` /
> `proxies add` API works, just with much higher uptime.

</details>

<details>
<summary><b>📣 Ad Intelligence — research competitor creatives across Meta / Instagram / Google / TikTok, plus App Store search & contact harvest</b></summary>

The five ad-library engines + App Store search turn AgentSearch into
a self-hosted competitor of BigSpy / AdSpy / SocialPeta /
data.ai — same data, no $99-499/mo subscription, results in standard
JSON ready for an agent to consume.

#### Single-engine ad search

```bash
# 1. Meta Ad Library — keyword / advertiser / page-URL search across FB + IG
agentsearch search "shopify" --engine meta_ads --limit 20 --json
# Returns per ad: ad_archive_id, page_name, days_running, image_urls[],
#                 video_urls[], body_text, cta_text, link_url,
#                 collation_id, currency, reach, funding_entity,
#                 disclaimer, page_like_count, age_gender_distribution,
#                 region_distribution, ... (60+ fields)

# 2. Instagram-only — same backend, locked to publisher_platforms=instagram
agentsearch search "sephora" --engine ig_ads --limit 5 --json

# 3. TikTok Creative Center — 19 modes (Top Ads, Keyword Insights,
#    Top Products, Trending Hashtags, Hashtag Analytics, Trending Songs,
#    Song Analytics, Trending Creators, Trending Videos, …)
agentsearch search "" --engine tt_ads --limit 10 --json
# Filters: --period 7|30|180  --country_code US|GB|JP  --order_by ctr|like|cvr
#          --industry <id>   --objective <id>   --keyword <kw>

# 4. Google Ads Transparency — search/domain/advertiser_ads/creative_detail
agentsearch search "nike.com" --engine g_ads --mode domain --json
# → resolves to the advertiser_id directly, then list their ads:
agentsearch search "AR01266454498310619137" --engine g_ads \
    --mode advertiser_ads --region anywhere --limit 20

# 5. TikTok Ad Library — EU/UK only (DSA-mandated)
agentsearch search "burger king" --engine tiktok_ads --region GB --limit 10
```

#### Cross-platform top-level commands

```bash
# 6. `ads` — fan out across all four ad libraries with one query
agentsearch ads "summer skincare" --limit 10
#   meta + instagram + tiktok + google in parallel, merged + sorted by
#   recency, normalized to a uniform AdRecord schema.

# 6a. Filter on the fly — only keep video ads with high impression upper
agentsearch ads "summer skincare" \
    -f has_video=true \
    -f min_impressions=10000 \
    -f country=US \
    -f last_seen_after=2026-04-01

# 7. `ads-by-app` — App Store URL → competitor's ads on every platform
agentsearch ads-by-app "https://apps.apple.com/us/app/instagram/id389801252" \
    --platform all --precise --limit 20
#   → resolves the app to a developer name + website domain
#   → Google ATC: `mode=domain` (exact match)
#   → Meta/IG:    `--precise` resolves dev name to canonical
#                 Facebook page_id, then advertiser-mode query (no
#                 keyword bleed-through)
#   → TikTok CC:  Top Ads filtered by dev name

# 8. `ads-batch` — weekly competitor sweep over a list of apps
cat > competitors.txt <<EOF
com.shopify.mobile
https://apps.apple.com/us/app/instagram/id389801252
com.spotify.music
EOF

agentsearch ads-batch competitors.txt -o ./weekly-sweep \
    --platform all --precise --limit 20 \
    --workers 4 --proxy-pool ./fluxisp-pool.txt
#   → 4 apps in parallel through 4 distinct residential IPs (each line
#     of fluxisp-pool.txt is a proxy URL; mismatched workers / pool
#     sizes get a warning).
#   → Output: ./weekly-sweep/<bundle_slug>.json per app + index.json
#     with per-platform ad counts, app metadata, ratings, versions,
#     last_updated dates.

# 9. `ads-download` — pull every image / video URL from a JSON dump
cat weekly-sweep/com_shopify_mobile.json | jq -c '.results[]' \
    | agentsearch ads-download - -o ./swipe --max-per-record 1
#   → highest-resolution video / image per ad, named
#     {platform}_{ad_id}_{idx}_{kind}.{ext}, served through the same
#     proxy that found them.
```

#### App Store keyword search & developer contact harvest

```bash
# 10. `app-search` — keyword search across Apple App Store + Google Play
agentsearch app-search "shopify" --store all -n 6
# →
#   [apple ] Shopify: Sell online/in person   by Shopify Inc.   id=371294472
#            web=http://www.shopify.com/mobile
#   [google] Shopify: Sell online/in person   by Shopify        id=com.shopify.mobile
#            web=http://www.shopify.com/mobile
#            email=support@shopify.com
#            privacy=http://www.shopify.com/legal/privacy

# Each result carries 25 fields including support_email, privacy_url,
# website, terms_url, version, last_updated, rating, rating_count,
# size_bytes, supported_devices, languages, genres, screenshot_urls.

# 10a. Only keep apps with public contact info (BD outreach / compliance)
agentsearch app-search "fitness tracker" --store all -n 50 \
    --with-contact --json > apps.json
```

#### End-to-end pipeline (keyword → competitors → ads → swipe files)

```bash
# Find every "fitness" app on both stores, then fan out to every ad
# library, then pull all the creative bytes — one shell pipeline.

agentsearch app-search "fitness" --store all -n 20 --json \
    | jq -r '.results[].bundle_id' \
    > /tmp/bundles.txt

agentsearch ads-batch /tmp/bundles.txt -o ./fitness-intel \
    --workers 4 --proxy-pool ./fluxisp-pool.txt --precise

for f in fitness-intel/*.json; do
  cat "$f" | jq -c '.results[]' \
    | agentsearch ads-download - -o "./swipe/$(basename $f .json)" \
        --max-per-record 1 --quiet
done
```

#### What you can extract per ad

| Field | Meta / Instagram | TikTok CC | Google ATC |
|---|---|---|---|
| `ad_id` / `creative_id` | ✅ | ✅ | ✅ |
| `advertiser_name` / `page_name` | ✅ | ✅ | ✅ (via domain) |
| `first_seen` / `last_seen` / `days_running` | ✅ (political/EU) | ⚠️ via period | ✅ |
| `image_urls[]` / `video_urls[]` | ✅ (all carousel) | ✅ (5 resolutions) | ✅ (text/image/video) |
| Performance (CTR / likes / CVR / 6-sec play rate) | ⚠️ political only | ✅ | ❌ |
| Headline / description / destination URL | ✅ | ✅ | ✅ (text-ad protobuf decoded) |
| Demographics + regions | ✅ political only | ❌ | ❌ |
| Funding entity / disclaimer (political) | ✅ | ❌ | ❌ |

> **Why this works** — every platform now publishes a transparency
> portal (EU DSA + voluntary self-regulation). They expose creative +
> run dates publicly; what they hide is real spend / CTR / ROAS. The
> longest-running ads are the proven winners — **a creative running
> 60+ days is almost certainly profitable**, no paid spy tool can
> give you better signal.
>
> Some TikTok CC modes (`keyword_insights`, `creative_insights`,
> `top_products`, `trending_*`, `hashtag_analytics`, `song_analytics`)
> require a free `ads.tiktok.com` login — run `agentsearch login
> tiktok_business` once and the cookies persist. The other 6 modes
> (`top_ads`, `top_ads_spotlight`, `ad_*`) work without auth.
>
> Google ATC under residential proxies: when the cloakbrowser+stealth
> path hits Google's bot challenge, the engine automatically falls
> back to a raw HTTP transport (`GoogleAdTransparencyEngine.raw()`)
> for full coverage.

</details>

<details>
<summary><b>📚 Developer Documentation Search — 142 platforms (Stripe / OpenAI / Anthropic / AWS / TikTok / WhatsApp / Telegram / Meta / …)</b></summary>

Searching developer-doc portals directly is painful — they're heavy
React SPAs (developers.facebook.com, platform.openai.com,
docs.anthropic.com), their built-in search returns shells with
zero hydrated results, and CloakBrowser+stealth+residential-proxy
gets blocked at navigation commit on most of them.

Pragmatic shortcut: drive an external web-search engine
(DuckDuckGo by default) with `site:<host>` prepended, then route
each match through `extract_page()` for clean Markdown.

#### `facebook_docs` — Meta Developer Documentation

```bash
# Generic search
agentsearch search "ad creation" -e fb_docs --json

# Limit to a sub-product (16 known: marketing-api, graph-api,
# whatsapp-business, instagram-graph, messenger, threads,
# audience-network, app-events, ad-library, login, webhooks,
# business-sdk, permissions, …)
agentsearch search "create campaign" -e fb_docs --product marketing-api

# Reference docs only (skip tutorials)
agentsearch search "adcreative" -e fb_docs --mode reference --product marketing-api

# Pin to an API version
agentsearch search "ad insights" -e fb_docs --api-version v25.0

# Search + auto-extract top N as Markdown in one call
agentsearch search "ad creation" -e fb_docs --depth 3 --json
# → 5 hits, top 3 each have body_markdown with curl examples,
#   parameter tables, ISO update dates.

# Aliases: fb_docs / meta_docs / fb_dev / facebook_docs
```

Output is auto-tagged with `doc_section` (reference / changelog /
quickstart / tutorial / use_case / webhook / guide), `product`
(inferred from URL path), and `api_version` (`v21.0` / `v25.0`).

#### `dev_docs` — generic developer-docs search across 142 platforms

```bash
# By preset platform
agentsearch search "subscription webhook" -e docs --platform stripe
agentsearch search "embeddings" -e docs --platform openai
agentsearch search "tool use streaming" -e docs --platform anthropic
agentsearch search "s3 presigned url" -e docs --platform aws --product s3
agentsearch search "useEffect cleanup" -e docs --platform react

# By arbitrary host (when the preset list doesn't cover it)
agentsearch search "rate limit" -e docs --site api.notion.com

# Multi-host presets auto-OR-combine
agentsearch search "messages" -e docs --platform anthropic
# → site:docs.anthropic.com OR site:docs.claude.com

# Modes: search / reference / changelog / api / tutorial / examples
agentsearch search "lambda" -e docs --platform aws --mode changelog

# Search + extract Markdown in one shot
agentsearch search "embeddings" -e docs --platform openai --depth 3 --json
```

#### Preset platforms (142)

| Domain | Aliases |
|---|---|
| **Cloud / Infra** | google-cloud / gcp · aws · azure / microsoft · docker · kubernetes / k8s · hashicorp / terraform · github · gitlab · cloudflare · vercel · netlify · fly · render |
| **APIs / SaaS** | stripe · twilio · slack · discord · shopify · supabase · firebase · mongodb · redis · postgres / postgresql · mysql · elasticsearch |
| **Social platforms** | tiktok / tiktok-business / tiktok-marketing / tiktok-login · snap / snapchat / snap-marketing · twitter / x · pinterest · reddit · linkedin · youtube |
| **Messaging / Chat** | whatsapp / whatsapp-business / whatsapp-cloud · telegram / telegram-bot · messenger · line · viber · wechat / wechat-pay · kakao · instagram · threads |
| **Meta megasite** | meta · facebook · instagram · messenger · threads · whatsapp · *(also: fb_docs engine for the 16-product narrow)* |
| **Google products** | google-cloud / gcp · firebase · google-ads · google-analytics · google-maps · google-pay · youtube · google-ai / gemini |
| **Mobile analytics / attribution / ad-intel** 🆕 | data.ai / appannie · sensortower · appsflyer · appsflyer-performance-index · appsflyer-benchmarks · adjust · branch · applovin / applovin-max · bigspy · similarweb · admiral / getadmiral · businessofapps · qimai / 七麦 · diandian / 点点数据 |
| **AI / ML** | openai · anthropic / claude · huggingface / hf · cohere · pinecone · google-ai / gemini · langchain · llamaindex |
| **Frontend / Languages** | mdn / mozilla · react · vue · angular · svelte · nextjs / next · remix · nuxt · nodejs / node · deno · bun · python · typescript · rust · go / golang |
| **Mobile** | android · apple / ios · swift · flutter · react-native · expo |
| **Browsers** | chrome · webkit |
| **DevOps / Observability** | datadog · grafana · prometheus · sentry · opentelemetry |
| **Identity** | auth0 · okta · clerk |
| **Workspace** | notion · airtable · linear |
| **ML training infra** | wandb · mlflow · ray |

> **Limitation** — DDG occasionally throttles `site:` queries from
> residential proxies. The engine surfaces `last_status` for
> debugging; in practice swapping the underlying search engine
> (Brave / Searx) is a one-line change in `dev_docs.py`. Each preset
> is just a `host` list — adding a new vendor is a 1-line PR.

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
