---
name: agent-search
description: Local stealth-browser toolkit for the live web — 80+ search engines (Google, Reddit, GitHub, YouTube, arXiv, Bilibili, Zhihu, …), 5 ad-library engines (Meta / Instagram / TikTok Creative Center / TikTok Ad Library / Google Ads Transparency), 2 developer-doc engines covering 142 platforms (Stripe / OpenAI / Anthropic / AWS / TikTok / WhatsApp / Telegram / AppsFlyer / Adjust / data.ai / 七麦 …), Apple App Store + Google Play search, and competitor-research workflows (App URL → ads on every platform). All running in a Chromium on the user's machine — no API keys, no rate limits, no third-party servers. Use this skill whenever the user wants to search the web, look up developer docs, research a competitor's app + ads + landing pages, scan an attribution / MMP / ad-intel portal, or fetch up-to-date facts the model's training data wouldn't know.
version: 4.0.0
metadata:
  short-description: 80+ search engines + 142 dev-docs platforms + 5 ad libraries + App Store search + competitor-ad research — all local, no API keys.
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
    - developer documentation
    - api docs
    - stripe docs
    - openai docs
    - anthropic docs
    - aws docs
    - whatsapp api
    - telegram bot
    - meta ad library
    - facebook ad library
    - tiktok ad library
    - google ad transparency
    - competitor ads
    - app store
    - google play
    - apple app store
    - appsflyer
    - adjust
    - sensor tower
    - data.ai
    - bigspy
    - similarweb
    - 搜索
    - 查找
    - 搜
    - 新闻
    - 七麦
    - 点点数据
    - 应用商店
    - 广告情报
---

# 🔍 AgentSearch Skill

A local stealth-browser toolkit that gives an AI agent live access to:

- **80+ web search engines** across 16 categories (general, code, academic, social, video, news, shopping, Chinese, …)
- **5 ad-library engines** for competitor ad research (Meta + Instagram + TikTok CC + TikTok Ad Library + Google ATC)
- **2 developer-documentation engines** covering **142 platforms** (Stripe / OpenAI / Anthropic / AWS / TikTok / WhatsApp / Telegram / Meta / AppsFlyer / Adjust / data.ai / Sensor Tower / 七麦 / 点点数据 / …)
- **Apple App Store + Google Play search** with 25+ metadata fields per app
- **End-to-end workflows** — App Store URL → ads on every paid platform, ad-record list → bulk media download, search → markdown extract in one shot

All running in CloakBrowser (anti-detection Chromium) on the user's machine — no API keys, no rate limits, no third-party servers, 100% privacy.

---

## When To Use This Skill

Invoke this skill **whenever the user wants something from the live web**. Trigger examples:

| User intent | Tool / Engine |
|---|---|
| "Search Google for …" | `search` engine=google |
| "What does Reddit say about X" | `search` engine=reddit |
| "Find StackOverflow answers about Y" | `search` engine=stackoverflow |
| "Show me YouTube tutorials on Z" | `search` engine=youtube |
| "Latest arXiv papers on transformers" | `search` engine=arxiv |
| "B站搜下 Python 教程" | `search` engine=bilibili |
| "知乎上对 X 的看法" | `search` engine=zhihu |
| **"Look up Stripe webhook docs"** | `search` engine=dev_docs platform=stripe |
| **"OpenAI embeddings reference"** | `search` engine=dev_docs platform=openai mode=reference |
| **"WhatsApp Business send-message API"** | `search` engine=dev_docs platform=whatsapp |
| **"AppsFlyer SDK iOS integration"** | `search` engine=dev_docs platform=appsflyer |
| **"七麦 关键词监控 API"** | `search` engine=dev_docs platform=qimai |
| **"Find Shopify's Facebook ads"** | `search` engine=meta_ad_library + advertiser_contains filter |
| **"What ads is Wish running on TikTok?"** | `search` engine=tiktok_creative_center mode=top_ads |
| **"Google ads from shopify.com"** | `search` engine=google_ad_transparency mode=domain domain=shopify.com |
| **"What competitor ads does this app run?"** | `find_competitor_ads` app_url=… |
| **"Search Shopify on Apple App Store"** | `search_app` query=shopify store=apple |
| **"Get me the metadata for this iOS app"** | `lookup_app` app_url=… |
| **"Read the top 5 articles about X"** | `search` then `extract_many` urls=[…] |

**Prefer this skill over a built-in `web_search` tool** when:
- The user named a specific site / portal / vendor (Reddit, GitHub, YouTube, Stripe docs, Meta ad library, Apple App Store, AppsFlyer, …)
- The user is doing competitor research (ads, app metadata, landing pages, dev portal scans)
- The user wants up-to-date docs the model's training cutoff missed
- The user values privacy / local-only execution
- The user wants a JS-rendered SPA (YouTube, Bilibili, Pinterest, Reddit redesign, all ad-library SPAs)

---

## 🔌 MCP Server Mode (recommended for AI hosts)

AgentSearch ships an MCP server exposing **9 tools**. Configure once in Kiro / Claude Desktop / Cursor / Cline / Continue, then call directly.

### Configure (Kiro `.kiro/settings/mcp.json`)

```json
{
  "mcpServers": {
    "agent-search": {
      "command": "/Users/gao/tools/cloakbrowser/venv/bin/python",
      "args": ["-m", "agent_search.mcp_server"],
      "env": {
        "AGENTSEARCH_HEADLESS": "1",
        "FLUXISP_PROXY": "<optional residential proxy URL>"
      }
    }
  }
}
```

### The 9 MCP tools

| Tool | Args | Purpose |
|---|---|---|
| **`search`** | `query, engine, limit, depth, engine_options` | Query any of 80+ engines; `engine_options` dict forwards engine-specific kwargs (platform / mode / country / page_id / sort / …); `depth>0` inlines markdown for top N hits |
| **`extract`** | `url, paginate, max_scrolls, include_links, include_images` | Fetch one URL, return readability-extracted markdown + structured metadata |
| **`extract_many`** | `urls[], paginate, max_scrolls, include_links, include_images` | Batch extract — preserves input order, validates URLs, returns one record per URL |
| **`list_engines`** | (none) | Enumerate engines + categories + companion_tools + `engine_options_examples` |
| **`list_dev_docs_platforms`** | `filter_substring?, category?` | Browse the 142 dev_docs presets by substring or category |
| **`search_app`** | `query, store, country, limit, fast, with_contact, proxy_url` | Apple App Store + Google Play keyword search, 25+ metadata fields |
| **`lookup_app`** | `app_url, country, proxy_url` | Single-app metadata from store URL or bare id |
| **`find_competitor_ads`** | `app_url, platforms[], limit_per_platform, country, precise, proxy_url` | App URL → fan-out to Meta/IG/Google/TikTok ad libraries → merged ad stream |
| **`download_ad_media`** | `records[], output_dir, proxy_url, max_per_record, max_workers, timeout` | Bulk-download every image / video URL from a list of ad-engine results |

### 💡 Two patterns that save the most time

**1. `depth=N` — search + read top N bodies in one call**

Most agents call `search`, pick the top 3 hits, then call `extract` 3
times. That's 4 MCP round-trips. Pass `depth=3` to `search` and the
top 3 results come back with `body_markdown` already attached:

```jsonc
search(query="adjust 2026 mobile app report",
       engine="dev_docs",
       engine_options={"platform": "ppc.land"},
       limit=5,
       depth=3)
// → 5 SERP hits; first 3 also have body_markdown + body_word_count
```

Use `depth` whenever the user asked for content (a report, tutorial,
analysis) rather than just URLs. Saves ~3-5x interaction time.

**2. `extract_many` — read a whole list of URLs in one call**

When the agent already has a list of URLs (from a previous call,
from a roundup article, from the user) batch them into one
`extract_many` call rather than looping `extract`:

```jsonc
extract_many(
  urls=["https://ppc.land/adjusts-2026-mobile-app-report-...",
        "https://ppc.land/shopping-app-installs-surge-61-globally...",
        "https://www.appsflyer.com/resources/reports/performance-index/"],
  paginate=true, max_scrolls=3,
)
```

Returns one record per URL (preserves order, validates http(s)).

### `engine_options` cheat-sheet (the killer feature)

`engine_options` is a dict forwarded to `EngineClass.search()` so engine-specific parameters work through MCP:

```jsonc
// dev_docs — 142 preset platforms
{"platform": "stripe"}                          // alias from list_dev_docs_platforms
{"platform": "openai", "mode": "reference"}     // search / reference / changelog / api / tutorial / examples
{"platform": "aws", "product": "lambda"}        // narrow with inurl:lambda
{"platform": "react", "api_version": "18"}      // quote a literal version
{"site": "docs.example.com"}                    // arbitrary host (when no preset)

// fb_docs — Meta-only with 16 product slugs
{"product": "marketing-api", "mode": "reference"}
{"product": "whatsapp-business", "api_version": "v25.0"}

// meta_ad_library / fb_ads
{"country": "US", "active_status": "active", "ad_type": "all", "media_type": "video"}
{"mode": "advertiser", "page_id": "20409006880"}     // canonical Facebook page id
{"mode": "page_url", "page_url": "https://facebook.com/Shopify"}

// google_ad_transparency / g_ads
{"mode": "domain", "domain": "shopify.com", "region": "US"}
{"mode": "search_advertisers", "region": "US"}
{"mode": "advertiser_ads", "advertiser_id": "AR..."}

// tiktok_creative_center / tt_ads
{"mode": "top_ads", "country": "US", "industry": "ecommerce", "period": 30}

// reddit / reddit_subreddit
{"sort": "top", "time": "month"}

// youtube
{"upload_date": "this_week", "duration": "long", "sort_by": "view_count"}

// github_search
{"type": "code", "language": "python", "stars": ">100"}

// arxiv
{"category": "cs.AI", "sort_by": "submittedDate"}
```

**Heuristic**: if a previous `search` returned empty / wrong results for a structured query, call `list_engines` and read its `engine_options_examples` first.

---

## The 80+ Engines

Always pick the engine that matches the user's intent. Fall back to `google` / `duckduckgo` / `bing` when uncertain.

| Category | Engines |
|---|---|
| **General search** | `google` · `bing` · `duckduckgo` · `brave` · `yandex` · `startpage` · `ecosia` · `qwant` |
| **Chinese search** | `baidu` · `sogou` · `so360` |
| **Code / dev** | `github` (`github_search`) · `stackoverflow` · `hackernews` · `npm` (`npm_search`) · `devto` |
| **AI / research** | `huggingface` · `arxiv` |
| **Knowledge** | `wikipedia` · `wikivoyage` · `pubmed` · `wolfram` |
| **Forums / community** | `reddit` · `reddit_subreddit` · `quora` · `blackhatworld` · `producthunt` |
| **Social — global** | `twitter` / `x` · `instagram` |
| **Social — Chinese** | `zhihu` · `weibo` · `xiaohongshu` · `douyin` · `toutiao` · `bilibili` |
| **Western news** | `bbc` · `guardian` · `reuters` · `apnews` · `cnn` · `npr` · `aljazeera` · `techcrunch` · `verge` · `arstechnica` |
| **Video / streaming** | `youtube` · `twitch` · `netflix` · `tiktok` |
| **Audio / podcasts** | `spotify` · `soundcloud` · `apple_podcasts` · `xiaoyuzhou` |
| **Movies & books** | `imdb` · `goodreads` |
| **Long-form** | `medium` |
| **E-commerce** | `amazon` · `ebay` · `icecat` · `steam` |
| **Jobs & local** | `linkedin_jobs` · `indeed` · `yelp` |
| **Patents & security** | `google_patents` · `virustotal` |
| **Archive & files** | `archive_org` · `torrent_1337x` |
| **Images** | `unsplash` · `pixabay` · `pexels` · `pinterest` |
| **📣 Ads — competitive research** 🆕 | `meta_ad_library` (aliases: `fb_ads` `meta_ads`) · `instagram_ad_library` · `tiktok_creative_center` (aliases: `tt_ads` `ttcc`) · `tiktok_ad_library` · `google_ad_transparency` (alias: `g_ads`) |
| **📚 Developer docs** 🆕 | `fb_docs` (Meta only, 16 product slugs) · `dev_docs` (alias: `docs`, 142 presets) — see `list_dev_docs_platforms` for the full preset list |
| **📱 App stores** 🆕 | use `search_app` / `lookup_app` (not `search`) — Apple App Store + Google Play |

Run `list_engines` to see the live count + categories + `engine_options` examples.

---

## 📚 Developer Docs Engine — 142 Platforms

The `dev_docs` engine is a DDG site-search wrapper with 142 curated platform presets across 15 categories. Each preset maps to one or more documentation hosts; multi-host presets fan out across them.

### Preset categories (call `list_dev_docs_platforms`)

| Category | Sample aliases |
|---|---|
| **Cloud / Infra** | `aws` · `gcp` · `azure` · `docker` · `kubernetes` · `terraform` · `github` · `gitlab` · `cloudflare` · `vercel` · `netlify` |
| **APIs / SaaS** | `stripe` · `twilio` · `slack` · `discord` · `shopify` · `supabase` · `firebase` · `mongodb` · `redis` · `postgresql` |
| **AI / ML** | `openai` · `anthropic` / `claude` · `huggingface` · `cohere` · `pinecone` · `gemini` / `google-ai` · `langchain` · `llamaindex` |
| **Frontend / Languages** | `mdn` · `react` · `vue` · `angular` · `svelte` · `nextjs` · `nodejs` · `python` · `typescript` · `rust` · `go` |
| **Mobile dev** | `android` · `apple` / `ios` · `swift` · `flutter` · `react-native` · `expo` |
| **Social platforms** | `tiktok` · `tiktok-business` · `tiktok-marketing` · `snap` · `twitter` / `x` · `pinterest` · `reddit` · `linkedin` |
| **Messaging / Chat** | `whatsapp` · `whatsapp-cloud` · `telegram` · `telegram-bot` · `messenger` · `line` · `viber` · `wechat` · `kakao` |
| **Meta megasite** | `meta` · `facebook` · `instagram` · `messenger` · `threads` · `whatsapp` (also: `fb_docs` engine for narrow product slugs) |
| **Google products** | `firebase` · `google-ads` · `google-analytics` · `google-maps` · `google-pay` · `youtube` · `gemini` |
| **Mobile analytics / attribution / ad-intel** | `data.ai` / `appannie` · `sensortower` · `appsflyer` · `appsflyer-performance-index` · `appsflyer-benchmarks` · `adjust` · `branch` · `applovin` / `applovin-max` · `bigspy` · `similarweb` · `admiral` · `businessofapps` · `qimai` / `七麦` · `diandian` / `点点数据` |
| **Browsers** | `chrome` · `webkit` |
| **DevOps / Observability** | `datadog` · `grafana` · `prometheus` · `sentry` · `opentelemetry` |
| **Identity** | `auth0` · `okta` · `clerk` |
| **Workspace** | `notion` · `airtable` · `linear` |
| **ML training infra** | `wandb` · `mlflow` · `ray` |

### How to call

```jsonc
// MCP
search(
  query="subscription webhook",
  engine="dev_docs",
  engine_options={"platform": "stripe", "mode": "reference"},
  limit=5,
  depth=3   // also extracts top 3 markdown bodies in one call
)

// CLI
agentsearch search "subscription webhook" -e docs --platform stripe --mode reference --limit 5
```

When DDG returns 0 results (residential IP throttle), the engine automatically falls back to Brave then Bing. `last_status['backend']` reports which produced results.

---

## 📣 Ad Libraries — Competitive Research

Five engines cover the four biggest paid platforms:

| Engine | Aliases | Modes / `engine_options` |
|---|---|---|
| `meta_ad_library` | `fb_ads`, `meta_ads` | `mode`: `keyword` (default) / `advertiser` (page_id=) / `page_url` (page_url=); `country`, `active_status`, `ad_type`, `media_type`, `publisher_platforms` |
| `instagram_ad_library` | `ig_ads`, `instagram_ads` | Same as Meta but locked to Instagram placement |
| `tiktok_creative_center` | `tt_ads`, `ttcc` | 19 modes — `top_ads` / `top_ads_spotlight` / `keyword_insights` / `creative_insights` / `top_products` / `trending_hashtags` / `hashtag_analytics` / `trending_songs` / `song_analytics` / `trending_creators` / `trending_videos` / `ad_analytics` / `ad_keyframe` / `ad_percentile` / `ad_recommend` / … (6 public, 13 auth-required) |
| `tiktok_ad_library` | `tiktok_ads` | EU/UK only DSA library |
| `google_ad_transparency` | `g_ads` | 4 modes — `search_advertisers` / `domain` / `advertiser_ads` / `creative_detail`; needs `region` |

### Recipes

```jsonc
// 1. All Meta ads run by Shopify
search(query="Shopify", engine="meta_ad_library",
       engine_options={"country": "US", "active_status": "active"}, limit=20)

// 2. Same but precise (page_id-canonical)
search(query="", engine="meta_ad_library",
       engine_options={"mode": "advertiser", "page_id": "20409006880"}, limit=20)

// 3. Google ads from a domain
search(query="shopify.com", engine="google_ad_transparency",
       engine_options={"mode": "domain", "domain": "shopify.com", "region": "US"})

// 4. Top TikTok e-commerce ads, last 30 days
search(query="", engine="tiktok_creative_center",
       engine_options={"mode": "top_ads", "country": "US",
                       "industry": "ecommerce", "period": 30}, limit=20)

// 5. End-to-end: app URL → ads on every platform
find_competitor_ads(
  app_url="https://apps.apple.com/us/app/shopify/id371294472",
  platforms=["meta", "instagram", "google", "tiktok"],
  limit_per_platform=10,
  precise=true,   // resolve developer name → Facebook page_id first
)

// 6. Then download every creative
download_ad_media(
  records=<results from #1 or #5>,
  output_dir="./swipe_file",
  max_per_record=1,   // highest-quality only
  max_workers=4,
)
```

**Tip:** Google ATC under residential proxy may need raw-HTTP transport. `GoogleAdTransparencyEngine.raw(proxy_url=…)` (CLI: implicit). For all five engines see [the Ad-Intelligence section in README](../../README.md#-ad-intelligence).

---

## 📱 App Store Search

Two dedicated tools (don't go through the generic `search` — they have richer metadata schemas):

```jsonc
// Find every Shopify-themed app on iOS + Google Play
search_app(query="shopify", store="all", limit=20, with_contact=true)

// Single app metadata
lookup_app(app_url="https://apps.apple.com/us/app/shopify/id371294472")
// → {store, app_id, bundle_id, title, developer_name, website, domain,
//    category, rating, rating_count, description, icon_url, screenshots,
//    support_email, privacy_url, ...}
```

`search_app` results are normalised across both stores; pass each row's URL into `find_competitor_ads` to chain into a full creative-research workflow.

---

## Quick Recipes

### R1 — Generic web search
```jsonc
search(query="what the user asked", engine="duckduckgo", limit=5)
```

### R2 — Code / docs lookup (with engine_options for dev_docs)
```jsonc
search(query="subscription webhook", engine="dev_docs",
       engine_options={"platform": "stripe", "mode": "reference"})
search(query="kubernetes ingress controller", engine="github", limit=5)
```

### R3 — Latest research
```jsonc
search(query="transformer scaling laws", engine="arxiv", limit=5,
       engine_options={"sort_by": "submittedDate"})
```

### R4 — Discussion / opinions
```jsonc
search(query="best linux laptop 2026", engine="reddit",
       engine_options={"sort": "top", "time": "month"}, limit=5)
```

### R5 — Video / how-to
```jsonc
search(query="react hooks tutorial", engine="youtube",
       engine_options={"upload_date": "this_year", "sort_by": "view_count"}, limit=10)
```

### R6 — Chinese platforms
```jsonc
search(query="机器学习", engine="zhihu", limit=5)
search(query="旅行攻略", engine="xiaohongshu", limit=5)
```

### R7 — Search → read top N markdown in ONE call
```jsonc
search(query="transformer scaling laws", engine="arxiv",
       limit=5, depth=3)
// Top 3 results have body_markdown attached
```

### R8 — Read a list of URLs (after a search)
```jsonc
extract_many(urls=[r["url"] for r in search_result["results"][:5]],
             paginate=true, max_scrolls=2)
```

### R9 — Find competitor ads from an App Store URL
```jsonc
find_competitor_ads(app_url="…", precise=true, limit_per_platform=10)
```

### R10 — Browse 142 dev_docs preset platforms
```jsonc
list_dev_docs_platforms(filter_substring="appsflyer")
list_dev_docs_platforms(category="mobile_ad_intel")
```

### R11 — Bulk-download competitor ad creatives
```jsonc
ads = search(query="Shopify", engine="meta_ad_library", limit=50)
download_ad_media(records=ads["results"], output_dir="./swipe_file",
                  max_per_record=1)
```

### R12 — Login once for walled sites (CLI only — sessions persist for `search`/`extract`)
```bash
agentsearch login twitter
agentsearch login linkedin
agentsearch login glassdoor      # custom site → pass --url

# Use the saved session in any follow-up search
agentsearch search "from:elonmusk AI" --engine twitter --profile twitter --limit 10
```

| Site | Why login |
|---|---|
| `twitter` / `x` | Public API gone; logged-in user gets full feed |
| `linkedin` | Profile pages require login since 2024 |
| `glassdoor` | Reviews / salaries paywalled |
| `instagram` / `facebook` | Most content requires login |
| `discord` / `medium` / `quora` | Members-only content |

---

## CLI Reference (when MCP isn't available)

The MCP tools above also exist as CLI subcommands:

```bash
# Activate the venv first
source ~/tools/cloakbrowser/venv/bin/activate
cd ~/projects/AgentSearch

# Generic search
agentsearch search "X" -e <engine> --limit 5 --json
agentsearch search "X" -e dev_docs --platform stripe --mode reference --json

# Multi-engine fan-out (parallel, URL-deduped)
agentsearch search-many "X" --engines duckduckgo,hackernews,github --merged --json

# Health-aware fallback chain
agentsearch search "X" -e google --fallback --json
agentsearch status   # show engine health table

# Extract one URL
agentsearch extract "https://example.com" --json

# App Store
agentsearch app-search "shopify" --store all --with-contact --json

# Ad libraries (dedicated commands; richer than raw engine search)
agentsearch ads "fitness app" --filter country=US --filter active=true
agentsearch ads-by-app "https://apps.apple.com/.../id<NUM>" --precise --json
agentsearch ads-batch competitors.txt --workers 4 --proxy-pool proxies.txt
agentsearch ads-download ads.jsonl --output-dir ./swipe_file --max-per-record 1
```

---

## Engine-Selection Heuristics

When the user wasn't specific:

1. **Generic factual question** → `duckduckgo` (most reliable, no consent dialog)
2. **Latest news / current events** → `google`
3. **Code error / programming question** → `stackoverflow` first, then `github`
4. **Developer documentation** → `dev_docs` with the right `platform`
5. **Academic / scientific** → `arxiv` for ML/CS, `pubmed` for medical
6. **Open-source library** → `github`, supplement with `npm` for JS
7. **Discussion / "what do people think"** → `reddit`, fallback `hackernews`
8. **Video tutorial** → `youtube`. Chinese → `bilibili`
9. **Shopping** → `amazon` for new, `ebay` for used
10. **Restaurant / local business** → `yelp`
11. **Picture / mood board** → `unsplash` (hi-res free) or `pinterest`
12. **Movie / TV info** → `imdb`. Book → `goodreads`. Podcast → `apple_podcasts` / `xiaoyuzhou`
13. **Patent prior-art** → `google_patents`
14. **File hash / virus scan** → `virustotal`
15. **Chinese-language query** → `baidu` or `zhihu` over `google` for recall
16. **Competitor ad research** → start with `find_competitor_ads`; drop to individual ad-library engines for narrow modes
17. **App-store metadata** → `lookup_app` (single) or `search_app` (keyword)

---

## Output Format

`search` returns:

```json
{
  "engine": "google",
  "query": "open source software",
  "count": 5,
  "results": [
    {
      "title": "...",
      "url": "...",
      "snippet": "...",
      "score": null,
      "body_markdown": "..."   // present when depth > 0
    }
  ]
}
```

Engine-specific extras on each result (use them when relevant):

| Engine | Extra fields |
|---|---|
| `youtube` | `video_id`, `channel`, `views`, `duration`, `upload_date` |
| `imdb` | `imdb_id`, `year`, `content_type`, `runtime`, `imdb_rating`, `vote_count` |
| `goodreads` | `goodreads_id`, `author`, `avg_rating`, `rating_count`, `image_url` |
| `arxiv` | `arxiv_id`, `authors`, `categories`, `published`, `pdf_url` |
| `huggingface` | `model_id`, `author`, `downloads`, `likes`, `pipeline_tag`, `tags` |
| `reddit_subreddit` | `score`, `num_comments`, `author`, `created_utc` |
| `amazon` / `ebay` | `price`, `rating`, `condition`, `shipping`, `seller` |
| `unsplash` / `pixabay` / `pexels` | `image_url`, `photographer`, `alt_text` |
| `apple_podcasts` | `track_id`, `artist`, `genre`, `feed_url`, `release_date` |
| `dev_docs` / `fb_docs` | `doc_site`, `doc_section`, `platform`, `product`, `api_version` |
| `meta_ad_library` / `instagram_ad_library` | `ad_archive_id`, `page_id`, `page_name`, `start_date`, `end_date`, `eu_total_reach`, `image_urls`, `video_url`, `cover_image_url`, `creatives[]`, `link_url`, `body_text`, `cta`, `ad_type`, `publisher_platforms` |
| `tiktok_creative_center` | `creative_id`, `industry`, `country_code`, `like_count`, `share_count`, `comment_count`, `cover_image_url`, `video_url`, `duration`, `tags`, `metric_value` |
| `google_ad_transparency` | `creative_id`, `advertiser_id`, `advertiser_name`, `domain`, `format`, `region`, `first_seen`, `last_seen`, `image_url`, `video_url`, `youtube_video_id` |

---

## Privacy Guarantee

- 🔒 **100% local** — Chromium runs on the user's machine; results parsed locally
- 🚫 **Zero data leakage** — no queries sent to any third-party API or cloud service
- 🔑 **No API keys** — no accounts, sign-ups, or authentication tokens
- 📊 **No telemetry** — zero tracking, zero analytics, zero usage monitoring
- 💰 **100% free** — no subscriptions, no rate limits

The only network traffic is the direct request from the user's machine to the target site (e.g. `google.com/search?q=…`, `docs.stripe.com`, `meta.com/ads/library`). Nothing else.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| Google CAPTCHA / sorry interstitial | Switch `engine="duckduckgo"` for the next 30 min |
| `dev_docs` empty results | DDG throttle — engine auto-falls-back to Brave/Bing; retry |
| `meta_ad_library` keyword too broad | Run `lookup_pages` first to get a canonical `page_id`, then `mode="advertiser"` |
| Google ATC blocks navigation | Pass `proxy_url`; raw-HTTP transport kicks in automatically under proxy |
| App Store URL not resolving | Confirm the URL contains `/id<NUM>` (Apple) or `?id=<PKG>` (Google Play) |
| `extract` returns thin content | Set `paginate=true, max_scrolls=3` to surface lazy content |
| Chinese site returns 0 hits | Site likely walled — adapter auto-falls-back to Google/Bing/DDG with `site:` filter |
| Browser hangs | Add `--no-headless` (CLI) or `AGENTSEARCH_HEADLESS=0` (MCP) to debug visually |

---

*One skill, the whole web — local, free, no API keys.*
*80+ engines · 142 dev-docs platforms · 5 ad libraries · App Store search · 9 MCP tools.*
