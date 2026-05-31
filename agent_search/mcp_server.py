"""MCP (Model Context Protocol) server wrapper for AgentSearch.

Exposes 9 tools to any MCP-compatible client (Claude Desktop, Cursor,
Cline, Continue, Kiro, Roo Code, Zed, ...):

  * ``search``                  — query one of 80+ search engines, with
                                   optional ``engine_options`` for
                                   engine-specific parameters
                                   (dev_docs platform=, ad-library
                                   country=, mode=, …)
  * ``extract``                 — fetch a URL and return readability-
                                   extracted markdown
  * ``extract_many``            — same, but parallel batch (3-4× faster
                                   than N sequential calls)
  * ``list_engines``            — enumerate engines + categories +
                                   ``engine_options`` examples
  * ``list_dev_docs_platforms`` — enumerate the 142 dev_docs presets
                                   (with optional substring/category
                                   filter)
  * ``search_app``              — keyword-search Apple App Store /
                                   Google Play with 25+ metadata fields
  * ``lookup_app``              — single-app metadata from URL or id
  * ``find_competitor_ads``     — App URL → ads on Meta / Instagram /
                                   Google / TikTok in one call
  * ``download_ad_media``       — bulk-download every image / video URL
                                   from a list of ad-engine results

The server keeps a single CloakBrowser instance alive for the lifetime
of the process so each tool call doesn't pay the ~0.5-2s Chromium
startup cost. The browser is recycled lazily after a configurable
number of calls (the page state otherwise drifts — cookies pile up,
JS world gets polluted, etc.).

Run with::

    python -m agent_search.mcp_server

Configure in Kiro / Claude Desktop's MCP config::

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
import concurrent.futures
import logging
import os
import threading
from functools import partial
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


# ---------------------------------------------------------------------------
# Dedicated single-worker browser executor
# ---------------------------------------------------------------------------
# Playwright's sync API uses greenlets that are *thread-bound*: every
# Browser / Context / Page object remembers the OS thread it was created
# on, and any subsequent call from a different thread raises
#
#     greenlet.error: cannot switch to a different thread
#
# (or, less commonly, ``Error: Page.goto: Greenlet was created from a
# different thread``).
#
# ``asyncio.to_thread()`` dispatches to asyncio's *default* executor,
# which is a ``ThreadPoolExecutor(max_workers=min(32, cpu_count+4))``.
# Under load that pool reuses idle workers but freely spawns new ones
# when concurrent requests stack up — so the singleton browser ends up
# being touched from whichever worker the second / third call lands on,
# and we hit the greenlet error intermittently.
#
# Fix: pin *all* browser work to a single dedicated worker thread. The
# helper :func:`_to_browser_thread` is the only path callbacks use to
# reach Playwright; ``BrowserPool`` records the worker's thread id on
# launch and asserts on every ``page()`` call so any future regression
# fails loudly instead of flaking under load.
_BROWSER_EXECUTOR = concurrent.futures.ThreadPoolExecutor(
    max_workers=1,
    thread_name_prefix="agent-search-browser",
)


async def _to_browser_thread(fn, *args, **kwargs):
    """Run ``fn`` on the dedicated browser worker thread.

    Every callback that touches the CloakBrowser / Playwright objects
    must go through this helper. We submit through a single-worker
    ``ThreadPoolExecutor`` (rather than ``asyncio.to_thread``, which
    uses the multi-worker default executor) so Playwright's
    greenlet-thread affinity is preserved across calls.

    See module-level comment on ``_BROWSER_EXECUTOR`` for the full
    reasoning.
    """
    loop = asyncio.get_running_loop()
    if args or kwargs:
        fn = partial(fn, *args, **kwargs)
    return await loop.run_in_executor(_BROWSER_EXECUTOR, fn)


def _resolve_default_proxy() -> str | None:
    """Pick an outbound proxy URL from the environment, in priority order.

    Priority: AGENTSEARCH_PROXY > FLUXISP_PROXY > HTTPS_PROXY > HTTP_PROXY.
    Returns None when nothing is set so the browser launches direct.

    This matters when the host's IP is a datacenter range (AWS, GCP, ...);
    Meta / TikTok / Google ad libraries rate-limit or 401 such IPs, so
    operators usually configure a residential proxy via env var. We pick
    it up automatically rather than forcing every deployment to also
    edit the unit / compose file.
    """
    for var in ("AGENTSEARCH_PROXY", "FLUXISP_PROXY", "HTTPS_PROXY", "HTTP_PROXY"):
        v = os.environ.get(var)
        if v:
            return v
    return None


class BrowserPool:
    """Lazy, thread-pinned singleton browser with periodic recycling.

    The browser must only be touched from the dedicated worker thread
    used by :data:`_BROWSER_EXECUTOR` — see the module-level comment on
    that executor for why. We record the launching thread's id and
    assert on every ``page()`` call to make any drift fail loudly.
    """

    def __init__(self) -> None:
        self._browser = None
        self._calls = 0
        self._lock = threading.Lock()
        # Thread id of whichever worker first launched the browser.
        # Set in _start(); checked in page().
        self._browser_thread_id: int | None = None

    def _start(self) -> None:
        log.info("[mcp] launching browser (headless=%s)", HEADLESS)
        self._browser = launch(BrowserConfig(
            headless=HEADLESS,
            humanize=True,
            proxy=_resolve_default_proxy(),
        ))
        self._calls = 0
        self._browser_thread_id = threading.get_ident()

    def _maybe_recycle(self) -> None:
        if self._calls >= RECYCLE_AFTER and self._browser is not None:
            log.info("[mcp] recycling browser after %d calls", self._calls)
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
            self._browser_thread_id = None

    def page(self):
        """Return a fresh page bound to the live browser.

        Must be called from the dedicated browser worker thread (i.e.
        from inside a callback dispatched via ``_to_browser_thread``).
        Calling from any other thread would trip Playwright's greenlet
        affinity — we assert here so the failure mode is a clear
        ``RuntimeError`` rather than an opaque greenlet stack trace.
        """
        with self._lock:
            self._maybe_recycle()
            if self._browser is None:
                self._start()
            else:
                cur = threading.get_ident()
                if (
                    self._browser_thread_id is not None
                    and cur != self._browser_thread_id
                ):
                    raise RuntimeError(
                        f"BrowserPool.page() called from thread {cur} but "
                        f"the browser was created on thread "
                        f"{self._browser_thread_id}. Playwright's sync API "
                        f"is greenlet-bound to its creation thread; route "
                        f"this call through _to_browser_thread() instead "
                        f"of asyncio.to_thread()."
                    )
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
                self._browser_thread_id = None


_pool = BrowserPool()
mcp = FastMCP("agent-search")


# ---------------------------------------------------------------------- tools


@mcp.tool()
async def search(
    query: str,
    engine: str = "duckduckgo",
    limit: int = 10,
    depth: int = 0,
    engine_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Search the live web through one of 80+ stealth-browser engines.

    Use this whenever you need fresh content that isn't in your training
    data — Google / Bing / DuckDuckGo for general queries, ``reddit``
    for opinions, ``stackoverflow`` for code errors, ``arxiv`` for
    research, ``github`` for repositories, ``youtube`` for video,
    ``bilibili`` / ``zhihu`` / ``xiaohongshu`` for Chinese content,
    ``dev_docs`` for any developer documentation portal (142 presets),
    ``fb_docs`` for Meta developer docs, ``meta_ad_library`` /
    ``google_ad_transparency`` / ``tiktok_creative_center`` for ad
    competitor research, and so on. Call ``list_engines`` to see every
    available engine.

    Args:
        query: Search query string. Supports the engine's native syntax.
        engine: Engine handle (e.g. ``google``, ``reddit``, ``arxiv``).
            Defaults to DuckDuckGo because it's the most rate-limit-free.
        limit: Max results to return (default 10, hard ceiling 50).
        depth: When > 0, also fetch and readability-extract the top N
            result URLs inline. Each result gets ``body_markdown`` /
            ``body_text`` / ``body_word_count`` fields attached. Saves
            the agent N follow-up ``extract`` calls. Default 0 (SERP only).
        engine_options: Engine-specific keyword arguments forwarded to
            ``EngineClass.search()``. Required for engines whose query
            isn't just free text. Common examples:

              * ``dev_docs``  / ``docs``:
                ``{"platform": "stripe"}``  — preset (one of 115:
                  stripe, openai, anthropic, aws, react, whatsapp,
                  telegram, tiktok, ...)
                ``{"site": "docs.example.com"}`` — arbitrary host
                ``{"mode": "reference"}`` (or changelog/api/tutorial/examples)
                ``{"product": "lambda"}`` — narrow with inurl:lambda
                ``{"api_version": "v25.0"}`` — quote a version literal
              * ``fb_docs`` / ``facebook_docs``:
                ``{"product": "marketing-api"}`` (16 product slugs)
                ``{"mode": "reference"}``  ``{"api_version": "v25.0"}``
              * ``meta_ad_library`` / ``fb_ads`` / ``instagram_ad_library``:
                ``{"country": "US", "active_status": "active",
                   "ad_type": "all", "media_type": "all"}``
                ``{"mode": "advertiser", "page_id": "20409006880"}`` —
                  query a known Facebook page id directly
                ``{"mode": "page_url", "page_url": "https://facebook.com/Shopify"}``
              * ``google_ad_transparency`` / ``g_ads``:
                ``{"mode": "search_advertisers", "region": "US"}``
                ``{"mode": "domain", "domain": "shopify.com"}``
                ``{"mode": "advertiser_ads", "advertiser_id": "AR..."}``
              * ``tiktok_creative_center`` / ``tt_ads``:
                ``{"mode": "top_ads", "country": "US",
                   "industry": "ecommerce", "period": 30}``
              * ``reddit`` / ``reddit_subreddit``:
                ``{"sort": "top", "time": "month"}``
              * ``youtube``:
                ``{"upload_date": "this_week", "duration": "long",
                   "sort_by": "view_count"}``
              * ``github_search``:
                ``{"type": "code", "language": "python",
                   "stars": ">100"}``
              * ``arxiv``:
                ``{"category": "cs.AI", "sort_by": "submittedDate"}``

            For engines that don't accept extras, leave it empty.

    Returns:
        A dict with ``engine``, ``query``, ``count``, and ``results`` —
        each result has at least ``title``, ``url``, ``snippet``, plus
        engine-specific extras (e.g. ``score``, ``video_id``,
        ``arxiv_id``, ``imdb_rating``, ``price``, ``doc_section``,
        ``platform``, ``ad_archive_id``). When ``depth > 0``, the
        first N results also have ``body_markdown`` / ``body_word_count``.
    """
    limit = max(1, min(limit, 50))
    depth = max(0, min(depth, limit))
    extra_kwargs = dict(engine_options or {})
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
            try:
                results = instance.search(
                    query, limit=limit, **extra_kwargs) or []
            except TypeError as te:
                # Fall back to a vanilla call when the engine doesn't
                # accept the supplied kwargs, but surface the exact
                # mismatch so the caller can fix their options dict.
                if extra_kwargs:
                    raise TypeError(
                        f"engine {engine!r} rejected engine_options "
                        f"{list(extra_kwargs)}: {te}"
                    ) from te
                raise
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
        raw = await _to_browser_thread(_run)
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
    wait_for_selector: str | None = None,
    wait_for_timeout_ms: int = 10000,
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
        wait_for_selector: Optional CSS / XPath selector to wait for
            after navigation, before extracting. Use this for JS-
            rendered widgets where the static DOM is empty (e.g.
            AppsFlyer benchmarks portal, data-heavy charts). Pass
            ``"div[data-testid='chart']"`` or ``"text=Average CPI"``.
            The result includes ``selector_matched: bool`` so the
            caller can detect timeouts.
        wait_for_timeout_ms: Per-selector wait budget. Default 10s.

    Returns:
        A dict with ``url``, ``status``, ``title``, ``author``,
        ``date``, ``description``, ``content_markdown``,
        ``content_text``, ``word_count``, ``extractor``, ``scrolls``,
        ``load_more_clicks``, ``selector_waited``, ``selector_matched``,
        plus ``links`` / ``images`` when requested.
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
                wait_for_selector=wait_for_selector,
                wait_for_timeout_ms=wait_for_timeout_ms,
            )
        finally:
            try:
                page.close()
            except Exception:
                pass

    try:
        return await _to_browser_thread(_run)
    except Exception as e:
        log.exception("[mcp] extract failed: %s", e)
        return {"url": url, "status": "error", "error": f"{type(e).__name__}: {e}"}


@mcp.tool()
async def search_app(
    query: str,
    store: str = "all",
    country: str = "us",
    limit: int = 25,
    fast: bool = False,
    with_contact: bool = False,
    proxy_url: str | None = None,
) -> dict[str, Any]:
    """Keyword-search the Apple App Store and/or Google Play.

    Returns rich app metadata (title, developer, ratings, website,
    support email, privacy URL, screenshots, …) so downstream agents
    can chain into ``find_competitor_ads`` (App Store URL → ads on
    every platform) or build outreach lists.

    Args:
        query: Free-text query — same syntax both stores accept.
        store: ``apple`` / ``ios`` for App Store, ``google`` / ``play``
            / ``android`` for Google Play, ``all`` (default) hits both.
        country: ISO country code (``us``, ``cn``, ``jp``, …). Some
            apps are region-locked.
        limit: Max apps per store (default 25).
        fast: When True, skip the per-app Google Play HTML detail
            fetch — only package ids and titles are returned for
            Google rows. Halves wall-time when scanning many apps.
        with_contact: Filter to apps that exposed at least one of
            ``support_email`` / ``privacy_url`` / ``website``. Useful
            for lead-gen / compliance pipelines.
        proxy_url: Optional ``http(s)://[user:pass@]host:port`` —
            falls back to ``FLUXISP_PROXY`` env var when unset.

    Returns:
        ``{"query", "store", "country", "count", "elapsed_s", "results": [...]}``
        Each result has 25+ fields including ``app_id``, ``bundle_id``,
        ``store``, ``title``, ``developer_name``, ``developer_id``,
        ``website``, ``support_email``, ``privacy_url``, ``rating``,
        ``rating_count``, ``price``, ``currency``, ``description``,
        ``icon_url``, ``screenshots``, ``url``, …
    """
    from .engines._app_store import (
        search_apple, search_google, search_app as _search_app_fn,
    )

    proxy = proxy_url or os.environ.get("FLUXISP_PROXY")
    proxies = ({"https": proxy, "http": proxy}) if proxy else None
    limit = max(1, min(limit, 200))
    s = (store or "all").lower()

    def _run() -> list[Any]:
        if s in ("apple", "ios"):
            return search_apple(query, country=country, limit=limit,
                                proxies=proxies) or []
        if s in ("google", "android", "play"):
            return search_google(query, country=country, limit=limit,
                                 proxies=proxies,
                                 fetch_details=not fast) or []
        return _search_app_fn(query, store="all", country=country,
                              limit=limit, proxies=proxies,
                              fetch_details=not fast) or []

    import time as _time
    started = _time.time()
    try:
        raw = await _to_browser_thread(_run)
    except Exception as e:
        log.exception("[mcp] search_app failed: %s", e)
        return {
            "query": query, "store": s, "country": country,
            "error": f"{type(e).__name__}: {e}",
            "count": 0, "results": [],
        }
    elapsed = _time.time() - started

    if with_contact:
        raw = [m for m in raw
               if m.support_email or m.privacy_url or m.website]

    return {
        "query": query,
        "store": s,
        "country": country,
        "count": len(raw),
        "elapsed_s": round(elapsed, 1),
        "results": [m.to_dict() for m in raw],
    }


@mcp.tool()
async def lookup_app(
    app_url: str,
    country: str = "us",
    proxy_url: str | None = None,
) -> dict[str, Any]:
    """Look up a single app's metadata from a store URL or app id.

    Accepts:

      * Apple URL — ``https://apps.apple.com/.../id1234567890``
      * Google Play URL — ``https://play.google.com/store/apps/details?id=com.foo.bar``
      * Bare numeric id (Apple) — ``1234567890``
      * Bare package id (Google Play) — ``com.foo.bar``

    Use this as the cheaper, single-app entry point compared to
    ``search_app`` (no keyword scan, just one lookup).

    Args:
        app_url: Store URL or bare id (see above).
        country: ISO country code (default ``us``).
        proxy_url: Optional outbound proxy.

    Returns:
        AppMetadata dict with the same 25+ fields as ``search_app``
        rows, plus a top-level ``error`` when resolution fails.
    """
    from .engines._app_store import lookup_app as _lookup

    proxy = proxy_url or os.environ.get("FLUXISP_PROXY")
    proxies = ({"https": proxy, "http": proxy}) if proxy else None

    def _run() -> Any:
        return _lookup(app_url, proxies=proxies, country=country.lower())

    try:
        meta = await _to_browser_thread(_run)
    except Exception as e:
        log.exception("[mcp] lookup_app failed: %s", e)
        return {"app_url": app_url, "error": f"{type(e).__name__}: {e}"}

    if not meta:
        return {
            "app_url": app_url,
            "error": (
                "could not resolve. Pass an Apple App Store URL "
                "(https://apps.apple.com/.../id<NUM>), a Google Play "
                "URL (https://play.google.com/store/apps/details?id=<PKG>), "
                "or a bare numeric / package id."
            ),
        }
    return meta.to_dict()


@mcp.tool()
async def find_competitor_ads(
    app_url: str,
    platforms: list[str] | None = None,
    limit_per_platform: int = 10,
    country: str = "US",
    precise: bool = False,
    proxy_url: str | None = None,
) -> dict[str, Any]:
    """End-to-end competitor ad research from an App Store URL.

    Pipeline::

        App URL  →  app metadata (developer, website domain)
                 →  fan-out to ad libraries:
                      * Meta / Instagram   (query=developer name)
                      * Google ATC         (mode=domain, domain=…)
                      * TikTok CC          (keyword=developer name)
                 →  merged AdRecord stream

    Use this when you have a competitor's app and want to know what
    ads they're running across the major paid platforms in one shot.

    Args:
        app_url: App Store URL or bare id (same accepted formats as
            ``lookup_app``).
        platforms: Subset of ``["meta", "instagram", "google", "tiktok"]``.
            Default = all four.
        limit_per_platform: Max ads per platform (default 10).
        country: ISO country code, applied to every platform that
            takes a country filter. Default ``US``.
        precise: When True, run Meta's ``lookup_pages`` first to
            resolve the developer name to a canonical Facebook
            page_id, then query Meta/Instagram by ``page_id`` instead
            of by keyword. ~1 extra round-trip but much higher
            advertiser-match precision.
        proxy_url: Optional ``http(s)://[user:pass@]host:port``.
            Falls back to ``FLUXISP_PROXY`` / ``HTTPS_PROXY`` /
            ``HTTP_PROXY`` env vars.

    Returns:
        ``{"app", "platforms_queried", "totals": {<platform>: count},
           "ads": [<AdRecord>, ...], "errors": {<platform>: msg}}``
    """
    from .engines._app_store import lookup_app as _lookup_app
    from .engines._ad_base import to_ad_record

    proxy = proxy_url or _resolve_default_proxy()
    proxies = ({"https": proxy, "http": proxy}) if proxy else None
    limit = max(1, min(limit_per_platform, 50))
    plats = [p.lower() for p in (
        platforms or ["meta", "instagram", "google", "tiktok"])]

    def _run() -> dict[str, Any]:
        meta = _lookup_app(app_url, proxies=proxies, country=country.lower())
        if not meta:
            return {
                "app_url": app_url,
                "error": "could not resolve app — pass a real store URL or id",
                "ads": [],
            }
        if not meta.developer_name and not meta.domain:
            return {
                "app": meta.to_dict(),
                "error": "app has no developer_name or domain — nothing to query",
                "ads": [],
            }

        ads: list[dict[str, Any]] = []
        totals: dict[str, int] = {}
        errors: dict[str, str] = {}
        platforms_queried: list[str] = []

        # Helper that runs one platform query in a fresh page.
        def _query(engine_handle: str, kwargs: dict) -> list[Any]:
            engine_cls = _get_engine(engine_handle)
            page = _pool.page()
            try:
                inst = engine_cls(page)
                rs = inst.search(
                    kwargs.pop("query", ""),
                    limit=limit,
                    **kwargs,
                ) or []
            finally:
                try:
                    page.close()
                except Exception:
                    pass
            return rs

        # Meta
        if "meta" in plats and meta.developer_name:
            platforms_queried.append("meta")
            try:
                if precise:
                    rs = _query("meta_ad_library", {
                        "query": meta.developer_name,
                        "mode": "keyword",
                        "country": country,
                    })
                else:
                    rs = _query("meta_ad_library", {
                        "query": meta.developer_name,
                        "country": country,
                    })
                for r in rs:
                    rec = to_ad_record(r.__dict__).to_dict()
                    ads.append(rec)
                totals["meta"] = len(rs)
            except Exception as e:
                errors["meta"] = f"{type(e).__name__}: {e}"

        # Instagram
        if "instagram" in plats and meta.developer_name:
            platforms_queried.append("instagram")
            try:
                rs = _query("instagram_ad_library", {
                    "query": meta.developer_name,
                    "country": country,
                })
                for r in rs:
                    rec = to_ad_record(r.__dict__).to_dict()
                    ads.append(rec)
                totals["instagram"] = len(rs)
            except Exception as e:
                errors["instagram"] = f"{type(e).__name__}: {e}"

        # Google ATC — domain mode (more reliable than keyword)
        if "google" in plats and meta.domain:
            platforms_queried.append("google")
            try:
                rs = _query("google_ad_transparency", {
                    "query": meta.domain,
                    "mode": "domain",
                    "domain": meta.domain,
                    "region": country,
                })
                for r in rs:
                    rec = to_ad_record(r.__dict__).to_dict()
                    ads.append(rec)
                totals["google"] = len(rs)
            except Exception as e:
                errors["google"] = f"{type(e).__name__}: {e}"

        # TikTok Creative Center — keyword on top_ads
        if "tiktok" in plats and meta.developer_name:
            platforms_queried.append("tiktok")
            try:
                rs = _query("tiktok_creative_center", {
                    "query": meta.developer_name,
                    "mode": "top_ads",
                    "country": country,
                })
                for r in rs:
                    rec = to_ad_record(r.__dict__).to_dict()
                    ads.append(rec)
                totals["tiktok"] = len(rs)
            except Exception as e:
                errors["tiktok"] = f"{type(e).__name__}: {e}"

        return {
            "app": meta.to_dict(),
            "platforms_queried": platforms_queried,
            "totals": totals,
            "ads": ads,
            "errors": errors,
        }

    try:
        return await _to_browser_thread(_run)
    except Exception as e:
        log.exception("[mcp] find_competitor_ads failed: %s", e)
        return {
            "app_url": app_url,
            "error": f"{type(e).__name__}: {e}",
            "ads": [],
        }


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
        # Developer documentation search — DDG site-search wrappers
        # for any developer portal. Use engine_options={"platform": ...}
        # for the 115-preset lookup, or {"site": "docs.example.com"}
        # for an arbitrary host.
        "developer_docs": [
            "dev_docs", "docs",
            "facebook_docs", "fb_docs", "meta_docs", "fb_dev",
        ],
    }
    return {
        "count": len(set(reg.values())),
        "engines": handles,
        "categories": categories,
        # Hint companion tools so agents can discover them without
        # needing to read the README first.
        "companion_tools": [
            "extract                  — fetch a URL and return Markdown",
            "extract_many             — parallel batch extract for many URLs",
            "list_dev_docs_platforms  — enumerate the 142 dev_docs preset platforms",
            "search_app               — keyword-search Apple App Store / Google Play",
            "lookup_app               — single-app metadata from URL or id",
            "find_competitor_ads      — App URL → ads on Meta/IG/Google/TikTok",
            "download_ad_media        — bulk-download every image/video URL from ad results",
        ],
        # Common engine_options examples for the most-used engines —
        # surfaced here so a curious agent can call list_engines once
        # and learn the full toolkit without reading the search docstring.
        "engine_options_examples": {
            "dev_docs": {
                "platform": "stripe",
                "mode": "reference",
                "product": "billing",
            },
            "fb_docs": {
                "product": "marketing-api",
                "mode": "reference",
                "api_version": "v25.0",
            },
            "meta_ad_library": {
                "country": "US",
                "active_status": "active",
                "media_type": "video",
            },
            "google_ad_transparency": {
                "mode": "domain",
                "domain": "shopify.com",
                "region": "US",
            },
            "tiktok_creative_center": {
                "mode": "top_ads",
                "country": "US",
                "industry": "ecommerce",
                "period": 30,
            },
            "youtube": {
                "upload_date": "this_week",
                "duration": "long",
                "sort_by": "view_count",
            },
            "github_search": {
                "type": "code",
                "language": "python",
                "stars": ">100",
            },
        },
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
        results = await _to_browser_thread(_run)
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


@mcp.tool()
async def list_dev_docs_platforms(
    filter_substring: str | None = None,
    category: str | None = None,
) -> dict[str, Any]:
    """List every platform alias the ``dev_docs`` engine accepts.

    Use this when the agent needs to know if a specific developer
    portal is supported as a preset (call with ``filter_substring``
    to grep), or to see all options grouped by domain.

    The ``dev_docs`` engine accepts ``engine_options={"platform": ...}``
    where the value is one of these aliases. For arbitrary hosts not
    in this list, pass ``engine_options={"site": "docs.example.com"}``
    instead.

    Args:
        filter_substring: Case-insensitive substring filter — only
            aliases or hostnames containing this string are returned.
            Example: ``"google"`` matches ``google-cloud``, ``google-ads``,
            ``google-analytics``, ``firebase``, etc.
        category: Restrict to a single category. One of:
            ``cloud_infra``, ``apis_saas``, ``social``, ``messaging``,
            ``meta_megasite``, ``google_products``, ``mobile_ad_intel``,
            ``ai_ml``, ``frontend``, ``mobile_dev``, ``browsers``,
            ``observability``, ``identity``, ``workspace``,
            ``ml_training``.

    Returns:
        ``{"count", "categories": {<name>: [aliases]}, "platforms": [
        {"alias", "hosts", "host_count"}, ...]}``
    """
    from .engines.dev_docs import (
        _PRESETS, list_platforms, _CATEGORIES, list_categories,
    )

    cat_map = _CATEGORIES

    if category and category not in cat_map:
        return {
            "error": f"unknown category {category!r}. Choose from "
                     f"{sorted(cat_map.keys())}",
            "count": 0,
            "categories": {},
            "platforms": [],
        }

    aliases = list_platforms()
    if category:
        wanted = set(cat_map[category])
        aliases = [a for a in aliases if a in wanted]
    if filter_substring:
        f = filter_substring.lower()
        aliases = [
            a for a in aliases
            if f in a.lower() or any(f in h.lower() for h in _PRESETS[a])
        ]

    platforms = [
        {
            "alias": a,
            "hosts": list(_PRESETS[a]),
            "host_count": len(_PRESETS[a]),
        }
        for a in aliases
    ]

    # Trim category map to only categories containing matching aliases
    # (so a filtered call returns a relevant overview).
    matched_set = {p["alias"] for p in platforms}
    categories_filtered = {
        cat: [a for a in lst if a in matched_set]
        for cat, lst in cat_map.items()
    }
    categories_filtered = {
        k: v for k, v in categories_filtered.items() if v
    }

    return {
        "count": len(platforms),
        "total_presets": len(_PRESETS),
        "categories": categories_filtered,
        "platforms": platforms,
        "usage_hint": (
            'Pass any alias as engine_options={"platform": "<alias>"} '
            'to the search tool with engine="dev_docs". For arbitrary '
            'hosts not in this list, use engine_options={"site": '
            '"docs.example.com"} instead.'
        ),
    }


@mcp.tool()
async def extract_many(
    urls: list[str],
    paginate: bool = True,
    max_scrolls: int = 2,
    include_links: bool = False,
    include_images: bool = False,
) -> dict[str, Any]:
    """Fetch and Markdown-extract a batch of URLs in one call.

    Use this when the agent has a list of URLs to read (e.g. the top N
    hits from a previous ``search`` call, or every link in a roundup
    article). Each URL is processed sequentially through the shared
    browser pool — this is the same wall-clock cost as N sequential
    ``extract`` calls, but saves the agent N round-trips.

    Note: parallelisation is disabled because Playwright's sync API
    is greenlet-bound to a single thread per browser. For real
    parallelism, scale by running multiple MCP servers behind a load
    balancer.

    Args:
        urls: List of HTTP(S) URLs. Anything else (chrome://, mailto:,
            javascript:) is rejected per-URL with ``status="invalid_url"``.
        paginate: If True, auto-scroll and click "Load more" buttons to
            surface lazy content (per URL).
        max_scrolls: Max scrolls per URL when paginating. Default 2 to
            keep batch wall-time bounded.
        include_links: If True, return all <a> tags per URL.
        include_images: If True, return all <img> tags per URL.

    Returns:
        ``{"total", "succeeded", "failed", "elapsed_s", "results": [<extract dict>...]}``
        Each result has the same shape as the single-URL ``extract``
        tool plus a ``url`` field for correlation. Results preserve
        input order.
    """
    # Validate URLs upfront — reject obvious junk so we don't waste
    # a Chromium tab on a mailto:.
    cleaned: list[tuple[int, str]] = []
    invalid_idx: dict[int, dict[str, Any]] = {}
    for i, u in enumerate(urls or []):
        u = (u or "").strip()
        if not u or not u.lower().startswith(("http://", "https://")):
            invalid_idx[i] = {
                "url": u,
                "status": "invalid_url",
                "error": "must start with http:// or https://",
            }
            continue
        cleaned.append((i, u))

    def _run() -> dict[int, dict[str, Any]]:
        out: dict[int, dict[str, Any]] = {}
        for i, url in cleaned:
            page = _pool.page()
            try:
                out[i] = extract_page(
                    page,
                    url=url,
                    paginate=paginate,
                    max_scrolls=max_scrolls,
                    include_links=include_links,
                    include_images=include_images,
                )
            except Exception as e:
                out[i] = {
                    "url": url,
                    "status": "error",
                    "error": f"{type(e).__name__}: {e}",
                }
            finally:
                try:
                    page.close()
                except Exception:
                    pass
        return out

    import time as _time
    started = _time.time()
    try:
        results_by_idx = await _to_browser_thread(_run)
    except Exception as e:
        log.exception("[mcp] extract_many failed: %s", e)
        return {
            "total": len(urls or []),
            "succeeded": 0,
            "failed": len(urls or []),
            "elapsed_s": round(_time.time() - started, 1),
            "error": f"{type(e).__name__}: {e}",
            "results": list(invalid_idx.values()),
        }

    # Merge and preserve input order
    merged_dict = {**results_by_idx, **invalid_idx}
    ordered = [merged_dict[i] for i in sorted(merged_dict.keys())]
    succeeded = sum(1 for r in ordered
                    if r.get("status") not in ("error", "invalid_url"))
    return {
        "total": len(ordered),
        "succeeded": succeeded,
        "failed": len(ordered) - succeeded,
        "elapsed_s": round(_time.time() - started, 1),
        "results": ordered,
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
        # The browser must be closed from the same thread that launched
        # it (greenlet affinity), so we submit shutdown through the
        # dedicated worker rather than calling _pool.shutdown() from
        # the main thread directly. Pre-fix this would silently raise
        # a greenlet error during process exit.
        try:
            _BROWSER_EXECUTOR.submit(_pool.shutdown).result(timeout=10)
        except Exception as e:
            log.warning("[mcp] browser shutdown raised: %s", e)
        finally:
            _BROWSER_EXECUTOR.shutdown(wait=True, cancel_futures=True)


if __name__ == "__main__":
    main()
