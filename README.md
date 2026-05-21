<div align="center">

<img src="https://img.shields.io/badge/-🔍_AgentSearch-1f2937?style=for-the-badge" alt="AgentSearch" height="48"/>

### The search engine for AI agents.

# **Free. Local. Private. Bypasses Cloudflare.**

**One Python package. 71 websites. Zero API keys. Zero data leakage.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![Sites: 71](https://img.shields.io/badge/Sites-71-success.svg)]()
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

python -m cloak_stealth_suite.cli search "what's new in transformers" --engine google --json
python -m cloak_stealth_suite.cli search "react hooks tutorial"     --engine youtube --limit 10
python -m cloak_stealth_suite.cli search "best laptop 2025"         --engine reddit
python -m cloak_stealth_suite.cli search "transformer attention"    --engine arxiv
```

71 sites. One CLI. Runs entirely on your machine. **Bypasses Cloudflare, PerimeterX, Akamai, DataDome, and every fingerprint test we know of.**

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

|                                          | AgentSearch | API services<br>(Tavily / Serper / Firecrawl) | Stock browser automation<br>(Selenium / Puppeteer) | SearXNG |
|:-----------------------------------------|:-----------:|:---------------------------------------------:|:--------------------------------------------------:|:-------:|
| 💰 Cost                                  | **Free forever** | Free tier → paid | Free | Free |
| 🔑 API key required                      | **No** | Yes | No | No |
| 🌐 Data leaves your machine              | **Never** | Always | Never | Depends on instance |
| 🛡️ **Bypasses Cloudflare**               | ✅ **C++ patches** | N/A (uses APIs) | ❌ Detected instantly | ❌ HTTP-only |
| 🔬 **Bypasses fingerprint tests**         | ✅ All major | N/A | ❌ Fails CreepJS | ❌ |
| 🌍 Sites supported                       | **71 sites** | 1 (Google) | Anything you code | Aggregates ~10 SEs |
| 🐍 JS rendering                          | ✅ Full Chromium | ❌ API-only | ✅ Yes | ❌ HTTP only |
| 🔐 Login-walled sites                    | ✅ Cookie import | ❌ | Limited | ❌ |
| 🚀 Setup                                 | `pip install` | Sign up + API key | Code from scratch | Docker + config |
| 💾 Self-hosted server needed             | No | No | No | Yes |

---

## 🌍 The 71 Sites — Categorized

> Every site is implemented as a self-contained adapter (`cloak_stealth_suite/engines/<name>.py`) with a runnable test (`tests/test_<name>.py`).

<table>
<tr>
<td valign="top" width="50%">

**🔍 Search Engines (11)**
Google · Bing · DuckDuckGo · Yandex · Brave · Baidu · Sogou · 360 Search · Startpage · Ecosia · Qwant

**💻 Code & Dev (5)**
GitHub · StackOverflow · Hacker News · NPM · dev.to

**🤖 AI & Research (2)**
HuggingFace · arXiv

**📚 Knowledge (4)**
Wikipedia · Wikivoyage · PubMed · Wolfram Alpha

**💬 Social & Forum (6)**
Reddit · Reddit Subreddit (JSON) · Twitter/X · Quora · BlackHatWorld · Instagram

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

**💼 Jobs & Local (3)**
LinkedIn Jobs · Indeed · Yelp

**📜 Patents & Security (2)**
Google Patents · VirusTotal

**📦 Archive & Files (2)**
Internet Archive · 1337x

**🖼️ Images (4)**
Unsplash · Pixabay · Pexels · Pinterest

</td>
</tr>
</table>

> *And growing — new adapters are added continuously.*

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
python -m cloak_stealth_suite.cli search "latest AI news" --engine google

# Code lookup on StackOverflow
python -m cloak_stealth_suite.cli search "TypeError pandas groupby" --engine stackoverflow

# Latest research papers
python -m cloak_stealth_suite.cli search "transformer scaling laws" --engine arxiv

# Reddit discussion
python -m cloak_stealth_suite.cli search "best linux laptop 2025" --engine reddit

# Video tutorials
python -m cloak_stealth_suite.cli search "react hooks" --engine youtube --limit 10

# Shopping
python -m cloak_stealth_suite.cli search "mechanical keyboard" --engine amazon

# Chinese platform
python -m cloak_stealth_suite.cli search "机器学习" --engine zhihu

# JSON output for piping into other tools
python -m cloak_stealth_suite.cli search "open source" --engine github --json | jq .

# List every available engine
python -m cloak_stealth_suite.cli list-engines
```

### 3. Or use it from Python

```python
from cloak_stealth_suite.core import launch, BrowserConfig, new_page
from cloak_stealth_suite.engines.google import GoogleEngine

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

Now your OpenClaw / Codex / Kiro agent natively knows how to search 71 sites — no plumbing, no prompts.

---

## 🍳 Cookbook — Common Recipes

<details>
<summary><b>📚 Research a topic across multiple sources</b></summary>

```bash
# Fan out across complementary engines and merge
python -m cloak_stealth_suite.cli search "X" --engine google     --limit 5 --json > /tmp/g.json
python -m cloak_stealth_suite.cli search "X" --engine reddit     --limit 5 --json > /tmp/r.json
python -m cloak_stealth_suite.cli search "X" --engine arxiv      --limit 3 --json > /tmp/a.json
python -m cloak_stealth_suite.cli search "X" --engine hackernews --limit 5 --json > /tmp/h.json
```
</details>

<details>
<summary><b>🛒 Compare prices across e-commerce sites</b></summary>

```bash
python -m cloak_stealth_suite.cli search "AirPods Pro 2" --engine amazon --json
python -m cloak_stealth_suite.cli search "AirPods Pro 2" --engine ebay   --json
```
</details>

<details>
<summary><b>🎬 Look up a movie + its book + its podcast</b></summary>

```bash
python -m cloak_stealth_suite.cli search "Dune"        --engine imdb       --json
python -m cloak_stealth_suite.cli search "Dune"        --engine goodreads  --json
python -m cloak_stealth_suite.cli search "Frank Herbert" --engine apple_podcasts --json
```
</details>

<details>
<summary><b>🇨🇳 Search Chinese platforms (auto-fallback to Google site:)</b></summary>

```bash
# These adapters ship with a transparent Google → Bing → DDG fallback for
# their walled SERPs — no auth, no cookies, just works.
python -m cloak_stealth_suite.cli search "旅行攻略" --engine xiaohongshu
python -m cloak_stealth_suite.cli search "美食"     --engine douyin
python -m cloak_stealth_suite.cli search "科技"     --engine weibo
```
</details>

<details>
<summary><b>🤖 Find AI models / datasets on HuggingFace</b></summary>

```bash
python -m cloak_stealth_suite.cli search "llama" --engine huggingface --json
# Returns model_id, author, downloads, likes, pipeline_tag, library, tags
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
├── cloak_stealth_suite/
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

- 🆕 New site adapters (browse `cloak_stealth_suite/engines/` for examples)
- 🐛 Bug fixes for existing adapters
- 🎯 Improved anti-detection techniques
- 🌐 Documentation and translations

PRs are reviewed promptly. Open an issue first if your change is large.

---

<div align="center">

**One skill. The whole web. Local. Free. Bypasses Cloudflare.**

[![GitHub](https://img.shields.io/badge/GitHub-t0ken--ai%2FAgentSearch-181717?logo=github)](https://github.com/t0ken-ai/AgentSearch)

</div>
