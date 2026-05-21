"""360 Search (so.com) adapter.

360's organic SERP at ``https://www.so.com/s?q=<q>`` is mostly
server-rendered: once the HTML lands the result list is in the DOM.
360 does push back against automation though — when it dislikes a
fingerprint or sees rate abuse it serves one of:

* ``https://www.so.com/captcha...`` — slider / click-the-character
  interstitial,
* the SERP itself, with a "请输入验证码" / "访问出错" / "安全验证"
  overlay,
* a "Forbidden" / blank page on outright blocks.

Strategy
--------

1. Set the browser locale to zh-CN before launching (the test does
   this); 360 serves a slightly different layout to en-US clients
   which is easier for it to flag.
2. Warm up at ``https://www.so.com/`` so cookies (Q, T, _S, …) settle,
   dismiss any privacy / login nudges, then submit the search.
3. Try several result-row selectors in priority order — 360 rotates
   markup but the dominant layouts are:
       ``#main .result``                  (organic + rich card wrappers)
       ``.res-list``                      (list-style organic rows)
       ``li.res-list``                    (older variant)
       ``#main > ul > li``                (last-resort)
4. Real URLs:
   * 360 wraps every external link in a ``/link?m=<sig>&u=<url>``
     redirector. Some rows expose the real URL through a ``data-url``
     / ``data-mdurl`` attribute on the row or title link — prefer
     that when present.
   * Otherwise return the redirector URL; it correctly resolves on
     click.
5. Snippet: the ``.res-desc``, ``.res-rich-info`` or ``.res-detail``
   block under the title. Multiple selectors tried in priority order.
6. Block detection via URL (``/captcha``, ``passport.360.cn``), title
   (``访问出错`` / ``验证码`` / ``安全验证``) and body phrases
   (``请输入验证码``, ``访问出错``, ``安全验证``, ``机器人``).

Diagnostics
-----------
* ``engine.last_status`` — ``url``, ``title``, ``body_len``,
  optional ``selector`` / ``block_reason`` / ``count``.
* ``engine.selector_counts()`` — element counts for every known
  result-row selector, useful when parsing returns 0.
"""

from __future__ import annotations

import logging
import random
import time
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

SO360_HOME = "https://www.so.com"

# Result-row selectors in priority order. The first one that returns
# hits is recorded on ``last_status['selector']``.
RESULT_SELECTORS = [
    "#main .result",
    "#main li.res-list",
    "#main .res-list",
    ".result.res-list",
    "ul.result li",
    "#main > ul > li",
]

# Title link selectors evaluated *inside* a result row.
TITLE_LINK_SELECTORS = [
    "h3.res-title a",
    ".res-title a",
    "h3 a",
    "a.res-title",
    "a[data-res-mdurl]",
    "a[href*='/link?']",
    "a[href]",  # last-resort: any link in the row
]

# Snippet selectors evaluated *inside* a result row.
SNIPPET_SELECTORS = [
    ".res-desc",
    ".res-rich-info",
    ".res-detail",
    ".res-comm-con",
    "p.res-desc",
    "[class*='res-desc']",
    "[class*='res-rich-info']",
    "[class*='res-detail']",
    ".res-newsinfo",
    ".text-layout",
]

# Source / "site name" line selectors evaluated *inside* a result row.
SOURCE_SELECTORS = [
    ".res-linkinfo cite",
    ".res-linkinfo",
    "cite",
    ".res-source",
    "[class*='res-source']",
    "[class*='linkinfo']",
    ".green",
]

# Buttons we may need to dismiss before / after navigation.
DISMISS_BUTTON_SELECTORS = [
    "[aria-label='关闭']",
    "[aria-label*='close' i]",
    "button:has-text('稍后再说')",
    "button:has-text('暂不')",
    "a:has-text('暂不')",
    ".close",
    ".btn-close",
    "#srf-mobile-app-close",
    ".so-popup-close",
]

# Phrases / URL fragments that indicate a captcha / security
# challenge / outright block.
BLOCK_URL_FRAGMENTS = (
    "/captcha",
    "passport.360.cn",
    "/forbidden",
    "/error.html",
)

BLOCK_PHRASES = (
    "请输入验证码",
    "访问出错",
    "安全验证",
    "请完成下方验证",
    "网络不给力",
    "网络繁忙",
    "访问异常",
    "搜索结果异常",
    "您的访问出错",
    "verify you are human",
    "captcha",
    "robot check",
)


def _is_so360_internal(href: str) -> bool:
    """Skip 360's own internal anchors (related searches, suggestions).

    The /link?m=... redirector is acceptable — that's how organic
    results are served. Filter only the SERP / suggestion / about
    pages.
    """
    if not href:
        return True
    if href.startswith("#") or href.startswith("javascript:"):
        return True
    if not (href.startswith("http://") or href.startswith("https://") or href.startswith("/")):
        return True
    try:
        parsed = urllib.parse.urlparse(href)
    except Exception:
        return False
    host = parsed.netloc.lower()
    path = parsed.path or ""
    if host in ("", "www.so.com", "so.com"):
        if path.startswith("/link"):
            return False
        return True
    return False


class So360Engine(BaseEngine):
    """360 Search adapter (https://www.so.com/s?q=<query>)."""

    name = "so360"
    max_retries = 3

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}

    # ------------------------------------------------------------------ search

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # Warm-up: hit the homepage so cookies settle and the search
        # form is loaded before we navigate to /s?q=...
        if safe_goto(self.page, SO360_HOME + "/", timeout=20000, retries=1):
            human_delay(1.0, 2.2)
            self._dismiss_overlays()
            self._human_hints()

        q = urllib.parse.quote(query)
        # pn = page number (1-indexed). ie ensures UTF-8 query.
        url = f"{SO360_HOME}/s?q={q}&pn=1&ie=utf-8"
        log.info("[so360] navigating to %s", url)
        if not safe_goto(self.page, url):
            return []

        human_delay(1.5, 3.0)
        self._dismiss_overlays()
        self._human_hints()

        if self._is_blocked():
            return []

        results = self._extract_results(limit)
        self.last_status["count"] = len(results)
        return results

    # -------------------------------------------------------------- diagnostics

    def selector_counts(self) -> dict[str, int]:
        """Return how many elements each result-row selector matches."""
        counts: dict[str, int] = {}
        for sel in RESULT_SELECTORS:
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        return counts

    # ------------------------------------------------------------------ helpers

    def _dismiss_overlays(self):
        """Best-effort close of login / privacy / nudge overlays."""
        for sel in DISMISS_BUTTON_SELECTORS:
            try:
                btn = self.page.query_selector(sel)
                if btn:
                    btn.click(timeout=2000)
                    log.info("[so360] dismissed overlay (%s)", sel)
                    human_delay(0.4, 1.0)
            except Exception:
                continue

    def _is_blocked(self) -> bool:
        try:
            url = (self.page.url or "").lower()
        except Exception:
            url = ""
        try:
            title = (self.page.title() or "")
        except Exception:
            title = ""
        try:
            body = self.page.inner_text("body") or ""
        except Exception:
            body = ""

        self.last_status = {
            "url": url,
            "title": title,
            "body_len": len(body),
        }

        for frag in BLOCK_URL_FRAGMENTS:
            if frag in url:
                log.warning("[so360] block url fragment: %r", frag)
                self.last_status["block_reason"] = f"url:{frag}"
                return True

        title_lc = title.lower()
        body_lc = body.lower()
        for phrase in BLOCK_PHRASES:
            p = phrase.lower()
            if p in body_lc or p in title_lc:
                log.warning("[so360] block phrase: %r", phrase)
                self.last_status["block_reason"] = phrase
                return True

        return False

    def _human_hints(self):
        try:
            self.page.mouse.move(
                random.randint(100, 400),
                random.randint(100, 400),
                steps=10,
            )
        except Exception:
            pass
        try:
            self.page.evaluate(
                "() => window.scrollBy(0, Math.floor(Math.random()*400) + 100)"
            )
        except Exception:
            pass
        time.sleep(random.uniform(0.3, 0.8))

    # ---------------------------------------------------------------- extraction

    def _extract_results(self, limit: int) -> list[SearchResult]:
        # Walk the result containers in priority order and accumulate
        # rows from each tier. 360's SERP intermixes organic
        # ``.result`` rows with ``.res-list`` rich cards; collecting
        # from multiple selectors and de-duping by handle picks up
        # both without double-counting.
        all_items: list[tuple[str, object]] = []
        seen_handles: set[int] = set()
        used_selectors: list[str] = []
        for sel in RESULT_SELECTORS:
            try:
                rows = self.page.query_selector_all(sel)
            except Exception:
                rows = []
            if not rows:
                continue
            new_rows = 0
            for row in rows:
                key = id(row)
                if key in seen_handles:
                    continue
                seen_handles.add(key)
                all_items.append((sel, row))
                new_rows += 1
            if new_rows:
                used_selectors.append(f"{sel}({new_rows})")
            # Stop once we have plenty of candidates to filter from.
            if len(all_items) >= limit * 4:
                break

        if not all_items:
            log.info("[so360] no result selector matched")
            self.last_status["selector"] = None
            return []

        log.info(
            "[so360] using selectors %s (%d rows total)",
            ", ".join(used_selectors), len(all_items),
        )
        self.last_status["selector"] = ", ".join(used_selectors)

        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        for _sel, r in all_items:
            # Title link. Try selectors in order; if a selector matches
            # but yields a very short string (e.g. an "官方" badge),
            # keep searching so we land on the real h3 element.
            title_el = None
            title = ""
            for sel in TITLE_LINK_SELECTORS:
                try:
                    cand = r.query_selector(sel)
                except Exception:
                    cand = None
                if not cand:
                    continue
                try:
                    cand_text = (cand.inner_text() or "").strip()
                except Exception:
                    cand_text = ""
                if not cand_text:
                    continue
                # Accept short matches only as a last resort.
                if len(cand_text) < 6 and title_el is not None:
                    continue
                title_el = cand
                title = cand_text
                if len(cand_text) >= 6:
                    break

            if not title_el or not title:
                continue

            try:
                href = title_el.get_attribute("href") or ""
            except Exception:
                href = ""

            # Prefer a row-level / title-level ``data-url`` /
            # ``data-mdurl`` / ``data-res-mdurl`` attribute — 360
            # sometimes puts the real destination URL there for
            # organic results, which lets us skip the /link?m=... redirector.
            real_url = ""
            for el in (title_el, r):
                for attr in ("data-url", "data-mdurl", "data-res-mdurl"):
                    try:
                        data_url = el.get_attribute(attr)
                    except Exception:
                        data_url = None
                    if data_url and (
                        data_url.startswith("http://") or data_url.startswith("https://")
                    ):
                        real_url = data_url
                        break
                if real_url:
                    break

            # If the redirector encodes the real URL in a ?u=... or
            # ?url=... query parameter, prefer that.
            if not real_url and href and "/link" in href:
                try:
                    parsed = urllib.parse.urlparse(href)
                    qs = urllib.parse.parse_qs(parsed.query)
                    for key in ("u", "url"):
                        vals = qs.get(key)
                        if vals and vals[0]:
                            candidate = urllib.parse.unquote(vals[0])
                            if candidate.startswith("http://") or candidate.startswith("https://"):
                                real_url = candidate
                                break
                except Exception:
                    pass

            if not real_url and href and not _is_so360_internal(href):
                # Resolve protocol-relative or root-relative hrefs.
                if href.startswith("//"):
                    real_url = "https:" + href
                elif href.startswith("/"):
                    real_url = SO360_HOME + href
                else:
                    real_url = href

            if not real_url:
                continue
            if real_url in seen_urls:
                continue
            seen_urls.add(real_url)

            # Snippet
            snippet = ""
            for sel in SNIPPET_SELECTORS:
                try:
                    snip_el = r.query_selector(sel)
                except Exception:
                    snip_el = None
                if snip_el:
                    try:
                        snippet = (snip_el.inner_text() or "").strip()
                    except Exception:
                        snippet = ""
                    if snippet:
                        break

            # Source / site-name line.
            source = ""
            for sel in SOURCE_SELECTORS:
                try:
                    src_el = r.query_selector(sel)
                except Exception:
                    src_el = None
                if not src_el:
                    continue
                try:
                    text = (src_el.inner_text() or "").strip()
                except Exception:
                    text = ""
                if not text:
                    continue
                # The site-name line is short; if we accidentally
                # picked up a long block, ignore it.
                if len(text) > 80:
                    continue
                source = text.splitlines()[0].strip()
                if source:
                    break

            # Compose snippet that surfaces the source even though
            # SearchResult's dataclass only has title/url/snippet/score.
            composed = snippet
            if source and source not in (snippet or ""):
                composed = f"[{source}] {snippet}".strip()

            sr = SearchResult(title=title, url=real_url, snippet=composed)
            # Attach the structured source as an extra attribute so
            # callers (and the test) can read it without parsing the
            # composed snippet.
            sr.source = source  # type: ignore[attr-defined]

            results.append(sr)
            if len(results) >= limit:
                break

        return results
