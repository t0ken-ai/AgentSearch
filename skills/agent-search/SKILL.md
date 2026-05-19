---
name: agent-search
description: The search engine for AI agents. Free, local, private. 39+ sites including Google, YouTube, Reddit, GitHub, Bilibili, Zhihu. No API keys. No cloud. No data leakage. Runs entirely on your machine using anti-detection stealth browser.
version: 2.0.0
---

# 🔍 AgentSearch

**The search engine for AI agents. Free, local, private.**

Search 39+ websites through an anti-detection headless browser. No API keys. No cloud services. No data leaves your machine.

## Privacy Guarantee / 隐私保证

- 🔒 **100% local execution** — browser runs on your machine, results parsed locally
- 🚫 **Zero data leakage** — no queries sent to any third-party API or cloud service
- 🔑 **No API keys** — no accounts, no sign-ups, no authentication tokens
- 📊 **No telemetry** — zero tracking, zero analytics, zero usage monitoring
- 💰 **100% free** — no subscriptions, no rate limits, no hidden costs

**The only network traffic is your direct request to the target website (e.g., google.com). Nothing else.**

---

## Supported Sites / 支持的网站

**Search Engines:** Google, Bing, DuckDuckGo, Yandex, Brave

**Tech & Dev:** GitHub, StackOverflow, Hacker News, NPM, dev.to

**Social & Forum:** Reddit, Twitter/X, BlackHatWorld, Quora

**Content & Media:** Medium, Wikipedia, Wikivoyage, YouTube, Product Hunt, Spotify, Twitch, TikTok, Instagram, Netflix

**Shopping & Business:** Amazon, Icecat, LinkedIn Jobs, Indeed, Yelp

**Academic & Specialized:** PubMed, Google Patents, VirusTotal, Internet Archive, Wolfram Alpha

---

## Usage / 使用方法

**CLI (recommended for agents):**

```bash
source ~/tools/cloakbrowser/venv/bin/activate
python3 ~/projects/AgentSearch/cloak_stealth_suite/cli.py search "query" --engine google
```

**Available commands:**

```bash
# Search
python3 ~/projects/AgentSearch/cloak_stealth_suite/cli.py search "query" --engine ENGINE [--limit N] [--json]

# Extract page content
python3 ~/projects/AgentSearch/cloak_stealth_suite/cli.py extract "https://example.com"

# List all engines
python3 ~/projects/AgentSearch/cloak_stealth_suite/cli.py list-engines
```

**Python API:**

```python
import sys
sys.path.insert(0, '~/projects/AgentSearch')
from cloak_stealth_suite.core import launch, BrowserConfig, new_page
from cloak_stealth_suite.engines.duckduckgo import DuckDuckGoEngine

browser = launch(BrowserConfig(headless=True, humanize=True))
page = new_page(browser)
engine = DuckDuckGoEngine(page)
results = engine.search("your query", limit=5)
for r in results:
    print(f"[{r.title}]({r.url})")
browser.close()
```

---

## Important Notes

1. **Always activate venv first**: `source ~/tools/cloakbrowser/venv/bin/activate`
2. **Each search launches a browser instance** — not lightweight. Batch queries when possible.
3. **If a search fails**, try a different engine. DuckDuckGo and Yandex are most reliable.
4. **Powered by [CloakBrowser](https://github.com/CloakHQ/CloakBrowser)** — C++ level anti-detection Chromium

## Troubleshooting

- **Google blocked**: Try again later, or use `--engine duckduckgo` / `--engine bing`
- **Timeout**: Use `--no-headless` to debug, or increase timeout in BrowserConfig
- **Empty results**: Site may have changed DOM. Check PROGRESS.md for last known status

---

*一个 Skill 搜遍全网，纯本地运行，不用 API Key，不花一分钱，数据不上云。*
