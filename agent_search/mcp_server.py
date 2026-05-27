"""MCP (Model Context Protocol) server wrapper for AgentSearch.

Exposes three tools to any MCP-compatible client (Claude Desktop, Cursor,
Cline, Continue, OpenClaw, Roo Code, Zed, ...):

  * ``search``        — query one of 71+ search engines, return SERP hits
  * ``extract``       — fetch a URL and return readability-extracted markdown
  * ``list_engines``  — enumerate available search engines

The server keeps a single CloakBrowser instance alive for the lifetime of
the process so each tool call doesn't pay the ~0.5-2s Chromium startup
cost. The browser is recycled lazily after a configurable number of
calls (the page state otherwise drifts — cookies pile up, JS world gets
polluted, etc.).

Run with::

    python -m agent_search.mcp_server

Configure in Claude Desktop's ``claude_desktop_config.json``::

    {
      "mcpServers": {
        "agent-search": {
          "command": "/path/to/venv/bin/python",
          "args": ["-m", "agent_search.mcp_server"]
        }
      }
    }
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from typing import Any

from mcp.server.fastmcp import FastMCP

from .core import BrowserConfig, launch, new_page
from .cli import _engine_registry, _get_engine
from .extract import extract_page

log = logging.getLogger(__name__)

# How many tool calls to serve from the same Chromium before we recycle.
# Keeping a single instance forever causes cookie/JS-world drift on some
# sites; recycling every N calls keeps things fresh without paying the
# startup cost for every request.
RECYCLE_AFTER = int(os.environ.get("AGENTSEARCH_RECYCLE_AFTER", "25"))

# Default headless mode. Override with AGENTSEARCH_HEADLESS=0 for debugging.
HEADLESS = os.environ.get("AGENTSEARCH_HEADLESS", "1") != "0"


class BrowserPool:
    """Lazy, thread-safe singleton browser with periodic recycling.

    The MCP runtime serializes tool calls through stdio so concurrent
    access is rare, but we still guard with a lock so the server can
    safely be wrapped in an HTTP/SSE transport later.
    """

    def __init__(self) -> None:
        self._browser = None
        self._calls = 0
        self._lock = threading.Lock()

    def _start(self) -> None:
        log.info("[mcp] launching browser (headless=%s)", HEADLESS)
        self._browser = launch(BrowserConfig(headless=HEADLESS, humanize=True))
        self._calls = 0

    def _maybe_recycle(self) -> None:
        if self._calls >= RECYCLE_AFTER and self._browser is not None:
            log.info("[mcp] recycling browser after %d calls", self._calls)
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None

    def page(self):
        """Return a fresh page bound to the live browser."""
        with self._lock:
            self._maybe_recycle()
            if self._browser is None:
                self._start()
            self._calls += 1
            return new_page(self._browser)

    def shutdown(self) -> None:
        with self._lock:
            if self._browser is not None:
                try:
                    self._browser.close()
                except Exception:
                    pass
                self._browser = None


_pool = BrowserPool()
mcp = FastMCP("agent-search")


# ---------------------------------------------------------------------- tools


@mcp.tool()
async def search(
    query: str,
    engine: str = "duckduckgo",
    limit: int = 10,
    depth: int = 0,
) -> dict[str, Any]:
    """Search the live web through one of 71+ stealth-browser engines.

    Use this whenever you need fresh content that isn't in your training
    data — Google / Bing / DuckDuckGo for general queries, ``reddit``
    for opinions, ``stackoverflow`` for code errors, ``arxiv`` for
    research, ``github`` for repositories, ``youtube`` for video,
    ``bilibili`` / ``zhihu`` / ``xiaohongshu`` for Chinese content, and
    so on. Call ``list_engines`` to see every available engine.

    Args:
        query: Search query string. Supports the engine's native syntax.
        engine: Engine handle (e.g. ``google``, ``reddit``, ``arxiv``).
            Defaults to DuckDuckGo because it's the most rate-limit-free.
        limit: Max results to return (default 10, hard ceiling 50).
        depth: When > 0, also fetch and readability-extract the top N
            result URLs inline. Each result gets ``body_markdown`` /
            ``body_text`` / ``body_word_count`` fields attached. Saves
            the agent N follow-up ``extract`` calls. Default 0 (SERP only).

    Returns:
        A dict with ``engine``, ``query``, ``count``, and ``results`` —
        each result has at least ``title``, ``url``, ``snippet``, plus
        engine-specific extras (e.g. ``score``, ``video_id``,
        ``arxiv_id``, ``imdb_rating``, ``price``). When ``depth > 0``,
        the first N results also have ``body_markdown`` /
        ``body_word_count``.
    """
    limit = max(1, min(limit, 50))
    depth = max(0, min(depth, limit))
    try:
        engine_cls = _get_engine(engine)
    except ValueError as e:
        return {
            "engine": engine,
            "query": query,
            "error": str(e),
            "count": 0,
            "results": [],
        }

    def _run() -> list[Any]:
        page = _pool.page()
        try:
            instance = engine_cls(page)
            results = instance.search(query, limit=limit) or []
        finally:
            try:
                page.close()
            except Exception:
                pass

        # Inline deep-fetch so the agent's tool result already has the
        # body markdown — saves a round-trip per top hit.
        if depth > 0 and results:
            for r in results[:depth]:
                if not getattr(r, "url", None):
                    continue
                ep = _pool.page()
                try:
                    rec = extract_page(
                        ep,
                        url=r.url,
                        paginate=True,
                        max_scrolls=2,
                        include_links=False,
                        include_images=False,
                    )
                    r.__dict__["body_markdown"] = rec.get("content_markdown") or ""
                    r.__dict__["body_word_count"] = rec.get("word_count") or 0
                    if rec.get("date") and not getattr(r, "date", None):
                        r.__dict__["date"] = rec["date"]
                    if rec.get("author") and not getattr(r, "author", None):
                        r.__dict__["author"] = rec["author"]
                except Exception as e:
                    log.warning("[mcp] deep-fetch failed: %s", e)
                    r.__dict__["body_error"] = f"{type(e).__name__}: {e}"
                finally:
                    try:
                        ep.close()
                    except Exception:
                        pass
        return results

    try:
        raw = await asyncio.to_thread(_run)
    except Exception as e:
        log.exception("[mcp] search failed: %s", e)
        return {
            "engine": engine,
            "query": query,
            "error": f"{type(e).__name__}: {e}",
            "count": 0,
            "results": [],
        }

    results = [r.__dict__ for r in raw]
    return {
        "engine": engine,
        "query": query,
        "count": len(results),
        "results": results,
    }


@mcp.tool()
async def extract(
    url: str,
    paginate: bool = True,
    max_scrolls: int = 3,
    include_links: bool = False,
    include_images: bool = False,
) -> dict[str, Any]:
    """Fetch a URL and extract its main article content as Markdown.

    Use this after ``search`` returns a hit you want to read in full.
    Beats a raw HTTP fetch because:

      * Stealth Chromium renders JS-heavy SPAs (YouTube, Bilibili,
        Reddit redesign, Medium paywalls).
      * Optional auto-scroll / "Load more" clicking surfaces lazy
        content (long Reddit threads, infinite-scroll feeds).
      * trafilatura strips chrome / nav / ads and returns clean
        Markdown plus structured metadata (title, author, date).

    Args:
        url: Full URL to fetch. Must be http(s).
        paginate: If True, auto-scroll and click "Load more" buttons to
            surface lazy content. Default True.
        max_scrolls: Max number of full-page scrolls when paginating.
        include_links: If True, return all <a> tags as a list. Off by
            default to keep the payload small.
        include_images: If True, return all <img> tags. Off by default.

    Returns:
        A dict with ``url``, ``status``, ``title``, ``author``,
        ``date``, ``description``, ``content_markdown``,
        ``content_text``, ``word_count``, ``extractor``, ``scrolls``,
        ``load_more_clicks``, plus ``links`` / ``images`` when
        requested.
    """
    def _run() -> dict[str, Any]:
        page = _pool.page()
        try:
            return extract_page(
                page,
                url=url,
                paginate=paginate,
                max_scrolls=max_scrolls,
                include_links=include_links,
                include_images=include_images,
            )
        finally:
            try:
                page.close()
            except Exception:
                pass

    try:
        return await asyncio.to_thread(_run)
    except Exception as e:
        log.exception("[mcp] extract failed: %s", e)
        return {"url": url, "status": "error", "error": f"{type(e).__name__}: {e}"}


@mcp.tool()
async def list_engines() -> dict[str, Any]:
    """List every search engine available to ``search``.

    Returns a dict with:
      * ``count``: total unique engines
      * ``engines``: list of engine handles (e.g. ``google``, ``reddit``)
      * ``categories``: high-level groupings to help pick the right one
    """
    reg = _engine_registry()
    handles = sorted(reg.keys())
    categories = {
        "general": ["google", "bing", "duckduckgo", "brave", "yandex", "startpage", "ecosia", "qwant"],
        "chinese_search": ["baidu", "sogou", "so360"],
        "code_dev": ["github", "github_search", "stackoverflow", "hackernews", "npm", "npm_search", "devto"],
        "ai_research": ["huggingface", "arxiv"],
        "knowledge": ["wikipedia", "wikivoyage", "pubmed", "wolfram"],
        "forums": ["reddit", "reddit_subreddit", "quora", "blackhatworld", "producthunt"],
        "social_global": ["twitter", "x", "instagram"],
        "social_chinese": ["zhihu", "weibo", "xiaohongshu", "douyin", "toutiao", "bilibili"],
        "western_news": ["bbc", "guardian", "reuters", "apnews", "cnn", "npr", "aljazeera",
                         "techcrunch", "verge", "arstechnica"],
        "video": ["youtube", "twitch", "netflix", "tiktok"],
        "audio_podcast": ["spotify", "soundcloud", "apple_podcasts", "xiaoyuzhou"],
        "movies_books": ["imdb", "goodreads"],
        "ecommerce": ["amazon", "ebay", "icecat", "steam"],
        "jobs_local": ["linkedin_jobs", "indeed", "yelp"],
        "patents_security": ["google_patents", "virustotal"],
        "archive": ["archive_org", "torrent_1337x"],
        "images": ["unsplash", "pixabay", "pexels", "pinterest"],
        "long_form": ["medium"],
        # Ad creative intelligence — competitive research across the four
        # major public ad libraries. Each returns image / video URLs +
        # first/last seen + copy so a marketing agent can build evergreen
        # swipe files. See docs/ADS.md for full mode/parameter reference.
        "ads": [
            "meta_ad_library", "fb_ads", "meta_ads",
            "instagram_ad_library", "ig_ads", "instagram_ads",
            "tiktok_creative_center", "tt_ads", "ttcc",
            "tiktok_ad_library", "tiktok_ads",
            "google_ad_transparency", "g_ads",
        ],
    }
    return {
        "count": len(set(reg.values())),
        "engines": handles,
        "categories": categories,
    }


@mcp.tool()
async def download_ad_media(
    records: list[dict[str, Any]],
    output_dir: str = "./ad_media",
    proxy_url: str | None = None,
    max_per_record: int | None = None,
    max_workers: int = 4,
    timeout: int = 30,
) -> dict[str, Any]:
    """Download every image / video URL from a list of ad-engine results.

    Use this **after** calling ``search`` against any ad-library engine
    (``meta_ad_library``, ``instagram_ad_library``,
    ``tiktok_creative_center``, ``tiktok_ad_library``,
    ``google_ad_transparency``). Pass the ``results`` array straight in
    and every image / video / cover / thumbnail URL it finds will be
    written to ``output_dir`` with the filename pattern
    ``{platform}_{ad_id}_{idx:02d}_{kind}.{ext}``.

    The download is fault-tolerant: a 404 / DNS error / proxy hiccup on
    one URL never breaks the batch; failures land in the response with
    ``success=False``. Use this to bulk-build a swipe-file folder for
    competitive analysis or to feed a vision model that needs the
    actual creative bytes (not just URLs).

    Args:
        records: A list of ad-record dicts. Accepts the per-result
            shape returned by ``search`` (``ad_archive_id`` /
            ``creative_id`` / ``ad_id`` plus ``image_urls`` /
            ``video_url`` / ``video_urls`` / ``cover_image_url`` /
            ``creatives[]`` / ``preview_url``). The downloader probes
            ~10 known field names so all five engines work uniformly.
        output_dir: Where to drop the files. Created if missing.
        proxy_url: Optional ``http(s)://[user:pass@]host:port``. When
            ``None``, an environment variable ``FLUXISP_PROXY`` is
            consulted as a convenience.
        max_per_record: Cap downloads per ad. ``1`` ≈ "highest-res only".
            ``None`` (default) downloads every URL found.
        max_workers: Concurrent downloads (default 4).
        timeout: Per-download timeout in seconds (default 30).

    Returns:
        A dict with::

            {
              "output_dir":  <str>,
              "total":       <int>,   # download attempts made
              "succeeded":   <int>,
              "failed":      <int>,
              "bytes":       <int>,   # total bytes written on success
              "files": [
                {"url", "local_path", "success", "error",
                 "file_size", "content_type", "kind",
                 "ad_id", "platform"},
                ...
              ],
            }
    """
    from .engines._ad_media import AdMediaDownloader

    eff_proxy = proxy_url or os.environ.get("FLUXISP_PROXY")

    def _run() -> list[Any]:
        dl = AdMediaDownloader(
            output_dir,
            proxy_url=eff_proxy,
            timeout=timeout,
            max_retries=2,
        )
        return dl.download_many(
            records,
            max_per_record=max_per_record,
            max_workers=max_workers,
        )

    try:
        results = await asyncio.to_thread(_run)
    except Exception as e:
        log.exception("[mcp] download_ad_media failed: %s", e)
        return {
            "output_dir": output_dir,
            "total": 0, "succeeded": 0, "failed": 0, "bytes": 0,
            "files": [],
            "error": f"{type(e).__name__}: {e}",
        }

    succeeded = sum(1 for r in results if r.success)
    bytes_total = sum(r.file_size or 0 for r in results if r.success)
    return {
        "output_dir": output_dir,
        "total": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "bytes": bytes_total,
        "files": [r.to_dict() for r in results],
    }


# ----------------------------------------------------------------------- main


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("AGENTSEARCH_LOG", "INFO"),
        format="%(levelname)s: %(message)s",
        # Log to stderr so we don't pollute stdout, which MCP uses for
        # JSON-RPC framing.
    )
    try:
        mcp.run()
    finally:
        _pool.shutdown()


if __name__ == "__main__":
    main()
