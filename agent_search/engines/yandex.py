"""Yandex Search adapter.

Yandex's organic search at ``https://yandex.com/search/?text=<q>``
(international gateway) is a server-rendered SERP, so once the HTML
arrives the result list is fully present in the DOM — no Next.js-style
hydration needed. Anti-bot pressure is real (Yandex shows a
``showcaptcha`` interstitial when it dislikes a fingerprint) but
noticeably milder than Google's, which is why Yandex makes a useful
fallback engine in this suite.

Strategy
--------

1. **yandex_int** — primary: ``yandex.com/search/?text=<q>``.
2. **yandex_ru**  — secondary mirror: ``yandex.ru/search/?text=<q>``.
   Used when the international gateway returns 0 results / triggers a
   captcha. The HTML structure is the same; only the host differs.
3. Cookie / GDPR consent (``button#gdpr-popup-button``,
   ``button[data-id='button-all']``) is dismissed before parsing.
4. Multiple result-row selectors are tried in priority order so the
   adapter survives layout flips:
       ``li.serp-item`` → ``.serp-item`` →
       ``div.organic`` → ``ul#search-result > li``.
5. ``showcaptcha`` / ``Подтвердите, что запросы отправляли вы`` /
   ``Are you a robot?`` are detected and reported via
   ``last_status['block_reason']``.

Each ``SearchResult`` carries:
  * ``title``   — link text of the organic result.
  * ``url``     — the destination URL. Yandex does not wrap organic
    links in a redirector for plain ``serp-item`` rows, so the raw
    ``href`` is already the real target. Tracking ``yabs.yandex.*``
    /click? wrappers (ads / "продвинутые" rows) are skipped.
  * ``snippet`` — the description / passage text below the title.

Diagnostics
-----------
* ``engine.last_status`` — ``mode``, ``url``, ``title``, ``body_len``,
  optional ``selector`` / ``block_reason`` / ``count``.
* ``engine.selector_counts()`` — element counts for each known
  result-row selector, useful when a parsing miss is suspected.
"""

from __future__ import annotations

import logging
import random
import time
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

YANDEX_INT = "https://yandex.com"
YANDEX_RU = "https://yandex.ru"

# Result-row selectors in priority order. Yandex revises markup
# regularly; the first one that returns hits is recorded on
# ``last_status['selector']``.
RESULT_SELECTORS = [
    "li.serp-item",
    ".serp-item",
    "div.organic",
    "ul#search-result > li",
    "ul.serp-list > li",
]

# Title link selectors evaluated *inside* a result row.
TITLE_LINK_SELECTORS = [
    "a.OrganicTitle-Link",
    "a.organic__url",
    "a.Link.organic__url",
    "h2 a",
    ".OrganicTitle a",
    ".organic__url-link",
    "a[href]",            # last-resort: any link in the row
]

# Snippet selectors evaluated *inside* a result row.
SNIPPET_SELECTORS = [
    ".OrganicTextContentSpan",
    ".organic__text",
    ".TextContainer",
    ".organic__content-wrapper",
    ".extended-text",
    ".Organic-ContentWrapper",
]

# Buttons to dismiss for cookie / GDPR / regional consent prompts.
CONSENT_BUTTON_SELECTORS = [
    "button#gdpr-popup-button",
    "button[data-id='button-all']",
    "button.gdpr-popup-v3-button",
    "button[aria-label*='Accept' i]",
    "button[aria-label*='Принять' i]",
    "button:has-text('Accept all')",
    "button:has-text('Принять все')",
]

# Phrases that indicate Yandex showed a captcha / "are you a bot" wall.
BLOCK_PHRASES = [
    "are you a robot",
    "showcaptcha",
    "подтвердите, что запросы отправляли вы",
    "ой!",
    "captcha",
    "доступ ограничен",
    "слишком много запросов",
    "too many requests",
]

# Hosts we skip when collecting URLs — Yandex's own click-tracking
# subdomains and ad redirectors.
AD_HOST_FRAGMENTS = (
    "yabs.yandex",
    "/clck/",
    "an.yandex.ru",
    "direct.yandex",
)


def _is_ad_or_tracker(href: str) -> bool:
    if not href:
        return True
    h = href.lower()
    return any(frag in h for frag in AD_HOST_FRAGMENTS)


def _normalize_url(href: str) -> str:
    """Trim Yandex's own /redir/?... wrappers when present."""
    if not href:
        return href
    if href.startswith("/"):
        # Relative — usually internal, ignore.
        return ""
    # Yandex sometimes uses /redir/?to=<target> for organic result
    # links; unwrap when we can.
    try:
        parsed = urllib.parse.urlparse(href)
        if "yandex." in (parsed.netloc or "") and "/redir" in (parsed.path or ""):
            qs = urllib.parse.parse_qs(parsed.query)
            for key in ("to", "url", "u"):
                if key in qs and qs[key]:
                    return qs[key][0]
    except Exception:
        pass
    return href


class YandexEngine(BaseEngine):
    """Yandex search adapter (international gateway with .ru fallback)."""

    name = "yandex"
    max_retries = 3

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}
        self._last_mode: str | None = None

    # ------------------------------------------------------------------ search

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # Try the international gateway first; fall through to .ru on
        # captcha / empty.
        for mode, base in (("yandex_int", YANDEX_INT), ("yandex_ru", YANDEX_RU)):
            log.info("[yandex] trying mode=%s base=%s", mode, base)
            self._last_mode = mode
            results = self._search_one(base, query, limit, mode)
            if results:
                return results
            human_delay(1.5, 3.0)

        return []

    def _search_one(
        self, base: str, query: str, limit: int, mode: str
    ) -> list[SearchResult]:
        # Warm-up: hit the homepage so cookies/consent settle.
        if safe_goto(self.page, base + "/", timeout=20000, retries=1):
            human_delay(1.0, 2.2)
            self._handle_consent()
            self._human_hints()

        q = urllib.parse.quote(query)
        url = f"{base}/search/?text={q}"
        log.info("[yandex] navigating to %s", url)
        if not safe_goto(self.page, url):
            return []

        human_delay(1.5, 3.0)
        self._handle_consent()
        self._human_hints()

        if self._is_blocked(mode):
            return []

        results = self._extract_results(limit, mode)
        self.last_status["mode"] = mode
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

    def _handle_consent(self):
        for sel in CONSENT_BUTTON_SELECTORS:
            try:
                btn = self.page.query_selector(sel)
                if btn:
                    btn.click(timeout=3000)
                    log.info("[yandex] clicked consent (%s)", sel)
                    human_delay(0.6, 1.4)
                    return
            except Exception:
                continue

    def _is_blocked(self, mode: str) -> bool:
        try:
            url = (self.page.url or "").lower()
        except Exception:
            url = ""
        try:
            title = (self.page.title() or "").lower()
        except Exception:
            title = ""
        try:
            body = self.page.inner_text("body").lower()
        except Exception:
            body = ""

        self.last_status = {
            "mode": mode,
            "url": url,
            "title": title,
            "body_len": len(body),
        }

        # Captcha redirect: yandex.com/showcaptcha?... or
        # yandex.ru/showcaptcha?...
        if "showcaptcha" in url:
            log.warning("[yandex] showcaptcha URL: %s", url)
            self.last_status["block_reason"] = "showcaptcha_url"
            return True

        for phrase in BLOCK_PHRASES:
            if phrase in body or phrase in title:
                log.warning("[yandex] block phrase detected: %r", phrase)
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

    def _extract_results(self, limit: int, mode: str) -> list[SearchResult]:
        items = []
        used = None
        for sel in RESULT_SELECTORS:
            try:
                items = self.page.query_selector_all(sel)
            except Exception:
                items = []
            if items:
                used = sel
                break

        if not items:
            log.info("[yandex] no result selector matched for %s", mode)
            self.last_status["selector"] = None
            return []

        log.info("[yandex] using selector %s (%d items)", used, len(items))
        self.last_status["selector"] = used

        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        for r in items[: limit * 4]:  # over-fetch; some rows are ads / packs
            title_el = None
            for sel in TITLE_LINK_SELECTORS:
                try:
                    title_el = r.query_selector(sel)
                except Exception:
                    title_el = None
                if title_el:
                    break

            if not title_el:
                continue

            try:
                title = (title_el.inner_text() or "").strip()
            except Exception:
                title = ""
            try:
                href = title_el.get_attribute("href") or ""
            except Exception:
                href = ""

            if not title or not href:
                continue

            href = _normalize_url(href)
            if not href or _is_ad_or_tracker(href):
                continue

            # Drop obvious in-SERP suggestions (relative anchors, javascript:, etc.)
            if href.startswith("#") or href.startswith("javascript:"):
                continue
            if not (href.startswith("http://") or href.startswith("https://")):
                continue

            # Filter Yandex-internal "more results from this site" sublinks.
            host = urllib.parse.urlparse(href).netloc.lower()
            if host.endswith("yandex.com") or host.endswith("yandex.ru"):
                # Allow yandex content pages (e.g. dzen, market) but skip the
                # SERP itself / clck / yabs.
                if any(p in href for p in ("/search/", "/clck/", "yabs.")):
                    continue

            if href in seen_urls:
                continue
            seen_urls.add(href)

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

            results.append(
                SearchResult(title=title, url=href, snippet=snippet)
            )
            if len(results) >= limit:
                break

        return results
