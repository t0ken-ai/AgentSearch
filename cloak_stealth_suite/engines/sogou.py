"""Sogou Search adapter.

Sogou's organic SERP at ``https://www.sogou.com/web?query=<q>`` is
mostly server-rendered: once the HTML lands, the result list is in
the DOM and no further hydration is needed. Sogou does push back
against automation though — when it dislikes a fingerprint or sees
rate abuse it serves one of:

* ``https://www.sogou.com/antispider/...`` — captcha / "您的访问出错"
  interstitial,
* the SERP itself, with a "请输入验证码" / "访问出错" / "用户您好"
  overlay,
* a "Forbidden" / blank page on outright blocks.

Strategy
--------

1. Set the browser locale to zh-CN before launching (the test does
   this); Sogou serves a slightly different layout to en-US clients
   which is easier for it to flag.
2. Warm up at ``https://www.sogou.com/`` so cookies (SUID, SUV, IPLOC,
   …) settle, dismiss any privacy / login nudges, then submit the
   search.
3. Try several result-row selectors in priority order — Sogou rotates
   markup but the dominant layouts are:
       ``.results .vrwrap``            (organic + rich card wrappers)
       ``.results .rb``                (legacy organic row)
       ``.result``                     (some sub-pages)
       ``#main .vrwrap``               (last-resort)
4. Real URLs:
   * Sogou wraps every external link in a ``/link?url=...`` redirector
     (302). Some rows expose the real URL through a ``data-url``
     attribute on the row or title link — prefer that when present.
   * Otherwise return the redirector URL; it correctly resolves on
     click.
5. Snippet: the ``.fz-mid``, ``.str_info``, ``.ft`` or ``.space-txt``
   block under the title. Multiple selectors tried in priority order.
6. Block detection via URL (``/antispider/``, ``/antispider.so``,
   ``passport.sogou.com``), title (``访问出错`` / ``验证码``) and body
   phrases (``请输入验证码``, ``访问出错``, ``机器人``, ``用户您好``).

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

SOGOU_HOME = "https://www.sogou.com"

# Result-row selectors in priority order. The first one that returns
# hits is recorded on ``last_status['selector']``.
RESULT_SELECTORS = [
    ".results .vrwrap",
    ".results .rb",
    ".results > div[class*='vrwrap']",
    "#main .vrwrap",
    "#main .rb",
    ".result",
]

# Title link selectors evaluated *inside* a result row.
TITLE_LINK_SELECTORS = [
    "h3.vr-title a",
    "h3.vrTitle a",
    "h3.pt a",
    "h3 a",
    ".vr-title a",
    ".vrTitle a",
    "a.title-link",
    "a[href*='/link?url=']",
    "a[href]",  # last-resort: any link in the row
]

# Snippet selectors evaluated *inside* a result row.
SNIPPET_SELECTORS = [
    ".fz-mid.space-txt",
    ".fz-mid",
    ".str_info",
    ".str-info",
    ".ft",
    ".space-txt",
    "[class*='space-txt']",
    "[class*='str-info']",
    "[class*='str_info']",
    ".text-layout",
    "p.star-wiki",
]

# Source / "site name" line selectors evaluated *inside* a result row.
SOURCE_SELECTORS = [
    ".citeurl",
    ".cite-url",
    ".cite",
    "[class*='citeurl']",
    "[class*='cite-url']",
    ".fb",
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
    ".sogou-pop-close",
]

# Phrases / URL fragments that indicate a captcha / security
# challenge / outright block.
BLOCK_URL_FRAGMENTS = (
    "/antispider/",
    "antispider.so",
    "passport.sogou.com",
    "/forbidden",
    "/error.html",
)

BLOCK_PHRASES = (
    "请输入验证码",
    "访问出错",
    "用户您好",
    "您的访问出错",
    "请完成下方验证",
    "网络不给力",
    "网络繁忙",
    "访问异常",
    "搜索结果异常",
    "我们的工作人员已经发现了这个问题",
    "verify you are human",
    "captcha",
    "robot check",
)


def _is_sogou_internal(href: str) -> bool:
    """Skip Sogou's own internal anchors (related searches, suggestions).

    The /link?url=... redirector is acceptable — that's how organic
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
    if host in ("", "www.sogou.com", "sogou.com"):
        if path.startswith("/link"):
            return False
        return True
    return False


class SogouEngine(BaseEngine):
    """Sogou search adapter (https://www.sogou.com/web?query=<query>)."""

    name = "sogou"
    max_retries = 3

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}

    # ------------------------------------------------------------------ search

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # Warm-up: hit the homepage so cookies settle and the search
        # form is loaded before we navigate to /web?query=...
        if safe_goto(self.page, SOGOU_HOME + "/", timeout=20000, retries=1):
            human_delay(1.0, 2.2)
            self._dismiss_overlays()
            self._human_hints()

        q = urllib.parse.quote(query)
        # num = result count per page (Sogou caps at ~10 in some
        # layouts; request 10 minimum). ie ensures UTF-8.
        url = f"{SOGOU_HOME}/web?query={q}&num={max(limit, 10)}&ie=utf-8"
        log.info("[sogou] navigating to %s", url)
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
                    log.info("[sogou] dismissed overlay (%s)", sel)
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
                log.warning("[sogou] block url fragment: %r", frag)
                self.last_status["block_reason"] = f"url:{frag}"
                return True

        title_lc = title.lower()
        body_lc = body.lower()
        for phrase in BLOCK_PHRASES:
            p = phrase.lower()
            if p in body_lc or p in title_lc:
                log.warning("[sogou] block phrase: %r", phrase)
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
        # rows from each tier. Sogou's SERP intermixes organic
        # ``.vrwrap`` rows with the legacy ``.rb`` rows; collecting
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
            log.info("[sogou] no result selector matched")
            self.last_status["selector"] = None
            return []

        log.info(
            "[sogou] using selectors %s (%d rows total)",
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

            # Prefer a row-level / title-level ``data-url`` attribute
            # — Sogou puts the real destination URL there for some
            # organic results, which lets us skip the /link?url=...
            # redirector.
            real_url = ""
            for el in (title_el, r):
                try:
                    data_url = el.get_attribute("data-url")
                except Exception:
                    data_url = None
                if data_url and (data_url.startswith("http://") or data_url.startswith("https://")):
                    real_url = data_url
                    break

            if not real_url and href and not _is_sogou_internal(href):
                # Resolve protocol-relative or root-relative hrefs.
                if href.startswith("//"):
                    real_url = "https:" + href
                elif href.startswith("/"):
                    real_url = SOGOU_HOME + href
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
