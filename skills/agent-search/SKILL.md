---
name: agent-search
description: Search the live web from 71 sites — Google, Bing, DuckDuckGo, YouTube, Reddit, GitHub, StackOverflow, Hacker News, arXiv, HuggingFace, Wikipedia, IMDB, Goodreads, Amazon, eBay, Pinterest, Unsplash, Apple Podcasts, Bilibili, Zhihu, Xiaohongshu, BBC, The Guardian, Reuters, AP News, CNN, NPR, Al Jazeera, TechCrunch, The Verge, Ars Technica and more — through a local stealth browser. Use this skill whenever the user wants to search the web, look up something online, find information on a specific site, research a topic, fetch up-to-date facts, browse a forum / video site / shopping site / academic site / news site, or get content the model's training data wouldn't know about. No API keys required, no rate limits, no third-party servers — every query runs in a Chromium on the user's machine. Prefer this skill over generic web_search whenever the user names a target site or wants results from a specific platform.
version: 3.0.0
metadata:
  short-description: Free local web search across 71 sites — Google, YouTube, Reddit, GitHub, arXiv, Amazon, IMDB, BBC, Reuters, TechCrunch, Bilibili, Zhihu, etc.
  keywords:
    - web search
    - search engine
    - google
    - youtube
    - reddit
    - github
    - stackoverflow
    - wikipedia
    - news
    - bbc
    - reuters
    - cnn
    - techcrunch
    - find on
    - look up
    - research
    - scrape
    - browse
    - 搜索
    - 查找
    - 搜
    - 新闻
---

# 🔍 AgentSearch Skill

A local stealth-browser search toolkit that gives an AI agent live access to 71 websites
across 15 categories — search engines, code/dev forums, academic papers, video sites,
shopping, social, podcasts, images and Chinese platforms — all without API keys, all
running on the user's machine through CloakBrowser (an anti-detection Chromium).

---

## When To Use This Skill

Invoke this skill **whenever the user wants to retrieve content from the live web**.
Trigger examples:

| User intent                                              | Use this skill? |
|----------------------------------------------------------|:---------------:|
| "Search Google for ..."                                  | ✅ → google     |
| "What does Reddit say about X"                           | ✅ → reddit     |
| "Find StackOverflow answers about Y"                     | ✅ → stackoverflow |
| "Show me YouTube tutorials on Z"                         | ✅ → youtube    |
| "Latest arXiv papers on transformers"                    | ✅ → arxiv      |
| "Top HuggingFace models for image generation"            | ✅ → huggingface|
| "What's on Hacker News today"                            | ✅ → hackernews |
| "B站搜下 Python 教程"                                     | ✅ → bilibili   |
| "知乎上对 X 的看法"                                       | ✅ → zhihu      |
| "Search a thing on the internet"                         | ✅ → google or duckduckgo |
| "Look up a Wikipedia article on …"                       | ✅ → wikipedia  |
| "Find a book on Goodreads"                               | ✅ → goodreads  |
| "Find a movie on IMDB"                                   | ✅ → imdb       |
| "Check Amazon / eBay prices for …"                       | ✅ → amazon / ebay |
| "Find a Pinterest board on …"                            | ✅ → pinterest  |

**Prefer this skill over a built-in `web_search` tool** when:
- The user named a specific site (Reddit, GitHub, YouTube, arXiv, etc).
- The user asks about content that needs a JS-rendered SPA (YouTube, Bilibili, Pinterest, etc).
- The user wants results from a non-English region (Bilibili, Zhihu, Baidu, Sogou).
- The user wants long-form content (Reddit threads, Medium articles, arXiv abstracts).
- The user values privacy and explicitly asks for local-only execution.

---

## The 71 Engines

Always pick the engine that matches the user's intent. If you're not sure, fall back to
`google`, `duckduckgo` or `bing`.

| Category | Engines |
|----------|---------|
| **General search** | `google`, `bing`, `duckduckgo`, `brave`, `yandex`, `startpage`, `ecosia`, `qwant` |
| **Chinese search** | `baidu`, `sogou`, `so360` |
| **Code / dev** | `github` (`github_search`), `stackoverflow`, `hackernews`, `npm` (`npm_search`), `devto` |
| **AI / research** | `huggingface`, `arxiv` |
| **Knowledge** | `wikipedia`, `wikivoyage`, `pubmed`, `wolfram` |
| **Forums / community** | `reddit`, `reddit_subreddit`, `quora`, `blackhatworld`, `producthunt` |
| **Social — global** | `twitter`, `instagram` |
| **Social — Chinese** | `zhihu`, `weibo`, `xiaohongshu`, `douyin`, `toutiao`, `bilibili` |
| **Western news** | `bbc`, `guardian`, `reuters`, `apnews`, `cnn`, `npr`, `aljazeera`, `techcrunch`, `verge`, `arstechnica` |
| **Video / streaming** | `youtube`, `twitch`, `netflix`, `tiktok` |
| **Audio / podcasts** | `spotify`, `soundcloud`, `apple_podcasts`, `xiaoyuzhou` |
| **Movies & books** | `imdb`, `goodreads` |
| **News / content** | `medium` |
| **E-commerce** | `amazon`, `ebay`, `icecat`, `steam` |
| **Jobs & local** | `linkedin_jobs`, `indeed`, `yelp` |
| **Patents & security** | `google_patents`, `virustotal` |
| **Archive & files** | `archive_org`, `torrent_1337x` |
| **Images** | `unsplash`, `pixabay`, `pexels`, `pinterest` |

---

## Quick Recipes

Every recipe assumes the venv is activated:

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd ~/projects/AgentSearch
```

### Recipe 1 — Generic web search
```bash
python -m cloak_stealth_suite.cli search "what the user asked" --engine google --limit 5 --json
```
Returns JSON with `title`, `url`, `snippet` for each hit. Pick `duckduckgo` if Google
shows a CAPTCHA.

### Recipe 2 — Code / docs lookup
```bash
python -m cloak_stealth_suite.cli search "TypeError pandas dataframe groupby" --engine stackoverflow --limit 5 --json
python -m cloak_stealth_suite.cli search "kubernetes ingress controller" --engine github --limit 5 --json
```

### Recipe 3 — Latest research
```bash
python -m cloak_stealth_suite.cli search "transformer scaling laws" --engine arxiv --limit 5 --json
python -m cloak_stealth_suite.cli search "llama text-generation" --engine huggingface --limit 5 --json
```

### Recipe 4 — Discussion / opinions
```bash
python -m cloak_stealth_suite.cli search "best linux laptop 2025" --engine reddit --limit 5 --json
python -m cloak_stealth_suite.cli search "Python" --engine reddit_subreddit --limit 5 --json   # r/Python hot posts
python -m cloak_stealth_suite.cli search "what is consciousness" --engine quora --limit 5 --json
```

### Recipe 5 — Video / how-to
```bash
python -m cloak_stealth_suite.cli search "react hooks tutorial" --engine youtube --limit 10 --json
python -m cloak_stealth_suite.cli search "Python 入门" --engine bilibili --limit 5 --json
```

### Recipe 6 — Shopping
```bash
python -m cloak_stealth_suite.cli search "mechanical keyboard hotswap" --engine amazon --limit 5 --json
python -m cloak_stealth_suite.cli search "vintage hi-fi tube amp" --engine ebay --limit 5 --json
```

### Recipe 7 — Chinese platforms
```bash
python -m cloak_stealth_suite.cli search "机器学习" --engine zhihu --limit 5 --json
python -m cloak_stealth_suite.cli search "旅行攻略" --engine xiaohongshu --limit 5 --json
python -m cloak_stealth_suite.cli search "美食" --engine douyin --limit 5 --json
```

### Recipe 8 — Movie / book / podcast
```bash
python -m cloak_stealth_suite.cli search "Inception" --engine imdb --limit 5 --json
python -m cloak_stealth_suite.cli search "Dune" --engine goodreads --limit 5 --json
python -m cloak_stealth_suite.cli search "Lex Fridman" --engine apple_podcasts --limit 5 --json
```

### Recipe 9 — Images
```bash
python -m cloak_stealth_suite.cli search "mountain landscape" --engine unsplash --limit 5 --json
python -m cloak_stealth_suite.cli search "interior design" --engine pinterest --limit 5 --json
```

### Recipe 10 — Extract one URL's content
```bash
python -m cloak_stealth_suite.cli extract "https://example.com/article" --json
```

### Recipe 11 — List every available engine
```bash
python -m cloak_stealth_suite.cli list-engines
```

---

## Output Format

With `--json` every CLI command returns:

```json
{
  "engine": "google",
  "query": "open source software",
  "limit": 5,
  "count": 5,
  "results": [
    {
      "title": "Open-source software - Wikipedia",
      "url": "https://en.wikipedia.org/wiki/Open-source_software",
      "snippet": "Open-source software (OSS) is computer software …",
      "score": null
    }
  ]
}
```

Engine-specific results include extra fields on each item (use them when relevant):

| Engine                | Extra fields                                                           |
|-----------------------|------------------------------------------------------------------------|
| `youtube`             | `video_id`, `channel`, `views`, `duration`, `upload_date`              |
| `imdb`                | `imdb_id`, `year`, `content_type`, `runtime`, `imdb_rating`, `vote_count` |
| `goodreads`           | `goodreads_id`, `author`, `avg_rating`, `rating_count`, `image_url`    |
| `arxiv`               | `arxiv_id`, `authors`, `categories`, `published`, `pdf_url`            |
| `huggingface`         | `model_id`, `author`, `downloads`, `likes`, `pipeline_tag`, `tags`     |
| `reddit_subreddit`    | `score`, `num_comments`, `author`, `created_utc`                       |
| `amazon` / `ebay`     | `price`, `rating`, `condition`, `shipping`, `seller`                   |
| `unsplash`/`pixabay`/`pexels` | `image_url`, `photographer`, `alt_text`                       |
| `apple_podcasts`      | `track_id`, `artist`, `genre`, `feed_url`, `release_date`              |

---

## Engine-Selection Heuristics

**For agents deciding which engine to call when the user wasn't specific:**

1. **Generic factual question** → `duckduckgo` (most reliable, no consent dialog).
2. **Latest news / current events** → `google` (best freshness).
3. **Code error / programming question** → `stackoverflow` first, then `github`.
4. **Academic / scientific** → `arxiv` for ML / physics / CS, `pubmed` for medical.
5. **Open-source library** → `github`, supplement with `npm` for JS.
6. **Discussion / "what do people think"** → `reddit`, fallback `hackernews`.
7. **Video tutorial / demonstration** → `youtube`. For Chinese audiences `bilibili`.
8. **Product review / shopping** → `amazon` for new, `ebay` for used.
9. **Restaurant / local business** → `yelp`.
10. **Picture / mood board** → `unsplash` (hi-res free) or `pinterest` (variety).
11. **Movie / TV info** → `imdb`. For "what to watch" → `netflix`.
12. **Book search / reviews** → `goodreads`.
13. **Podcast** → `apple_podcasts`. Chinese podcast → `xiaoyuzhou`.
14. **Patent prior-art** → `google_patents`.
15. **File hash / virus scan** → `virustotal`.
16. **Chinese-language query** → `baidu` or `zhihu` over `google` for better recall.

---

## Common Patterns

### Multi-engine fan-out for breadth
When the user asks something open-ended ("research X for me"), call 2-3 complementary
engines and merge:

```bash
python -m cloak_stealth_suite.cli search "X" --engine google     --limit 5 --json
python -m cloak_stealth_suite.cli search "X" --engine reddit     --limit 5 --json
python -m cloak_stealth_suite.cli search "X" --engine arxiv      --limit 3 --json
```

### Fallback chain for resilience
If engine N times out or returns 0 results, retry on engine N+1:

```
google → duckduckgo → bing → brave
```

The DuckDuckGo HTML fallback (`html.duckduckgo.com/html`) is the most rate-limit-free
option.

### Refining a query for a target site
For Chinese and walled platforms (`xiaohongshu`, `douyin`, `weibo`, `toutiao`, `xiaoyuzhou`)
the adapters automatically fall back to `site:` searches on Google/Bing/DuckDuckGo. The
caller doesn't need to do anything special — the adapter handles it.

---

## Python API (when CLI isn't enough)

```python
import sys
sys.path.insert(0, "/Users/<user>/projects/AgentSearch")  # absolute path

from cloak_stealth_suite.core import launch, BrowserConfig, new_page
from cloak_stealth_suite.engines.duckduckgo import DuckDuckGoEngine

browser = launch(BrowserConfig(headless=True, humanize=True))
try:
    page = new_page(browser)
    engine = DuckDuckGoEngine(page)
    results = engine.search("transformer attention", limit=5)
    for r in results:
        print(f"{r.title}\n  {r.url}\n  {r.snippet}\n")
finally:
    browser.close()
```

Every engine is in `cloak_stealth_suite.engines.<name>` and exposes a
`<NameCamelCase>Engine` class that takes a `page` object and provides
`search(query: str, limit: int) -> list[SearchResult]`.

---

## Important Operational Notes

1. **Always activate venv before running**: `source ~/tools/cloakbrowser/venv/bin/activate`
2. **Each call launches a Chromium instance** (≈ 0.5s startup). Batch when possible —
   don't loop one query per call when 1 query → many engines is better.
3. **Default `--limit 5`**. Maximum useful is 20 for most engines (Google caps SERP at ~10).
4. **Results sometimes come back below `limit`**: most search engines pack ads / "people also ask"
   panels into the SERP. Yield is typically 90-100% in stress tests but not guaranteed 100%.
5. **Walled sites use external-search-engine fallback automatically** — when you call
   `xiaohongshu`, `douyin`, `weibo`, `toutiao`, `xiaoyuzhou`, the adapter first tries the
   site's own search; if that's blocked it transparently routes through Google/Bing/DDG
   with a `site:` filter. The returned `SearchResult` carries a `source` attribute
   indicating which path produced the hit.

---

## Troubleshooting

| Symptom                                         | Fix                                                   |
|-------------------------------------------------|-------------------------------------------------------|
| `cannot import name 'core'` from another path   | Run from `~/projects/AgentSearch` or set `PYTHONPATH` |
| Google shows a CAPTCHA / sorry interstitial     | Switch to `--engine duckduckgo` for the next 30 min   |
| Empty `results: []`                             | Try a different engine; site may have changed DOM     |
| `ImportError: cloakbrowser`                     | `pip install cloakbrowser` inside the venv            |
| Browser hangs                                   | Add `--no-headless` to debug visually                 |

---

## Privacy Guarantee

- 🔒 100% local — Chromium runs on the user's machine, results parsed locally
- 🚫 Zero data leakage — no queries sent to any third-party API or cloud service
- 🔑 No API keys — no accounts, no sign-ups, no authentication tokens
- 📊 No telemetry — zero tracking, zero analytics, zero usage monitoring
- 💰 100% free — no subscriptions, no rate limits

The only network traffic is the direct request from the user's machine to the target site
(e.g., `google.com/search?q=...`). Nothing else.

---

*One skill, the whole web — local, free, no API keys.*
