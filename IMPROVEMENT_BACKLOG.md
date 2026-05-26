# AgentSearch — Improvement Backlog

> Distilled from a real-world pain-point sweep across HN / Reddit / GitHub
> on 2026-05-21. Goal: make AgentSearch the unambiguous best free,
> local, no-API-key web-search layer for AI agents.
>
> Status legend: ✅ done · 🚧 in progress · ⏳ pending

---

## 1. Field-validated pain points

| # | Pain point | Source |
|---|---|---|
| 1 | **GitHub 60 req/h unauthenticated rate limit** triggers after 2-3 file views; ticketing system itself is rate-limited | HN [#43936992](https://news.ycombinator.com/item?id=43936992) — "When unauthenticated, code search doesn't work at all and issue search stops working after like, 5 clicks at best." |
| 2 | **Brave Search API TOS forbids AI inference use** — but every tutorial wires it into Cursor/Cline/OpenClaw anyway | HN [#46822822](https://news.ycombinator.com/item?id=46822822) |
| 3 | **Search API prices going up while LLMs get 1000× cheaper**: Bing $15/1k, Brave $9/1k, Gemini grounding $35/1k | HN [#43921238](https://news.ycombinator.com/item?id=43921238) |
| 4 | Free tiers are unusable: Brave 2k/mo @ 1 TPS, Bing 1k/mo, Kagi 100 total | same |
| 5 | **CAPTCHA / reCAPTCHA blocks AI agent browsers** repeatedly: Vercel agent browser, Mercadona, banks 2FA | r/openclaw 87-use-cases (90 pts) |
| 6 | **Claude Code's WebFetch can't access Reddit etc.** — community workaround is tmux + gemini-cli | r/ClaudeAI 552 pts, Tip 11 |
| 7 | **Local LLM users stuck**: SearXNG free but low quality; Tavily/Jina good but paid | r/LocalLLaMA 306 pts |
| 8 | **OpenClaw's high-frequency tasks are all open-web research** — products, doctors, domain bulk-check, shopping | r/openclaw 87-use-cases |

---

## 2. Competitor landscape

| Project | ⭐ | Model | Engines | Pitch |
|---|---|---|---|---|
| firecrawl/firecrawl-mcp-server | 6353 | **Paid SaaS** | 1 | Already integrated in Cursor/Claude |
| exa-labs/exa-mcp-server | 4459 | **Paid SaaS** | 1 (neural) | High-quality search |
| Aas-ee/open-webSearch | 1265 | Local + MCP | ~7 | **Closest direct competitor** — multi-engine, no API key, skill-guided |
| nickclyde/duckduckgo-mcp-server | 1169 | Local + MCP | 1 | Simple |
| mrkrsl/web-search-mcp | 871 | Local + MCP | ~3 | For local LLMs |
| Shelpuk/kindly-web-search-mcp | 331 | Local + MCP | 3 (Serper/Tavily/SearXNG) | Explicit OpenClaw support |
| **AgentSearch (us)** | — | Local CLI + Skill + MCP | **71** | Most engines + CloakBrowser stealth + engine-specific fields |

**Our moat once P0 ships**: 71 engines vs 1-7, real Chromium with C++ stealth patches vs HTTP scrapers, engine-specific structured fields (IMDB rating, arXiv categories, YouTube views), and we don't touch any third-party API so we're TOS-clean for AI agent use.

---

## 3. Backlog

### 🔥 P0 — ship this week

| # | Item | Status | Files | Notes |
|---|---|---|---|---|
| P0-1 | Fix reddit `BLOCK_PHRASES` false positives | ✅ | `engines/reddit.py` | Was scanning `body[:2000]` which echoes the user's query. Now: result-container check first → URL fragments → title-only phrases → narrow `.error/.interstitial` selectors. Verified with the previously broken query "GitHub API rate limit AI agent coding" (5 hits returned). |
| P0-2 | `extract --json` with readability + auto-paginate | ✅ | `extract.py` (new), `cli.py`, `pyproject.toml` | New module uses **trafilatura 2.0** for Markdown + metadata. Auto-scrolls + clicks "Load more" buttons. CLI flags: `--json`, `--format`, `--no-paginate`, `--max-scrolls`, `--max-load-more`, `--no-links`, `--no-images`. Verified on HN #43936992 → 7936-word Markdown with date "2025-05-09". |
| P0-3 | MCP server wrapper | ✅ | `mcp_server.py` (new), `tests/test_mcp_server_smoke.py` | FastMCP server exposing `search` / `extract` / `list_engines` as **async** tools (sync Playwright wrapped in `asyncio.to_thread`). `BrowserPool` singleton, recycles every 25 calls. End-to-end JSON-RPC smoke test passes initialize → tools/list → list_engines → search. |
| P0-4 | README rewrite — competitor table + MCP install | ✅ | `README.md` | TL;DR shows `extract` + `mcp_server`; full 8-column comparison table with concrete prices; full "🔌 Use as an MCP Server" section with Claude Desktop / Cursor / Cline / OpenClaw configs; new "📰 Extract a URL as clean Markdown" recipe. |

### 🚀 P1 — next 1-2 weeks

| # | Item | Status | Files | Notes |
|---|---|---|---|---|
| P1-5 | `search-many` parallel multi-engine fan-out | ✅ | `multi.py` (new), `cli.py` | One thread per engine, each owns its own browser. Returns `{per_engine, merged}`. URL-dedup with engine-consensus signal. **2.3× speedup measured** (3 engines: 5.0s parallel vs ~11.7s sequential). |
| P1-6 | Engine health log + auto-fallback chain | ✅ | `health.py` (new), `cli.py` | JSON sliding-window log at `~/.cache/agentsearch/health.json`; `search_with_fallback()` re-orders fallback by health score. CLI: `search --fallback`, `cloak status`. |
| P1-7 | Extract: readability + paginate | ✅ | (folded into P0-2) | Done in `extract.py`. |
| P1-8 | Deep-fetch for reddit / hackernews | ✅ | `cli.py`, `extract.py`, `mcp_server.py` | `search --depth N` returns top-N hits with `body_markdown` inline. |
| (doc) | Update SKILL.md to match implementation | ✅ | `skills/agent-search/SKILL.md` | Recipe 12-14 + MCP Server Mode section. |
| (test) | Run / fix existing stress tests | ✅ | (verified) | reddit / hackernews / duckduckgo / core tests all PASS. |

### 🔧 P1.5 — strategic hot list (next, ordered by leverage)

These came out of the 2026-05-21 follow-up sweep on "rigid demand × hard to scrape" sites
and the realization that CloakBrowser already supports `launch_persistent_context()`.

| # | Item | Status | Why now |
|---|---|---|---|
| P1.5-A | **CloakBrowser persistent profile + `agentsearch login` command** | ✅ | Done. Added `BrowserConfig.user_data_dir`, `core.profile_path()`, `agentsearch login <site>` (headed window → user logs in → press Enter to save), and `--profile <name>` flag on search/extract. Verified: persistent context launches as `BrowserContext`, navigation works, profile dir contains 13 files after close. Default URLs for twitter/x/linkedin/instagram/facebook/reddit/glassdoor/discord/github/medium/quora/weibo/zhihu/bilibili/xiaohongshu/douyin. Non-existent profile → graceful warn + anonymous fallback. |
| P1.5-B | **Brand rename: `cloak` → `agentsearch`** | ✅ | Done in `df0115c`. Package `cloak_stealth_suite` → `agent_search`, CLI `cloak` → `agentsearch`, `pyproject` version 0.1.0 → 1.0.0. All tests still PASS. |
| P1.5-C | **Google Maps adapter** | ✅ | Done. New `agent_search/engines/google_maps.py` — handles consent gate, scrolls the inner `[role="feed"]` panel, parses `[role="article"]` cards via aria-labels (resilient to Google's class mangling). Returns name/url/rating/review_count/address/category/phone/website. Verified: "coffee shops san francisco" returns 5 places with correct ratings + categories + addresses. Phone/website/review_count not in feed cards (need place-page click-through, deferred to v2). |
| P1.5-D | **Jobs aggregator (`agentsearch jobs ...`)** | ✅ | Done. New CLI bundle subcommands: `jobs` (linkedin_jobs+indeed+ziprecruiter+glassdoor), `research` (ddg+google+reddit+hackernews), `news` (reuters+ap+bbc+guardian+npr), `code` (github+stackoverflow+hackernews). Reuses search_many fan-out + merge logic. New ZipRecruiter and Glassdoor engines added with Cloudflare-aware waits — these have skeletons in place but DOM selectors will need iterative tuning as the sites A/B test their layouts. |
| P1.5-E | **Twitter/X structured improvements** | ⏳ | Existing `twitter.py` already uses Nitter mirrors with fallback; with P1.5-A done, future work is to detect a `--profile twitter` and route through x.com's logged-in advanced search instead of Nitter for richer fields (replies, quote tweets, view counts). Deferred — current Nitter route works for most queries. |
| P1.5-F | **Instagram engine: multi-mode + og-meta enrichment** | ✅ | Big rewrite (2026-05-26). New `mode=` parameter (`hashtag` / `user` / `post` / `keyword` / `auto`); new public `fetch_post()` / `fetch_profile()` methods; SERP fallback chain `Google → DuckDuckGo` (Bing dropped — proven 0 IG hits) with `max_retries=1` per fallback to bound latency. Crucially, the `og:description` parser is also fed the SERP **snippet** so DDG hits like `"1M likes, 2,844 comments - srinidhi_shetty on April 21, 2026: \"...\""` parse straight to structured fields without an extra navigation. Force-scroll-if-empty unblocks IG's selective hydration. **Experiment vs baseline (10 queries)**: 10/10 pass (was 8/10), 5 queries gained likes/comments/posted_at via DDG snippet (was 0/10), `post` mode gives full likes/comments/date/image in 6-8s (structurally impossible before), `user` mode gives 269M followers + 32K posts via og even when the SPA grid never hydrates. Data: `tests/instagram_baseline.json` vs `tests/instagram_enhanced.json`. New tests: `tests/test_instagram.py` (multi-mode regression, 3/3 PASS), `tests/probe_instagram_*.py` (probes). Logged-in `keyword` mode is wired (`_search_logged_in_keyword` + `sessionid` cookie sniff) and degrades gracefully to hashtag flow when no `--profile instagram` is set; full validation deferred until a profile is captured. |
| P1.5-G | **Instagram: extract media URLs + sidecar + full caption from page JSON** | ✅ | (commit `738daee`, 2026-05-26). Researched **instaloader (12.4k stars)** as the gold-standard reference for guest IG scraping. Their key technique: GraphQL `doc_id=8845758582119845` POST returns the same `xdt_api__v1__media__shortcode__web_info.items[0]` payload that the post detail page **embeds inside `<script type="application/json">` blobs** for SSR. We walk those scripts (no GraphQL POST needed — fewer signals to IG's bot detection) and extract: exact `like_count` / `comment_count` (not the rounded "3M" og approximation), full untruncated caption, `image_versions2.candidates[]` (every resolution), `video_versions[]` (3 quality .mp4 direct links), `carousel_media[]` (sidecar children), `taken_at` unix ts, `media_id` (pk), `play_count` / `view_count` / `video_duration`. Stamped on SearchResult as `image_url` / `image_urls` / `video_url` / `video_urls` / `sidecar` / `media_count` / etc. Verified on 3 reels (DTfS7SMEk8B → 3,146,569 likes, 5,789 comments, 11 image_urls, 3 video_urls; DSI7LxnEbrb → 18M likes; DW14TyKjKya → 3.6M likes). Falls back to `og:description` when the Relay payload is missing. New probes: `tests/probe_instagram_dom.py`, `tests/probe_instagram_dump.py`. |
| P1.5-H | **YouTube engine: video / channel / transcript modes via ytInitialData** | ✅ | (commit `f4a1a13`, 2026-05-27). Researched **yt-dlp (165k stars)**, pytube, **jdepoix/youtube-transcript-api (7.6k stars)**. Same architectural pattern as IG: walk `ytInitialPlayerResponse` (videoDetails / microformat / captions) + `ytInitialData` (videoPrimaryInfoRenderer / videoSecondaryInfoRenderer) on the watch page; for channels walk the new 2024+ `pageHeaderRenderer` + `richItemRenderer.content.lockupViewModel` shapes (not the legacy `c4TabbedHeaderRenderer` / `videoRenderer` — YT switched to viewModels for these layouts). Like count requires walking 6 levels deep into `segmentedLikeDislikeButtonViewModel.likeButtonViewModel.likeButtonViewModel.toggleButtonViewModel.toggleButtonViewModel.defaultButtonViewModel.buttonViewModel.title`. **Verified**: VIDEO `jNQXAC9IVRw` → views=392,328,164 (exact int), likes=18M, subscribers=6.14M, duration=19s, captions×2, keywords×3, thumbnails×4, ISO upload_date — 14s end-to-end. CHANNEL `@MrBeast` → subscribers=491M, video_count=982, 30 recent videos with views/duration/upload_date — 10s. TRANSCRIPT mode is wired via `captions[].baseUrl` (timedtext XML), but YouTube's 2024 `pot=` (Proof of Token) requirement now serves empty bodies for many tracks — best-effort, captions metadata always preserved on the result so callers can try fetching themselves. New tests: `tests/test_youtube_modes.py` (3/3 PASS), `tests/probe_youtube_watch.py`. The legacy `mode="search"` (default) is unchanged. |
| P1.5-I | **Reddit engine: post-detail mode via .json endpoint** | ✅ | (commit `ca620fd`, 2026-05-27). Reddit gives us the easiest gift: appending `.json` to **any** URL returns the full official JSON, no OAuth, no API key. Researched **praw-dev/praw (4.1k stars)** to align our extracted fields with their `Submission` schema. New `mode="post"` accepts a full URL / `redd.it/<id>` / bare base36 id, fetches `/comments/<id>.json` via `page.request.get` (no full page render, ~1-2s vs 10-15s for browser scrape), with `old.reddit.com` host fallback when the canonical host throttles. Returns the post + top comments depth-first walk (skips `more` placeholders that need a second XHR), with full media extraction matching PRAW's schema: `image_urls` (i.redd.it direct + html-decoded preview + gallery via `media_metadata`), `video_url` (v.redd.it `fallback_url`, the DASH MP4 direct link), `gallery` (multi-image `[{url, media_id}, ...]`). **Verified end-to-end**: r/Python self post in 1.1s with 1186-char selftext + 5 comments; r/pics gallery with 2 images html-decoded; r/funny v.redd.it post returning `https://v.redd.it/<id>/CMAF_720.mp4?source=fallback`. New test: `tests/test_reddit_post_mode.py` (2/2 PASS). The legacy `mode="search"` (`old.reddit.com/search`) is unchanged. |
| P1.5-J | **Proxy support: HTTP / HTTPS / SOCKS4 / SOCKS5 + rotation pool** | ✅ | (2026-05-27). New `agent_search/proxy.py` (660 lines) — dependency-free `Proxy` dataclass with health metadata, `ProxyPool` with random / round-robin / sticky strategies, dedupe on `(scheme, host, port)`, on-disk cache at `~/.cache/agentsearch/proxies.json`, free-list fetchers from **proxifly** / **roosterkid/openproxylist** / **TheSpeedX/PROXY-List** / **Zaeem20/FREE_PROXIES_LIST** (4 GitHub repos, no API key, refresh windows 5-60min), parallel `test_all` with `ThreadPoolExecutor` for HTTP/HTTPS health checks (SOCKS skipped at this layer — they're verified inside CloakBrowser at use time). `BrowserConfig` extended with `proxy_pool` field; `core.launch()` resolves explicit `proxy=` first, falls back to `pool.next()`, stashes the picked Proxy on `cfg._picked_proxy` for caller-driven `mark_ok` / `mark_fail`. CLI: `--proxy` on `search` / `extract` accepts `URL` / `pool` / `pool:socks5` / `pool:/path/cache.json` / `file:/path/list.txt` via `apply_proxy_spec_to_config()`. New `agentsearch proxies` subcommand with 5 actions: `fetch` (pull GitHub free-lists, supports source name or bundle name), `test` (concurrent live ping against `api.ipify.org` or custom target), `list` (sorted by health score, optional `--json`), `add` (manual entries, dedupe), `clear` (with `--yes` guard). Verified end-to-end: fetch 50 socks5 in <2s, list renders, add accepts URLs and rejects garbage, test runs 20 http proxies in 10s. New test: `tests/test_proxy.py` (12/12 PASS, fully offline via `urlopen` mock). Tagged in module docstring: free-list hit rates are inherently low (most listed proxies dead within minutes); for serious automation users should buy residential pools from Webshare / Bright Data / Oxylabs / IPRoyal and load via `file:/path/list.txt` — same API, much higher hit rate. |

### 🔭 P2 — long-term

These are tracked separately from P1.5 because each one is multi-day and
the value depends on adoption signals from the P0/P1 stack landing first.

| # | Item | Status | Notes |
|---|---|---|---|
| **P2-LT-A** | Twitter/X login enhancement | ✅ | `twitter.py` now detects the `auth_token` cookie via `BrowserContext.cookies()` and routes through x.com/search **before** Nitter when present. Anonymous flow unchanged (still tries Nitter mirrors first). Verified: anon search returns Nitter results; authed path triggers when `--profile twitter` carries an `auth_token`. |
| **P2-LT-B** | LinkedIn adapter | ✅ | New `agent_search/engines/linkedin.py` (284 lines). Detects `li_at` cookie. Authed path uses `/search/results/people/` and parses entity-result cards (name / headline / location / current_company). Anon path falls through to `/pub/dir` with explicit warning. Iterative DOM tuning will be needed as LinkedIn re-mangles class names. |
| **P2-LT-C** | Booking.com hotel adapter | ✅ | New `engines/booking.py` (239 lines). Anonymous SERP works — verified "kyoto" returns 3 hotels with score 9.0-9.3 and 1k-5k reviews. Returns name / url / rating / review_count / price / area / stars. CloakBrowser passes Booking's DataDome challenge cleanly. |
| **P2-LT-D** | Expedia hotel adapter | ⚠️ skeleton | New `engines/expedia.py` (191 lines). Implementation in place but Expedia's DataDome variant returns "Bot or Not?" wall on direct ?destination= queries — needs entry-point flow (visit homepage, type into search box, submit). Marked TODO; bundle excludes it for now. |
| **P2-LT-E** | Skyscanner flights | ⏳ deferred | Skyscanner needs structured airport codes + dates rather than free-text — doesn't fit the SearchResult shape cleanly. Better as a separate `flights` tool than a search engine. |
| **P2-LT-F** | New CLI bundle: `travel` | ✅ | `agentsearch travel "<destination>"` fans out to booking + expedia (expedia returns 0 until P2-LT-D fixed; booking carries the bundle today). |

### 💡 P2 — other backlog

| # | Item | Why it matters |
|---|---|---|
| P2-1 | Persistent browser daemon (`agentsearch-daemon`) | Eliminates the ~0.5-1.5s Chromium startup per CLI call for hot loops |
| P2-2 | Nightly canary CI for all 71 engines | Auto-detect DOM drift; auto-file GitHub issue on regression |
| P2-3 | More AI / dev high-traffic engines | `docs.python.org`, MDN, `crates.io`, HF Spaces, Kaggle, Papers with Code, Phind, Perplexity, OpenReview, Semantic Scholar |
| P2-4 | Optional `serve --http` mode | For team agents running in cloud / docker without local Chromium; self-hosted only — never a managed SaaS |
| P2-5 | Travel sites (rolled into P2-LT-C/D, partial) | Booking ✅, Expedia ⚠️, Skyscanner deferred |
| P2-6 | Financial sites: `tradingview` / `yahoo_finance` | Once P1.5-A login lands, financial-data agents become tractable |
| P2-7 | `chrome-devtools-mcp` interop (Path B, demoted) | Originally proposed as our login path; demoted because P1.5-A (persistent CloakBrowser profile) is strictly better (keeps stealth). Still useful for "use my CURRENT logged-in Chrome" zero-friction case — keep as opt-in alternative. |
| P2-8 | Launch blog → Show HN → r/LocalLLaMA / r/ChatGPTCoding / r/openclaw | Direct line to the three HN viral pain-point posts (#43936992, #43921238, #46822822). Soft-recommend in firecrawl/exa MCP issue threads when users complain about cost. |
| P2-9 | **Instagram: validate `keyword` mode end-to-end with a saved profile** | Code path is in `_search_logged_in_keyword` and gates on `sessionid` cookie. Once an `instagram` profile is captured via `agentsearch login instagram`, run probes to confirm the SPA's Top/Accounts/Tags/Places anchors are still walked correctly (DOM may have drifted since the spec was written). |
| P2-10 | **Instagram: per-IP rate-limit awareness** | Direct hashtag path is the fastest (6-12s, 6 results) but IP gets throttled after ~10-20 hits, after which we fall through to the SERP chain (1-3 results). Track per-IP success rate in `health.json` and prefer DDG-snippet fallback after a streak of `goto_failed`/`grid_empty` so we don't waste 30s on the homepage warm-up + tag goto. |

---

## 4. Recommended execution order

```
✅ Day 1     P0-1 reddit block-phrase bug          (~30 min, done)
✅ Day 1     P0-2 + P1-7 extract --json + readability  (~4h, done)
✅ Day 2-3   P0-3 MCP server + smoke test          (~2 days work, done)
✅ Day 3     P0-4 README rewrite                   (~1h, done)
✅ Day 4     P1-5 search-many fan-out              (~1 day, done)
🚧 Day 4-5   P1-6 health log + --fallback          (module done, CLI wiring next)
⏳ Day 6     P1-8 deep-fetch reddit/HN
⏳ Day 7     SKILL.md alignment + stress test pass
   Week 4    Launch blog → Show HN → community posts
```

After P0-P1 ships:

- ✅ Plug-and-play in Cursor / Cline / Claude Desktop / OpenClaw / Continue / Roo Code
- ✅ 71-engine breadth publicly marketed against 1-7-engine competitors
- ✅ Self-bug fixed (reddit block-phrase), self-doc fixed (extract --json)
- ✅ Direct response to the three biggest HN pain-point threads of 2026

---

## 5. Files modified so far

```
agent_search/
  cli.py                    ← extract subcommand rewritten, search-many added,
                              search adds --fallback (in progress)
  engines/reddit.py         ← _is_blocked() rewritten with multi-signal logic
  extract.py                ← NEW (369 lines)
  mcp_server.py             ← NEW (289 lines)
  multi.py                  ← NEW (233 lines)
  health.py                 ← NEW (275 lines, CLI wiring in progress)
README.md                   ← TL;DR + competitor table + MCP install + extract recipe
pyproject.toml              ← +trafilatura>=2.0, +mcp optional-deps extra
tests/test_mcp_server_smoke.py  ← NEW (108 lines)
IMPROVEMENT_BACKLOG.md      ← this file
```
