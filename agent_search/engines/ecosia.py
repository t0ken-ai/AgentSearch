"""Ecosia search adapter (ecosia.org).

Ecosia is the Berlin-based "search engine that plants trees", which on
the backend mostly proxies Bing's organic results. The SERP markup is
proprietary (Ecosia-branded, Svelte / SvelteKit components) but
follows the standard SERP shape: result card with a title anchor, a
display URL line and a snippet paragraph.

Strategy
--------
1. Navigate to ``https://www.ecosia.org/search?q=<query>``.
2. Wait briefly for the result list — the most stable hook is
   ``[data-test-id="organic-result"]``, with ``.result`` /
   ``.mainline__result`` as fallbacks.
3. Extract organic web results with a chain of selector strategies.
   Ecosia rotates class hashes between deployments, so we try
   ``data-test-id`` first (test hooks are deliberately stable),
   then ``.result`` / ``.mainline__result``, and lastly any ``article``
   that contains an ``h2/h3`` plus an external anchor.
4. Detect block / CAPTCHA / consent banner via title / URL / body
   phrases. Ecosia rarely challenges, but the cookie banner on first
   load can hide content if not dismissed.

Selectors observed (May 2025 layouts):
- Container       : ``main`` / ``[data-test-id="mainline"]``
- Web result card : ``[data-test-id="organic-result"]`` (preferred),
                    ``article.result``, ``.mainline__result``,
                    ``article[data-test-id*="result"]``
- Title anchor    : ``a[data-test-id="result-link"]``,
                    ``a.result__title``, ``h2 a``, ``h3 a``,
                    ``a[href^="http"]`` (last resort)
- Snippet         : ``[data-test-id="result-description"]``,
                    ``.result__description``,
                    ``[class*="description"]``, ``p``
- Display URL     : ``[data-test-id="result-url"]``, ``cite``,
                    ``.result__url``
"""

from __future__ import annotations

import logging
import random
import time
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

ECOSIA_BASE = "https://www.ecosia.org"

RESULT_SELECTORS = [
    '[data-test-id="organic-result"]',
    'article[data-test-id*="result"]',
    "article.result",
    ".mainline__result",
    ".result",
    "article",
]

# Ecosia's organic-result anchor wraps both the display URL (first
# child <span>) and the title (<h2>). Inner-text on the anchor would
# return the URL line first, so we extract the title via the heading
# tag inside the anchor and the URL via the anchor's href.
TITLE_LINK_SELECTORS = [
    "a.result__title",
    "h2 a",
    "h3 a",
    'a[data-test-id="result-link"]',
    'a[href^="http"]',
]

# Title text selectors evaluated *inside* a result card (preferred over
# the anchor's full inner text, because the anchor often wraps the
# display-URL line too).
TITLE_TEXT_SELECTORS = [
    "h2.result__title",
    "h3.result__title",
    "h2",
    "h3",
    ".result__title",
]

SNIPPET_SELECTORS = [
    '[data-test-id="result-description"]',
    ".result__description",
    '[class*="description" i]',
    'p[data-test-id*="description" i]',
    "p",
]

URL_LINE_SELECTORS = [
    '[data-test-id="result-url"]',
    ".result__url",
    "cite",
    '[class*="result-url" i]',
]

CONSENT_BUTTON_SELECTORS = [
    'button[data-test-id="cookie-consent-accept-all"]',
    'button[data-test-id="cookie-consent-accept"]',
    'button:has-text("Accept all")',
    'button:has-text("Accept")',
    'button:has-text("I agree")',
    'button:has-text("Got it")',
    'button[aria-label*="accept" i]',
]

BLOCK_PHRASES = (
    "verify you are human",
    "verify you're a human",
    "captcha",
    "unusual traffic",
    "automated requests",
    "access denied",
    "403 forbidden",
    "rate limit",
    "too many requests",
    "checking your browser",
    "just a moment",
)

BLOCK_URL_FRAGMENTS = (
    "/cf-challenge",
    "/challenge",
    "/blocked",
    "/forbidden",
    "/anomaly",
)

NO_RESULT_PHRASES = (
    "no results found",
    "we didn't find any results",
    "did not match any documents",
)


def _is_ecosia_internal(href: str) -> bool:
    if not href:
        return True
    if href.startswith(("#", "javascript:")):
        return True
    if href.startswith("/"):
        return True
    try:
        parsed = urllib.parse.urlparse(href)
    except Exception:
        return False
    host = (parsed.netloc or "").lower()
    return host.endswith("ecosia.org")


class EcosiaEngine(BaseEngine):
    """Ecosia search adapter (https://www.ecosia.org/search?q=<q>)."""

    name = "ecosia"
    max_retries = 2

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}

    # ------------------------------------------------------------------ search

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        q = urllib.parse.quote_plus(query)
        url = f"{ECOSIA_BASE}/search?q={q}"
        log.info("[ecosia] navigating to %s", url)

        if not safe_goto(self.page, url):
            return []

        human_delay(1.0, 2.2)
        self._dismiss_consent()

        for sel in (
            '[data-test-id="organic-result"]',
            "article.result",
            ".mainline__result",
            "main article",
        ):
            try:
                self.page.wait_for_selector(sel, timeout=4000)
                break
            except Exception:
                continue

        self._human_hints()

        if self._is_blocked():
            return []

        results = self._extract_results(limit)
        self.last_status["count"] = len(results)
        return results

    # -------------------------------------------------------------- diagnostics

    def selector_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for sel in RESULT_SELECTORS:
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        for sel in ("main a", "main h2, main h3"):
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        return counts

    # ------------------------------------------------------------------ helpers

    def _dismiss_consent(self):
        for sel in CONSENT_BUTTON_SELECTORS:
            try:
                btn = self.page.query_selector(sel)
                if btn:
                    btn.click(timeout=2000)
                    log.info("[ecosia] dismissed consent (%s)", sel)
                    human_delay(0.4, 0.9)
                    return
            except Exception:
                continue

    def _is_blocked(self) -> bool:
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
            "url": url,
            "title": title,
            "body_len": len(body),
        }

        for frag in BLOCK_URL_FRAGMENTS:
            if frag in url:
                self.last_status["block_reason"] = f"url:{frag}"
                return True

        for phrase in BLOCK_PHRASES:
            if phrase in body or phrase in title:
                self.last_status["block_reason"] = phrase
                return True

        for phrase in NO_RESULT_PHRASES:
            if phrase in body:
                self.last_status["block_reason"] = f"no_results:{phrase}"
                return False
        return False

    def _human_hints(self):
        try:
            self.page.mouse.move(
                random.randint(120, 500),
                random.randint(120, 400),
                steps=8,
            )
        except Exception:
            pass
        try:
            self.page.evaluate(
                "() => window.scrollBy(0, Math.floor(Math.random()*300) + 80)"
            )
        except Exception:
            pass
        time.sleep(random.uniform(0.3, 0.7))

    # ---------------------------------------------------------------- extraction

    def _extract_results(self, limit: int) -> list[SearchResult]:
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

        if items:
            log.info("[ecosia] using selector %s (%d cards)", used, len(items))
            self.last_status["selector"] = used
            results = self._extract_from_cards(items, limit)
            if results:
                return results
            log.info(
                "[ecosia] cards matched but no usable results, "
                "trying anchor fallback"
            )

        return self._extract_from_anchors(limit)

    def _extract_from_cards(self, items, limit: int) -> list[SearchResult]:
        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        for r in items[: limit * 4]:
            # Title anchor: try h2/h3 anchors first (cleanest title),
            # then any anchor with a heading inside, then the test-id
            # anchor (which wraps URL + title), and finally any HTTP
            # anchor in the card.
            anchor = None
            for sel in TITLE_LINK_SELECTORS:
                try:
                    cand = r.query_selector(sel)
                except Exception:
                    cand = None
                if not cand:
                    continue
                try:
                    href_check = cand.get_attribute("href") or ""
                except Exception:
                    href_check = ""
                if not href_check.startswith(("http://", "https://")):
                    continue
                if _is_ecosia_internal(href_check):
                    continue
                anchor = cand
                break

            if not anchor:
                continue

            try:
                href = anchor.get_attribute("href") or ""
            except Exception:
                href = ""
            if not href or not href.startswith(("http://", "https://")):
                continue
            if _is_ecosia_internal(href):
                continue
            if href in seen_urls:
                continue

            # Title text: prefer the heading tag in the card so we
            # avoid the display-URL line that the test-id anchor
            # wraps as its first child.
            title = ""
            for sel in TITLE_TEXT_SELECTORS:
                try:
                    title_el = r.query_selector(sel)
                except Exception:
                    title_el = None
                if not title_el:
                    continue
                try:
                    text = (title_el.inner_text() or "").strip()
                except Exception:
                    text = ""
                if text and len(text) >= 4:
                    title = text.split("\n")[0].strip()
                    break
            if not title:
                # Fallback: use the anchor's inner text but pick the
                # last non-empty line (display URL is the first line,
                # title is usually the last).
                try:
                    text = (anchor.inner_text() or "").strip()
                except Exception:
                    text = ""
                lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
                if lines:
                    title = lines[-1]
            if not title:
                continue

            snippet = ""
            for sel in SNIPPET_SELECTORS:
                try:
                    snip_el = r.query_selector(sel)
                except Exception:
                    snip_el = None
                if not snip_el:
                    continue
                try:
                    text = (snip_el.inner_text() or "").strip()
                except Exception:
                    text = ""
                if text and len(text) > 10:
                    snippet = text
                    break

            display_url = ""
            for sel in URL_LINE_SELECTORS:
                try:
                    url_el = r.query_selector(sel)
                except Exception:
                    url_el = None
                if not url_el:
                    continue
                try:
                    text = (url_el.inner_text() or "").strip()
                except Exception:
                    text = ""
                if text and len(text) < 200:
                    display_url = text.splitlines()[0].strip()
                    break

            seen_urls.add(href)
            sr = SearchResult(title=title, url=href, snippet=snippet)
            if display_url:
                sr.display_url = display_url  # type: ignore[attr-defined]
            results.append(sr)
            if len(results) >= limit:
                break

        return results

    def _extract_from_anchors(self, limit: int) -> list[SearchResult]:
        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        anchors = []
        for region in ("main", '[data-test-id="mainline"]', "body"):
            try:
                anchors = self.page.query_selector_all(f"{region} a[href^='http']")
            except Exception:
                anchors = []
            if anchors:
                self.last_status["selector"] = f"anchors:{region}"
                break

        for a in anchors:
            try:
                href = a.get_attribute("href") or ""
            except Exception:
                continue
            if not href.startswith(("http://", "https://")):
                continue
            if _is_ecosia_internal(href):
                continue
            if href in seen_urls:
                continue
            try:
                title = (a.inner_text() or "").strip().split("\n")[0]
            except Exception:
                title = ""
            if not title or len(title) < 5:
                continue
            seen_urls.add(href)
            results.append(SearchResult(title=title, url=href, snippet=""))
            if len(results) >= limit:
                break

        return results
