"""URL content extraction with readability + auto-pagination.

Given a Playwright `page`, this module:

1. Navigates to the URL (caller can also pre-navigate).
2. Optionally auto-scrolls / clicks "Load more" buttons to trigger lazy
   content (so we don't lose the bottom half of long Reddit threads /
   Medium articles / infinite-scroll feeds).
3. Pulls the rendered HTML and runs it through `trafilatura` to extract
   the main article content as Markdown plus structured metadata
   (title, author, date, language).
4. Falls back to a plain `inner_text("body")` dump when the page is too
   short or trafilatura returns nothing — so this never fails open.

The public entry point is :func:`extract_page`; it returns a dict that
the CLI can render either as JSON or as human-readable text.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any
from urllib.parse import urljoin, urlparse

log = logging.getLogger(__name__)

try:
    import trafilatura  # type: ignore
    from trafilatura.settings import use_config  # type: ignore

    _TRAFILATURA_OK = True
except Exception as e:  # pragma: no cover
    log.warning("trafilatura not available: %s", e)
    trafilatura = None  # type: ignore
    _TRAFILATURA_OK = False


# Auto-scroll heuristics for lazy-loaded pages.
DEFAULT_MAX_SCROLLS = 3
SCROLL_PAUSE_S = 0.8

# Selectors of "Load more" / "Show more" / pagination buttons we'll click
# at most a few times to materialise hidden content. Keep this list short
# and well-targeted — clicking generic buttons risks navigation.
LOAD_MORE_SELECTORS = [
    "button:has-text('Load more')",
    "button:has-text('Show more')",
    "button:has-text('See more')",
    "a:has-text('Load more')",
    "a:has-text('Show more')",
    "[data-testid='load-more']",
    ".load-more",
    "button.morebutton",  # old-reddit "load more comments" pattern
    ".morecomments a",
]


def _build_trafilatura_config():
    """Trafilatura config tuned for agent use:

    * Always include comments (Reddit / HN / Medium discussions matter).
    * Always include tables (docs, comparison pages).
    * No author / date filtering — we want everything we can get.
    """
    if not _TRAFILATURA_OK:
        return None
    cfg = use_config()
    cfg.set("DEFAULT", "EXTRACTION_TIMEOUT", "30")
    cfg.set("DEFAULT", "MIN_EXTRACTED_SIZE", "100")
    cfg.set("DEFAULT", "MIN_OUTPUT_SIZE", "100")
    return cfg


def _auto_scroll(page, max_scrolls: int) -> int:
    """Scroll to the bottom up to ``max_scrolls`` times. Returns scrolls done.

    We stop early if the page height stops growing — that means there's no
    more lazy content to surface.
    """
    if max_scrolls <= 0:
        return 0
    done = 0
    try:
        prev_h = page.evaluate("() => document.body.scrollHeight")
    except Exception:
        return 0
    for _ in range(max_scrolls):
        try:
            page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
        except Exception:
            break
        time.sleep(SCROLL_PAUSE_S)
        try:
            new_h = page.evaluate("() => document.body.scrollHeight")
        except Exception:
            break
        done += 1
        if new_h <= prev_h:
            break
        prev_h = new_h
    return done


def _click_load_more(page, max_clicks: int = 3) -> int:
    """Click 'Load more' / 'Show more' style buttons up to ``max_clicks`` times.

    We try each selector in order; on a successful click we wait briefly for
    new DOM nodes to appear before trying again. Failures are silent — the
    selectors are best-effort.
    """
    if max_clicks <= 0:
        return 0
    clicks = 0
    for _ in range(max_clicks):
        clicked = False
        for sel in LOAD_MORE_SELECTORS:
            try:
                btn = page.query_selector(sel)
                if not btn:
                    continue
                btn.scroll_into_view_if_needed(timeout=2000)
                btn.click(timeout=3000)
                clicked = True
                clicks += 1
                time.sleep(SCROLL_PAUSE_S)
                break
            except Exception:
                continue
        if not clicked:
            break
    return clicks


def _absolutize(base_url: str, href: str) -> str:
    if not href:
        return ""
    if href.startswith(("http://", "https://", "mailto:", "javascript:")):
        return href
    try:
        return urljoin(base_url, href)
    except Exception:
        return href


def _collect_links(page, base_url: str, limit: int = 200) -> list[dict[str, str]]:
    """Pull the first N <a> tags as {text, url} dicts."""
    try:
        raw = page.evaluate(
            "(n) => Array.from(document.querySelectorAll('a[href]'))"
            ".slice(0, n).map(a => ({text: (a.innerText || '').trim().slice(0, 200), href: a.getAttribute('href') || ''}))",
            limit,
        )
    except Exception:
        return []
    out: list[dict[str, str]] = []
    for item in raw or []:
        href = (item.get("href") or "").strip()
        text = (item.get("text") or "").strip()
        if not href:
            continue
        out.append({"text": text, "url": _absolutize(base_url, href)})
    return out


def _collect_images(page, base_url: str, limit: int = 50) -> list[dict[str, str]]:
    """Pull the first N <img> tags as {src, alt} dicts."""
    try:
        raw = page.evaluate(
            "(n) => Array.from(document.querySelectorAll('img'))"
            ".slice(0, n).map(i => ({src: i.getAttribute('src') || i.getAttribute('data-src') || '', alt: i.getAttribute('alt') || ''}))",
            limit,
        )
    except Exception:
        return []
    out: list[dict[str, str]] = []
    for item in raw or []:
        src = (item.get("src") or "").strip()
        if not src:
            continue
        out.append({"src": _absolutize(base_url, src), "alt": (item.get("alt") or "").strip()})
    return out


def _word_count(text: str) -> int:
    if not text:
        return 0
    return len(re.findall(r"\w+", text))


def _fallback_text(page) -> str:
    """Last-resort extractor: strip script/style and dump body text."""
    try:
        return page.evaluate(
            """
            () => {
                const clone = document.body.cloneNode(true);
                for (const sel of ['script', 'style', 'noscript', 'iframe']) {
                    clone.querySelectorAll(sel).forEach(el => el.remove());
                }
                return clone.innerText || '';
            }
            """
        ) or ""
    except Exception:
        try:
            return page.inner_text("body") or ""
        except Exception:
            return ""


def extract_page(
    page,
    url: str | None = None,
    *,
    paginate: bool = True,
    max_scrolls: int = DEFAULT_MAX_SCROLLS,
    max_load_more_clicks: int = 3,
    include_links: bool = True,
    include_images: bool = True,
    timeout_ms: int = 30000,
    wait_for_selector: str | None = None,
    wait_for_timeout_ms: int = 10000,
) -> dict[str, Any]:
    """Extract main content from a page as structured data.

    If ``url`` is provided, we navigate there first; otherwise we assume the
    caller already loaded the page.

    Args (selected):
        wait_for_selector:  Optional CSS / XPath selector to wait for after
            navigation, before extracting. Useful for JS-rendered widgets
            where the static DOM is empty (AppsFlyer benchmarks portal,
            data-heavy SPAs). Pass e.g. ``"div[data-testid='chart']"`` or
            ``"text=Average CPI"``. Returns ``status="empty"`` if the
            selector never appears within ``wait_for_timeout_ms``.
        wait_for_timeout_ms:  Per-selector wait budget. Default 10s.

    Returns a dict with keys:
      - url:               final URL after redirects
      - status:            "ok" | "empty" | "error"
      - title:             <title> or trafilatura-detected title
      - author:            best-effort byline (may be empty)
      - date:              published date string (may be empty)
      - language:          detected language code (may be empty)
      - description:       meta description (may be empty)
      - content_markdown:  main article content as Markdown
      - content_text:      plain-text version of main content
      - word_count:        int
      - links:             [{text, url}, ...] (omitted when include_links=False)
      - images:            [{src, alt}, ...]  (omitted when include_images=False)
      - extractor:         which path produced the content ("trafilatura" | "fallback")
      - scrolls:           number of auto-scrolls performed
      - load_more_clicks:  number of "Load more" clicks performed
      - selector_waited:   the wait_for_selector argument (echoed for diagnosis)
      - selector_matched:  bool — whether the selector was found
    """
    out: dict[str, Any] = {
        "url": url or "",
        "status": "error",
        "title": "",
        "author": "",
        "date": "",
        "language": "",
        "description": "",
        "content_markdown": "",
        "content_text": "",
        "word_count": 0,
        "extractor": None,
        "scrolls": 0,
        "load_more_clicks": 0,
        "selector_waited": wait_for_selector or "",
        "selector_matched": False,
    }

    if url:
        try:
            page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
        except Exception as e:
            out["error"] = f"navigation failed: {e}"
            return out

    try:
        out["url"] = page.url
    except Exception:
        pass

    # Wait for a custom selector (e.g. a JS-rendered widget) before
    # falling through to scroll/extract. If the selector never appears,
    # we still attempt extraction — the caller can read selector_matched
    # to decide whether the body is meaningful.
    if wait_for_selector:
        try:
            page.wait_for_selector(
                wait_for_selector, timeout=wait_for_timeout_ms,
            )
            out["selector_matched"] = True
        except Exception as e:
            log.debug("wait_for_selector %r timed out: %s",
                      wait_for_selector, e)
            out["selector_matched"] = False

    # Capture <title> as a baseline. Trafilatura's metadata may overwrite this
    # later with a cleaner version (e.g. without site suffix).
    try:
        out["title"] = (page.title() or "").strip()
    except Exception:
        pass

    if paginate:
        try:
            out["scrolls"] = _auto_scroll(page, max_scrolls)
            out["load_more_clicks"] = _click_load_more(page, max_load_more_clicks)
        except Exception as e:
            log.debug("paginate failed: %s", e)

    # Pull the rendered HTML for trafilatura.
    html = ""
    try:
        html = page.content()
    except Exception as e:
        log.warning("page.content() failed: %s", e)

    base_url = out["url"] or url or ""

    md_content = ""
    txt_content = ""
    extractor = "fallback"

    if _TRAFILATURA_OK and html:
        cfg = _build_trafilatura_config()
        # Markdown body — what we care about most.
        try:
            md_content = trafilatura.extract(  # type: ignore[union-attr]
                html,
                url=base_url,
                output_format="markdown",
                include_comments=True,
                include_tables=True,
                include_links=True,
                favor_recall=True,
                config=cfg,
            ) or ""
        except Exception as e:
            log.debug("trafilatura markdown extract failed: %s", e)

        # Plain-text body — for word count + non-markdown consumers.
        try:
            txt_content = trafilatura.extract(  # type: ignore[union-attr]
                html,
                url=base_url,
                output_format="txt",
                include_comments=True,
                include_tables=True,
                favor_recall=True,
                config=cfg,
            ) or ""
        except Exception as e:
            log.debug("trafilatura text extract failed: %s", e)

        # Metadata.
        try:
            meta = trafilatura.extract_metadata(html, default_url=base_url)  # type: ignore[union-attr]
            if meta:
                if meta.title:
                    out["title"] = meta.title
                if meta.author:
                    out["author"] = meta.author
                if meta.date:
                    out["date"] = str(meta.date)
                if meta.description:
                    out["description"] = meta.description
                if getattr(meta, "language", None):
                    out["language"] = meta.language
        except Exception as e:
            log.debug("trafilatura metadata failed: %s", e)

        if md_content or txt_content:
            extractor = "trafilatura"

    # Fallback: if trafilatura yielded nothing, dump cleaned body text.
    if not txt_content:
        txt_content = _fallback_text(page)
        if txt_content and not md_content:
            md_content = txt_content  # rough but better than nothing

    out["content_markdown"] = md_content
    out["content_text"] = txt_content
    out["word_count"] = _word_count(txt_content)
    out["extractor"] = extractor
    out["status"] = "ok" if (md_content or txt_content) else "empty"

    if include_links:
        out["links"] = _collect_links(page, base_url)
    if include_images:
        out["images"] = _collect_images(page, base_url)

    return out


def deep_fetch_urls(
    urls: list[str],
    *,
    browser=None,
    paginate: bool = True,
    max_scrolls: int = 2,
    timeout_ms: int = 30000,
    on_error: str = "skip",
) -> list[dict[str, Any]]:
    """Fetch + readability-extract a list of URLs.

    Useful as a follow-up to ``search`` so the agent can read the top-K
    results in one shot instead of N round-trips of ``extract``. Each URL
    gets its own fresh ``page`` (cookies / JS state isolated) but the
    same ``browser`` instance is reused, so the cost amortises nicely
    over the batch.

    Args:
        urls: list of URLs to fetch (in order).
        browser: optional existing CloakBrowser instance. When None we
            launch a fresh one and tear it down before returning.
        paginate: forwarded to ``extract_page``.
        max_scrolls: forwarded to ``extract_page``. Defaults to 2 here
            (vs 3 for the single-URL path) since deep-fetch usually
            wants speed over exhaustive scroll.
        on_error: ``"skip"`` returns a stub dict for failed fetches,
            ``"raise"`` re-raises the first exception.

    Returns:
        A list of extract dicts in the same order as ``urls``. Each item
        has the same shape as :func:`extract_page`'s return value, plus
        an ``input_url`` key mirroring the request.
    """
    own_browser = False
    if browser is None:
        from .core import BrowserConfig, launch  # local import; avoid hard dep at module load
        browser = launch(BrowserConfig(headless=True, humanize=True))
        own_browser = True

    out: list[dict[str, Any]] = []
    try:
        for url in urls:
            page = None
            try:
                page = browser.new_page()
                rec = extract_page(
                    page,
                    url=url,
                    paginate=paginate,
                    max_scrolls=max_scrolls,
                    include_links=False,
                    include_images=False,
                    timeout_ms=timeout_ms,
                )
                rec["input_url"] = url
                out.append(rec)
            except Exception as e:
                if on_error == "raise":
                    raise
                log.warning("[deep_fetch] %s failed: %s", url, e)
                out.append({
                    "input_url": url,
                    "url": url,
                    "status": "error",
                    "error": f"{type(e).__name__}: {e}",
                })
            finally:
                if page is not None:
                    try:
                        page.close()
                    except Exception:
                        pass
    finally:
        if own_browser:
            try:
                browser.close()
            except Exception:
                pass
    return out
