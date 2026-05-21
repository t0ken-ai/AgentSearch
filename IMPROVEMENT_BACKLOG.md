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
| P1-6 | Engine health log + auto-fallback chain | 🚧 | `health.py` (new, written), `cli.py` (CLI wiring in progress) | JSON sliding-window log at `~/.cache/agentsearch/health.json`; `search_with_fallback(query, primary, chain)` re-orders fallback by health score. Module is done; finishing `--fallback` flag + `cloak status` command. |
| P1-7 | Extract: readability + paginate | ✅ | (folded into P0-2) | Done in `extract.py`. |
| P1-8 | Deep-fetch for reddit / hackernews | ⏳ | `engines/reddit.py`, `engines/hackernews.py` | Add optional `with_full_text=True` so the engine fetches the top K comment threads / submission bodies in one call. Eliminates follow-up `extract` calls. |
| (doc) | Update SKILL.md to match implementation | ⏳ | `skills/agent-search/SKILL.md` | Add new MCP / search-many / extract flags / fallback recipes. |
| (test) | Run / fix existing stress tests | ⏳ | `tests/test_*_stress.py`, etc. | Make sure none of the changes regressed reddit / google / hackernews / extract. |

### 💡 P2 — backlog

| # | Item | Why it matters |
|---|---|---|
| P2-1 | Persistent browser daemon (`agentsearch-daemon`) | Eliminates the ~0.5-1.5s Chromium startup per CLI call for hot loops |
| P2-2 | Nightly canary CI for all 71 engines | Auto-detect DOM drift; auto-file GitHub issue on regression |
| P2-3 | More AI / dev high-traffic engines | `docs.python.org`, MDN, `crates.io`, HF Spaces, Kaggle, Papers with Code, Phind, Perplexity, OpenReview, Semantic Scholar |
| P2-4 | Optional `serve --http` mode | For team agents running in cloud / docker without local Chromium; self-hosted only — never a managed SaaS |
| P2-5 | Launch blog → Show HN → r/LocalLLaMA / r/ChatGPTCoding / r/openclaw | Direct line to the three HN viral pain-point posts (#43936992, #43921238, #46822822). Soft-recommend in firecrawl/exa MCP issue threads when users complain about cost. |

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
cloak_stealth_suite/
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
