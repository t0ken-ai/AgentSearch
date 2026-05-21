"""Startpage adapter (startpage.com).

Startpage is a privacy-focused metasearch that proxies Google's results
without sending the user's IP / cookies upstream. Because it forwards
queries to Google on the back end the SERP layout looks similar (title
heading + URL line + description), but the markup is Startpage-native
Svelte/Vue components.

Strategy
--------
1. Navigate directly to ``https://www.startpage.com/sp/search?query=<q>``.
   (Startpage rewrote the older ``/do/search`` endpoint to ``/sp/search``;
   ``/do/search`` still 302s to ``/sp/search`` for backwards compat.)
2. Wait briefly for the results section. The container is a
   ``section.w-gl`` (or one of the ``[data-testid="..."]`` variants),
   wrapping per-result ``div.w-gl__result`` cards.
3. Extract organic web results with a chain of selector strategies.
   Startpage rotates class hashes between deployments, so we try the
   most stable hooks first (``.w-gl__result``, ``[data-testid*="result"]``)
   before falling back to "any anchor whose href is external + has a
   sibling description block".
4. Detect Cloudflare / CAPTCHA / "anomaly" interstitials via title /
   URL / body phrases — Startpage does occasionally serve them when
   the headers look automated.

Selectors observed (May 2024 / 2025 layouts):
- Container       : ``section.w-gl``, ``[data-testid="results"]``, ``#main``
- Web result card : ``div.w-gl__result``, ``section.w-gl > div``,
                    ``[data-testid="result"]``, ``.result``
- Title anchor    : ``a.w-gl__result-title``,
                    ``a[data-testid="gl-title-link"]``, ``h3 a``,
                    or just the first external ``<a>`` inside the card
- URL display     : ``.w-gl__result-url``, ``.w-gl__result__url``,
                    ``cite``
- Snippet         : ``.w-gl__description``, ``p.w-gl__description``,
                    ``[class*="description"]``, ``p``

Diagnostics
-----------
* ``engine.last_status`` — ``url``, ``title``, ``body_len``, optional
  ``selector`` / ``block_reason`` / ``count``.
* ``engine.selector_counts()`` — element counts for every known
  result-card selector.
"""

from __future__ import annotations

import logging
import random
import time
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

STARTPAGE_BASE = "https://www.startpage.com"

# Result-card selectors, in priority order.
RESULT_SELECTORS = [
    "div.w-gl__result",
    "section.w-gl div.w-gl__result",
    '[data-testid="result"]',
    'section.w-gl > div',
    "section.w-gl .result",
    ".result",
]

# Title-anchor selectors evaluated *inside* a result card.
TITLE_LINK_SELECTORS = [
    "a.w-gl__result-title",
    'a[data-testid="gl-title-link"]',
    "h3 a",
    "h2 a",
    "a.result-link",
    'a[href^="http"]',  # last resort: any external link
]

# Snippet selectors evaluated *inside* a result card.
SNIPPET_SELECTORS = [
    "p.w-gl__description",
    ".w-gl__description",
    '[data-testid="description"]',
    '[class*="description" i]',
    "p.description",
    "p",
]

# URL-line selectors evaluated *inside* a result card.
URL_LINE_SELECTORS = [
    ".w-gl__result-url",
    ".w-gl__result__url",
    'a[data-testid="gl-result-url"]',
    "cite",
    '[class*="result-url" i]',
    '[class*="result__url" i]',
]

# Block / challenge phrases.
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
    "anomaly detection",
    "checking your browser",
    "just a moment",  # Cloudflare interstitial
)

# URL fragments that indicate a block / challenge / error redirect.
BLOCK_URL_FRAGMENTS = (
    "/anomaly",
    "/cf-challenge",
    "/challenge",
    "/blocked",
    "/forbidden",
    "/error",
)

NO_RESULT_PHRASES = (
    "no results found",
    "we didn't find any results",
    "did not match any documents",
    "your search did not match",
)


def _is_startpage_internal(href: str) -> bool:
    """Return True for Startpage's own internal links (filters / settings /
    related searches), which we want to skip when building results."""
    if not href:
        return True
    if href.startswith("#") or href.startswith("javascript:"):
        return True
    if href.startswith("/"):
        return True  # any /do/..., /settings, /support, etc.
    try:
        parsed = urllib.parse.urlparse(href)
    except Exception:
        return False
    host = (parsed.netloc or "").lower()
    return host.endswith("startpage.com") or host.endswith("ixquick.com")


class StartpageEngine(BaseEngine):
    """Startpage adapter (https://www.startpage.com/sp/search?query=<q>)."""

    name = "startpage"
    max_retries = 2

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}

    # ------------------------------------------------------------------ search

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        q = urllib.parse.quote_plus(query)
        # `cat=web` pins the SERP to the web (vs images/news/videos) tab,
        # `pl=opensearch` mimics the standard browser search-bar entry
        # point, and `language=english` keeps the layout stable for our
        # selectors.
        url = (
            f"{STARTPAGE_BASE}/sp/search?query={q}&cat=web"
            f"&pl=opensearch&language=english"
        )
        log.info("[startpage] navigating to %s", url)

        if not safe_goto(self.page, url):
            return []

        human_delay(1.2, 2.5)

        # Wait for the results section, but don't fail hard if the
        # selector never appears — Startpage occasionally renders
        # results without the section wrapper, and we still try to
        # extract from whatever is present.
        for sel in ("section.w-gl", "div.w-gl__result", '[data-testid="results"]'):
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
        """Return how many elements each result-card selector matches."""
        counts: dict[str, int] = {}
        for sel in RESULT_SELECTORS:
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        for sel in ("section.w-gl a", "section.w-gl h3, section.w-gl h2"):
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        return counts

    # ------------------------------------------------------------------ helpers

    def _is_blocked(self) -> bool:
        """Detect CAPTCHA / Cloudflare / challenge / no-results page."""
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
                log.warning("[startpage] block url fragment: %r", frag)
                self.last_status["block_reason"] = f"url:{frag}"
                return True

        for phrase in BLOCK_PHRASES:
            if phrase in body or phrase in title:
                log.warning("[startpage] block phrase detected: %r", phrase)
                self.last_status["block_reason"] = phrase
                return True

        for phrase in NO_RESULT_PHRASES:
            if phrase in body:
                log.info("[startpage] no-results page: %r", phrase)
                self.last_status["block_reason"] = f"no_results:{phrase}"
                # No-results is not a block; just return [].
                return False

        return False

    def _human_hints(self):
        """Light human-like activity: mouse move + small scroll."""
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
        """Extract results using multiple selector strategies."""
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
            log.info("[startpage] using selector %s (%d cards)", used, len(items))
            self.last_status["selector"] = used
            results = self._extract_from_cards(items, limit)
            if results:
                return results
            log.info(
                "[startpage] cards matched but no usable results, "
                "trying anchor fallback"
            )

        return self._extract_from_anchors(limit)

    def _extract_from_cards(self, items, limit: int) -> list[SearchResult]:
        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        for r in items[: limit * 4]:
            # Title anchor: try selectors in priority order; if the
            # first match yields a too-short string (e.g. an "Ad"
            # label or a bare domain badge), keep searching.
            anchor = None
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
                if len(cand_text) < 6 and anchor is not None:
                    continue
                anchor = cand
                title = cand_text.split("\n")[0].strip()
                if len(cand_text) >= 6:
                    break

            if not anchor or not title:
                continue

            try:
                href = anchor.get_attribute("href") or ""
            except Exception:
                href = ""

            if not href or not href.startswith(("http://", "https://")):
                continue
            if _is_startpage_internal(href):
                continue
            if href in seen_urls:
                continue

            # Snippet
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

            # Display URL (cite-style line) — useful as an extra hint
            # for callers but not required.
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
            # Stash display URL so callers / tests can show it without
            # parsing the snippet. The dataclass only has
            # title/url/snippet/score, so we attach as a dynamic attr.
            if display_url:
                sr.display_url = display_url  # type: ignore[attr-defined]
            results.append(sr)
            if len(results) >= limit:
                break

        return results

    def _extract_from_anchors(self, limit: int) -> list[SearchResult]:
        """Last-resort fallback: scan all external anchors in the
        results region."""
        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        anchors = []
        for region in ("section.w-gl", '[data-testid="results"]', "#main", "body"):
            try:
                anchors = self.page.query_selector_all(f"{region} a[href^='http']")
            except Exception:
                anchors = []
            if anchors:
                log.info(
                    "[startpage] anchor fallback: %d <a> in %s",
                    len(anchors), region,
                )
                self.last_status["selector"] = f"anchors:{region}"
                break

        for a in anchors:
            try:
                href = a.get_attribute("href") or ""
            except Exception:
                continue
            if not href.startswith(("http://", "https://")):
                continue
            if _is_startpage_internal(href):
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
