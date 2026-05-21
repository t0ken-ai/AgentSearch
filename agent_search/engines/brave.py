"""Brave Search adapter (search.brave.com).

Brave Search has relatively weak anti-bot defenses, making it a good
fallback when Google / Bing block us.

Strategy:
1. Navigate directly to https://search.brave.com/search?q=<query>&source=web.
2. Wait for the results container (`#results`) to appear.
3. Extract organic web results using a chain of selector strategies. Brave
   ships a Svelte UI whose class names change between deployments, so we
   try the most stable hooks first (`[data-type="web"]`, `#results .snippet`)
   before falling back to "any anchor with an `h*` heading inside #results".
4. Detect block / CAPTCHA via title / URL / body phrases.

Selectors observed (May 2024 / 2025 layouts):
- Container       : `#results`
- Web result card : `div.snippet[data-type="web"]`, `div[data-type="web"]`,
                    `#results .snippet`
- Title anchor    : `a.heading-serpresult`, `a.h`, `a[href]:has(.title)`,
                    or just the first `<a>` inside the card
- Title text      : `.title` (current), or the anchor's own text
- URL display     : `cite`, `.netloc`, or the anchor href itself
- Snippet         : `.snippet-description`, `.snippet-content`,
                    `[class*="description"]`, `p`
"""

from __future__ import annotations

import logging
import random
import time
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

BRAVE_BASE = "https://search.brave.com"

# Result-card selectors, in priority order.
RESULT_SELECTORS = [
    'div.snippet[data-type="web"]',
    'div[data-type="web"]',
    "#results div.snippet",
    "#results .snippet",
    "#results > div",
]

# Phrases that indicate Brave blocked us / showed a challenge.
BLOCK_PHRASES = [
    "verify you are human",
    "verify you're a human",
    "captcha",
    "unusual traffic",
    "automated requests",
    "access denied",
    "403 forbidden",
]

NO_RESULT_PHRASES = [
    "no results found",
    "we didn't find any results",
    "nothing came up",
]


class BraveEngine(BaseEngine):
    """Brave Search adapter."""

    name = "brave"
    max_retries = 2

    def __init__(self, page):
        super().__init__(page)
        # Diagnostics for callers / tests.
        self.last_status: dict = {}

    # ------------------------------------------------------------------ search

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        q = urllib.parse.quote_plus(query)
        url = f"{BRAVE_BASE}/search?q={q}&source=web"
        log.info("[brave] navigating to %s", url)

        if not safe_goto(self.page, url):
            return []

        human_delay(1.0, 2.2)

        # Wait for results container, but don't fail hard if the selector never
        # appears — we still try to extract from whatever rendered.
        try:
            self.page.wait_for_selector("#results", timeout=8000)
        except Exception as e:
            log.info("[brave] #results wait timed out: %s", e)

        self._human_hints()

        if self._is_blocked():
            return []

        return self._extract_results(limit)

    # -------------------------------------------------------------- diagnostics

    def selector_counts(self) -> dict[str, int]:
        """Return how many elements each selector matches (for tests)."""
        counts: dict[str, int] = {}
        for sel in RESULT_SELECTORS:
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        for sel in ("#results a", "#results h1, #results h2, #results h3"):
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        return counts

    # ------------------------------------------------------------------ helpers

    def _is_blocked(self) -> bool:
        """Detect CAPTCHA / challenge / no-results page."""
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

        for phrase in BLOCK_PHRASES:
            if phrase in body or phrase in title:
                log.warning("[brave] block phrase detected: %r", phrase)
                self.last_status["block_reason"] = phrase
                return True

        for phrase in NO_RESULT_PHRASES:
            if phrase in body:
                log.info("[brave] no-results page: %r", phrase)
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
            log.info("[brave] using selector %s (%d cards)", used, len(items))
            results = self._extract_from_cards(items, limit)
            if results:
                return results
            log.info("[brave] cards matched but no usable results, trying anchor fallback")

        return self._extract_from_anchors(limit)

    def _extract_from_cards(self, items, limit: int) -> list[SearchResult]:
        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        for r in items[: limit * 3]:
            # Title anchor: try a few hooks before falling back to the first <a>.
            anchor = (
                r.query_selector("a.heading-serpresult")
                or r.query_selector("a.h")
                or r.query_selector("a[href]:has(.title)")
                or r.query_selector("a[href]")
            )
            if not anchor:
                continue

            try:
                href = anchor.get_attribute("href") or ""
            except Exception:
                href = ""
            if not href or not href.startswith(("http://", "https://")):
                continue
            # Skip Brave's own internal links (filters, etc.).
            if href.startswith(BRAVE_BASE):
                continue

            # Title: try .title first, then anchor text.
            title = ""
            try:
                title_el = r.query_selector(".title") or anchor.query_selector(".title")
                if title_el:
                    title = (title_el.inner_text() or "").strip()
                if not title:
                    title = (anchor.inner_text() or "").strip().split("\n")[0]
            except Exception:
                title = ""

            # Snippet. Modern Brave wraps the description text in
            # `.generic-snippet .content` for organic results, or
            # `.inline-qa-question` / `.inline-qa-answer` for Reddit-style
            # Q&A inserts. Older / experimental layouts may still use
            # `.snippet-description`.
            snippet = ""
            try:
                snip_el = (
                    r.query_selector(".generic-snippet .content")
                    or r.query_selector(".generic-snippet")
                    or r.query_selector(".inline-qa-question")
                    or r.query_selector(".inline-qa-answer")
                    or r.query_selector(".snippet-description")
                    or r.query_selector(".snippet-content")
                    or r.query_selector('[class*="description" i]')
                    or r.query_selector("p")
                )
                if snip_el:
                    snippet = (snip_el.inner_text() or "").strip()
            except Exception:
                snippet = ""

            if not title:
                continue
            if href in seen_urls:
                continue
            seen_urls.add(href)

            results.append(SearchResult(title=title, url=href, snippet=snippet))
            if len(results) >= limit:
                break

        return results

    def _extract_from_anchors(self, limit: int) -> list[SearchResult]:
        """Last-resort fallback: scan all anchors inside #results."""
        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        try:
            anchors = self.page.query_selector_all("#results a[href]")
        except Exception:
            anchors = []

        log.info("[brave] anchor fallback found %d <a> in #results", len(anchors))

        for a in anchors:
            try:
                href = a.get_attribute("href") or ""
            except Exception:
                continue
            if not href.startswith(("http://", "https://")):
                continue
            if href.startswith(BRAVE_BASE):
                continue
            if href in seen_urls:
                continue

            try:
                title = (a.inner_text() or "").strip().split("\n")[0]
            except Exception:
                title = ""
            if not title or len(title) < 3:
                continue

            seen_urls.add(href)
            results.append(SearchResult(title=title, url=href, snippet=""))
            if len(results) >= limit:
                break

        return results
