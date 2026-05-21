"""Baidu Search adapter.

Baidu's organic SERP at ``https://www.baidu.com/s?wd=<q>`` is mostly
server-rendered: once the HTML lands, the result list is in the DOM and
no further hydration is needed. Baidu does push back against
automation though — when it dislikes a fingerprint or sees rate
abuse it serves one of:

* ``https://wappass.baidu.com/static/captcha/...`` — slider /
  click-the-character interstitial,
* the SERP itself, with a "百度安全验证" / "请输入验证码" /
  "网络不给力" / "请稍候" overlay,
* a "robots.txt" / "Forbidden" page on outright blocks.

Strategy
--------

1. Set the browser locale to zh-CN before launching (the test does
   this); Baidu serves a different layout to en-US clients which is
   easier for it to flag.
2. Warm up at ``https://www.baidu.com/`` so cookies (BAIDUID,
   BIDUPSID, H_PS_PSSID …) settle, dismiss any privacy / "添加到主屏"
   nudges, then submit the search.
3. Try several result-row selectors in priority order — Baidu rotates
   markup frequently:
       ``#content_left .result.c-container[mu]`` (organic with real URL)
       ``#content_left .result.c-container``
       ``#content_left .result-op.c-container``       (rich cards)
       ``#content_left > div[id][class*=c-container]`` (last-resort)
4. Real URLs:
   * Prefer the ``mu`` attribute on the row — Baidu stores the actual
     destination there for organic results.
   * Otherwise fall back to the title link's ``href``, which is a
     ``https://www.baidu.com/link?url=...`` redirector. Returning the
     redirector URL is acceptable (it 302s to the real page on click).
5. Source / 来源: the small grey "site name · 时间" line below the
   title. Stored both as ``r.source`` (instance attribute) and
   prepended to the snippet so callers that only look at
   ``SearchResult`` fields still see it.
6. Block detection via URL (``wappass.baidu.com``, ``passport.baidu``,
   ``/forbidden``), title (``百度安全验证`` / ``robot``) and body
   phrases (``请输入验证码``, ``网络不给力``, ``百度安全验证``,
   ``访问异常``).

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

BAIDU_HOME = "https://www.baidu.com"

# Result-row selectors in priority order. The first one that returns
# hits is recorded on ``last_status['selector']``.
RESULT_SELECTORS = [
    "#content_left .result.c-container[mu]",
    "#content_left .result.c-container",
    "#content_left .result-op.c-container",
    "#content_left > div.c-container",
    "#content_left > div[class*='c-container']",
]

# Title link selectors evaluated *inside* a result row.
TITLE_LINK_SELECTORS = [
    "h3.t a",
    "h3 a",
    "a.c-title",
    ".c-title a",
    ".t a",
    "a[href*='baidu.com/link']",
    "a[href]",                 # last-resort: any link in the row
]

# Snippet selectors evaluated *inside* a result row. Baidu has cycled
# through many class names; we try the recent ones first.
SNIPPET_SELECTORS = [
    ".c-abstract",
    "[data-module='abstract']",
    ".content-right_8Zs40",
    ".content-right_2s-H4",
    ".c-span-last",
    "[class*='content-right']",
    "[class*='abstract']",
    ".c-row",
]

# Source / "site name" line selectors evaluated *inside* a result row.
SOURCE_SELECTORS = [
    ".c-color-gray",
    ".c-color-gray2",
    "[class*='source-text']",
    "[class*='c-source']",
    ".c-showurl",
    "[class*='showurl']",
    "[class*='c-color-gray']",
]

# Buttons we may need to dismiss before / after navigation. Baidu's
# desktop SERP is light on consent prompts, but the homepage sometimes
# pushes login or "add to home screen" overlays.
DISMISS_BUTTON_SELECTORS = [
    "a.s-top-login-btn",                       # login top-right (no-op click is harmless to skip)
    ".passMod_dialog-container .pass-button",  # login modal
    ".se-bind-mobile-close",                   # bind-mobile prompt
    "button:has-text('稍后再说')",
    "button:has-text('暂不')",
    "a:has-text('暂不')",
    "[aria-label='关闭']",
    "[aria-label*='close' i]",
    ".tang-pass-pop-close",
]

# Phrases / URL fragments that indicate a captcha / security
# challenge / outright block.
BLOCK_URL_FRAGMENTS = (
    "wappass.baidu.com",
    "passport.baidu.com/v2/",
    "/forbidden",
    "/error.html",
)

BLOCK_PHRASES = (
    "百度安全验证",
    "请输入验证码",
    "请完成下方验证",
    "网络不给力",
    "网络繁忙",
    "访问异常",
    "verify you are human",
    "captcha",
    "robot check",
    "搜索结果异常",
)


def _is_baidu_internal(href: str) -> bool:
    """Skip Baidu's own internal anchors (related searches, suggestions)."""
    if not href:
        return True
    if href.startswith("#") or href.startswith("javascript:"):
        return True
    if not (href.startswith("http://") or href.startswith("https://") or href.startswith("/")):
        return True
    try:
        host = urllib.parse.urlparse(href).netloc.lower()
    except Exception:
        return False
    # The /link? redirector is fine — that's how organic results are
    # served. Only filter the SERP / suggestion / about-baidu pages.
    if host in ("", "www.baidu.com", "baidu.com"):
        path = urllib.parse.urlparse(href).path or ""
        if path.startswith("/link"):
            return False
        return True
    return False


class BaiduEngine(BaseEngine):
    """Baidu search adapter (https://www.baidu.com/s?wd=<query>)."""

    name = "baidu"
    max_retries = 3

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}

    # ------------------------------------------------------------------ search

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # Warm-up: hit the homepage so cookies settle and the search
        # form is loaded before we navigate to /s?wd=...
        if safe_goto(self.page, BAIDU_HOME + "/", timeout=20000, retries=1):
            human_delay(1.0, 2.2)
            self._dismiss_overlays()
            self._human_hints()

        q = urllib.parse.quote(query)
        # rn = result count per page (max ~50). ie ensures UTF-8 query.
        url = f"{BAIDU_HOME}/s?wd={q}&rn={max(limit, 10)}&ie=utf-8"
        log.info("[baidu] navigating to %s", url)
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
                    log.info("[baidu] dismissed overlay (%s)", sel)
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
                log.warning("[baidu] block url fragment: %r", frag)
                self.last_status["block_reason"] = f"url:{frag}"
                return True

        title_lc = title.lower()
        body_lc = body.lower()
        for phrase in BLOCK_PHRASES:
            p = phrase.lower()
            if p in body_lc or p in title_lc:
                log.warning("[baidu] block phrase: %r", phrase)
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
        # rows from each tier. Baidu's SERP is layered:
        #   - .result.c-container[mu]  → pure organic (3-5 rows usually)
        #   - .result.c-container      → organic without mu attr (rare)
        #   - .result-op.c-container   → rich cards (百科 / 百家号 / 视频 / 新闻),
        #                                still valid web results, just decorated
        # Stopping at the first selector that matches misses the rich
        # cards that often make up the bulk of a Chinese-query SERP.
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
            log.info("[baidu] no result selector matched")
            self.last_status["selector"] = None
            return []

        log.info(
            "[baidu] using selectors %s (%d rows total)",
            ", ".join(used_selectors), len(all_items),
        )
        self.last_status["selector"] = ", ".join(used_selectors)

        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        for _sel, r in all_items:
            # Title link. Try selectors in order; if a selector matches
            # but yields a very short string (e.g. an "官方" badge or a
            # tiny "更多" link) keep searching so we land on the real
            # h3 / c-title element.
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

            # Prefer the row-level ``mu`` attribute — Baidu puts the
            # real destination URL there for organic results, which
            # lets us skip the /link?url=... redirector.
            real_url = ""
            try:
                mu = r.get_attribute("mu")
            except Exception:
                mu = None
            if mu and (mu.startswith("http://") or mu.startswith("https://")):
                real_url = mu
            elif href and not _is_baidu_internal(href):
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

            # Source / site-name line. Filter out things that are
            # actually the snippet captured by an over-broad selector.
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
