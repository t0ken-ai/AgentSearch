# 🔍 AgentSearch

**The search engine for AI agents. Free, local, private.**

A stealth web search toolkit for AI agents that bypasses bot detection on 39+ websites — running entirely on your machine, with zero API keys, zero cloud dependency, and zero data leakage.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![No API Key Required](https://img.shields.io/badge/No%20API%20Key-Required-green.svg)]()
[![Local Only](https://img.shields.io/badge/Data-Never%20Leaves%20Your%20Machine-orange.svg)]()

**[English](README.md)** | [中文](README_CN.md)

---

## Why This Exists

Most "free" search tools aren't really free:

- **API-based services** (Tavily, Serper, Firecrawl) send your queries to their servers. Free tiers run out. Your data is their data.
- **Browser automation** (Selenium, Puppeteer) gets blocked immediately by Google, Cloudflare, and modern anti-bot systems.
- **Meta-search engines** (SearXNG) are great but require self-hosting a server.

**AgentSearch** is different:

- 🔒 **100% local** — every search runs on your machine. No data ever leaves your network.
- 🆓 **100% free** — no API keys, no rate limits, no subscriptions. Ever.
- 🕵️ **Anti-detection built-in** — powered by [CloakBrowser](https://github.com/CloakHQ/CloakBrowser), a Chromium fork with 49 C++ level fingerprint patches.
- 🤖 **OpenClaw native** — install as a skill, use it immediately. One command to search any site.

---

## Features

- **39+ website adapters** — Google, Bing, DuckDuckGo, YouTube, Reddit, GitHub, StackOverflow, Hacker News, Medium, Amazon, Wikipedia, Bilibili, Zhihu, Xiaohongshu, and many more
- **Headless stealth browsing** — CloakBrowser modifies Chromium at the C++ source level. Not a JS injection. Not a config patch. Anti-bot systems see a real browser because it *is* a real browser.
- **Zero configuration** — no API keys, no accounts, no sign-ups. Install and search.
- **Privacy first** — all processing happens locally. Your search queries never touch any cloud service.
- **CLI + Python API** — use from command line or import as a library
- **Auto-retry with fallback** — if one engine blocks you, automatically retries or switches to alternatives
- **Extensible** — add new site adapters with a simple Python class

---

## Supported Sites

**Search Engines:** Google, Bing, DuckDuckGo, Yandex, Brave, Baidu, Sogou, 360 Search, Startpage, Ecosia, Qwant

**Tech & Dev:** GitHub, StackOverflow, Hacker News, NPM, dev.to

**Social & Forum:** Reddit, Twitter/X, BlackHatWorld, Quora, Zhihu, Weibo, Xiaohongshu

**Content & Media:** Medium, Wikipedia, Wikivoyage, YouTube, Product Hunt, Spotify, Twitch, TikTok, Instagram, Netflix, Bilibili, Douyin, Toutiao

**Shopping & Business:** Amazon, Icecat, LinkedIn Jobs, Indeed, Yelp

**Academic & Specialized:** PubMed, Google Patents, VirusTotal, Internet Archive, Wolfram Alpha

**Images:** Unsplash, Pixabay, Pexels

**Podcasts:** Xiaoyuzhou FM

*And growing — new adapters are being added continuously.*

---

## Quick Start

### Prerequisites

- Python 3.9+
- [OpenClaw](https://github.com/openclaw/openclaw) (optional, for skill integration)

### Installation

```bash
# 1. Install CloakBrowser
pip install cloakbrowser

# 2. Clone this repo
git clone https://github.com/t0ken-ai/AgentSearch.git
cd AgentSearch

# 3. Install as editable package
pip install -e .
```

### Usage

**CLI:**
```bash
# Search Google
python -m cloak_stealth_suite.cli search "latest AI news" --engine google

# Search DuckDuckGo
python -m cloak_stealth_suite.cli search "Rust tutorial" --engine duckduckgo

# Search GitHub repos
python -m cloak_stealth_suite.cli search "headless browser" --engine github

# List all available engines
python -m cloak_stealth_suite.cli list-engines

# Get JSON output
python -m cloak_stealth_suite.cli search "Python async" --engine stackoverflow --json
```

**Python API:**
```python
from cloak_stealth_suite.core import launch, BrowserConfig, new_page
from cloak_stealth_suite.engines.google import GoogleEngine

browser = launch(BrowserConfig(headless=True, humanize=True))
page = new_page(browser)
engine = GoogleEngine(page)
results = engine.search("open source AI models", limit=5)

for r in results:
    print(f"[{r.title}]({r.url})")
    print(f"  {r.snippet[:120]}")

browser.close()
```

**OpenClaw Skill:**

Copy the `skills/agent-search/` folder to your OpenClaw skills directory:

```bash
cp -r skills/agent-search ~/.openclaw/workspace/skills/
```

Then your OpenClaw agent can search the web natively.

---

## Privacy & Security

### What runs locally
- ✅ Browser instance (CloakBrowser/Chromium)
- ✅ Search queries
- ✅ Result parsing
- ✅ All data processing

### What goes to the internet
- 🔍 Only the HTTP requests to the target website (e.g., `google.com/search?q=...`)
- That's it. No middleware, no proxy, no analytics, no telemetry.

### What never happens
- ❌ Your queries are never sent to any third-party API
- ❌ No usage tracking or analytics
- ❌ No cloud storage of search history
- ❌ No API keys stored or transmitted
- ❌ No data collection of any kind

**Your searches are yours. Period.**

---

## Architecture

```
AgentSearch/
├── cloak_stealth_suite/
│   ├── core.py              # Browser launch & config
│   ├── cli.py               # Command-line interface
│   ├── engines/             # Site adapters (one file per site)
│   │   ├── base.py          # BaseEngine + SearchResult
│   │   ├── google.py
│   │   ├── duckduckgo.py
│   │   └── ...              # 39+ adapters
│   ├── stealth/
│   │   └── enhance.py       # Anti-detection JS injection
│   └── tests/               # Test suite
├── skills/
│   └── agent-search/
│       └── SKILL.md          # OpenClaw skill definition
├── LEGAL.md
├── LICENSE
├── README.md
├── README_CN.md
└── pyproject.toml
```

Each website adapter is a single Python file inheriting from `BaseEngine`. Adding a new site is as simple as:

```python
from .base import BaseEngine, SearchResult

class MySiteEngine(BaseEngine):
    name = "mysite"

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # Navigate to search page
        self.page.goto(f"https://mysite.com/search?q={query}")
        # Parse results
        # Return list of SearchResult
        ...
```

---

## Comparison

| | AgentSearch | SearXNG | Tavily/Serper | free-search |
|--|------------|---------|---------------|-------------|
| **Cost** | Free forever | Free (self-hosted) | Free tier, then paid | Free |
| **Privacy** | 100% local | Depends on instance | Queries sent to cloud | Queries sent to cloud |
| **API Key** | None needed | None needed | Required | None needed |
| **Anti-detection** | C++ level patches | UA spoofing | N/A (API access) | Basic Puppeteer |
| **Setup** | pip install | Docker + config | Sign up + API key | npm install |
| **Sites** | 39+ adapters | Aggregates existing SEs | Google only | 15 engines |
| **JS rendering** | ✅ Full browser | ❌ HTTP only | ❌ API only | ✅ Puppeteer |
| **Login sites** | ✅ Cookie import | ❌ | ❌ | ❌ |

---

## Acknowledgments

This project is built on top of [**CloakBrowser**](https://github.com/CloakHQ/CloakBrowser) — an open-source stealth Chromium that modifies fingerprint signals at the C++ source level. Without CloakBrowser's incredible work on browser-level anti-detection, this project would not be possible.

> CloakBrowser — Stealth Chromium that passes every bot detection test. Not a patched config. Not a JS injection. A real Chromium binary with fingerprints modified at the C++ source level.
> — [github.com/CloakHQ/CloakBrowser](https://github.com/CloakHQ/CloakBrowser) (MIT License)

---

## Disclaimer

This project is provided for **entertainment and educational research purposes only**. The authors and contributors are NOT responsible for any misuse of this software.

- ❌ Do NOT use this tool to violate any laws or regulations in your jurisdiction.
- ❌ Do NOT use this tool to scrape websites that explicitly prohibit automated access in their Terms of Service.
- ❌ Do NOT use this tool for unauthorized data collection, privacy violation, or any illegal activity.
- ✅ DO respect robots.txt, rate limits, and website Terms of Service.
- ✅ DO use this tool for legitimate research, learning, and personal use.

By using this software, you agree to bear full responsibility for your actions. See [LEGAL.md](LEGAL.md) for full legal notices.

---

## License

MIT License — use it however you want. Just don't blame us if you get blocked (you won't).

---

## Contributing

Contributions welcome! Especially:

- New site adapters (see `engines/` for examples)
- Bug fixes for existing adapters
- Improved anti-detection techniques
- Documentation and translations

PRs are reviewed promptly.
