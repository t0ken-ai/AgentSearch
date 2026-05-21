"""Qwant search adapter (qwant.com).

Qwant is a French / European privacy search engine that runs its own
crawler (also pulls supplementary results from Bing). The web SERP is
served by a React SPA at ``https://www.qwant.com/?q=<query>``; results
are rendered with stable ``data-testid`` attributes that we use as the
primary selectors.

Strategy
--------
1. Navigate directly to ``https://www.qwant.com/?q=<query>``.
2. Dismiss the cookie consent banner if it shows up.
3. Wait briefly for the result list (``[data-testid="webResult"]``).
4. Extract organic web results with a chain of selector strategies.
   Qwant rotates class hashes between deployments, but the
   ``data-testid`` hooks are stable.
5. Detect block / CAPTCHA via title / URL / body phrases.

Selectors observed (May 2025 layouts):
- Container       : ``[data-testid="sectionWeb"]`` / ``main``
- Web result card : ``[data-testid="webResult"]``,
                    ``article[data-testid*="webResult"]``,
                    ``article.result``, ``.web-result``
- Title anchor    : ``a[data-testid="serTitle"]``,
                    ``a[data-testid="webResultTitle"]``,
                    ``h2 a``, ``h3 a``, ``a[href^="http"]``
- Title text      : ``[data-testid="webResultTitle"]``,
                    ``h2``, ``h3``
- Snippet         : ``[data-testid="webResultDesc"]``,
                    ``[data-testid="serDescription"]``,
                    ``p[class*="desc"]``, ``p``
- Display URL     : ``[data-testid="webResultUrl"]``, ``cite``
"""

from __future__ import annotations

import logging
import random
import time
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

QWANT_BASE = "https://www.qwant.com"

RESULT_SELECTORS = [
    '[data-testid="webResult"]',
    'article[data-testid*="webResult"]',
    '[data-testid*="webResult"]',
    'div[data-testid="webResult"]',
    "article.result",
    ".web-result",
    "article",
]

TITLE_LINK_SELECTORS = [
    'a[data-testid="serTitle"]',
    'a[data-testid="webResultTitle"]',
    'h2 a',
    'h3 a',
    'a[href^="http"]',
]

TITLE_TEXT_SELECTORS = [
    '[data-testid="webResultTitle"]',
    '[data-testid="serTitle"]',
    "h2",
    "h3",
]

SNIPPET_SELECTORS = [
    '[data-testid="webResultDesc"]',
    '[data-testid="serDescription"]',
    '[data-testid*="description" i]',
    'p[class*="desc" i]',
    "p",
]

URL_LINE_SELECTORS = [
    '[data-testid="webResultUrl"]',
    '[data-testid*="url" i]',
    "cite",
    '[class*="result-url" i]',
]

CONSENT_BUTTON_SELECTORS = [
    'button[data-testid="cookieConsentAcceptAll"]',
    'button[data-testid="cookieConsentAccept"]',
    'button:has-text("Accept all")',
    'button:has-text("Accept")',
    'button:has-text("Agree")',
    'button:has-text("J\'accepte")',
    'button:has-text("Tout accepter")',
    'button[aria-label*="accept" i]',
    'button[aria-label*="accepter" i]',
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
    "aucun résultat",
)


def _is_qwant_internal(href: str) -> bool:
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
    return host.endswith("qwant.com")


class QwantEngine(BaseEngine):
    """Qwant search adapter (https://www.qwant.com/?q=<q>)."""

    name = "qwant"
    max_retries = 2

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}

    # ------------------------------------------------------------------ search

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        q = urllib.parse.quote_plus(query)
        # ``t=web`` pins the SERP to the web tab. ``locale`` keeps
        # selectors stable across regions.
        url = f"{QWANT_BASE}/?q={q}&t=web"
        log.info("[qwant] navigating to %s", url)

        if not safe_goto(self.page, url):
            return []

        human_delay(1.5, 3.0)
        self._dismiss_consent()

        for sel in (
            '[data-testid="webResult"]',
            'article[data-testid*="webResult"]',
            "article.result",
            "main article",
        ):
            try:
                self.page.wait_for_selector(sel, timeout=5000)
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
                    log.info("[qwant] dismissed consent (%s)", sel)
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
            log.info("[qwant] using selector %s (%d cards)", used, len(items))
            self.last_status["selector"] = used
            results = self._extract_from_cards(items, limit)
            if results:
                return results
            log.info(
                "[qwant] cards matched but no usable results, "
                "trying anchor fallback"
            )

        return self._extract_from_anchors(limit)

    def _extract_from_cards(self, items, limit: int) -> list[SearchResult]:
        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        for r in items[: limit * 4]:
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
                if _is_qwant_internal(href_check):
                    continue
                anchor = cand
                break

            if not anchor:
                continue

            try:
                href = anchor.get_attribute("href") or ""
            except Exception:
                href = ""
            if not href:
                continue
            if href in seen_urls:
                continue

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
                try:
                    text = (anchor.inner_text() or "").strip()
                except Exception:
                    text = ""
                lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
                if lines:
                    # Title is usually the longest non-URL line.
                    non_urls = [
                        ln for ln in lines
                        if not ln.startswith(("http://", "https://", "www."))
                    ]
                    title = (non_urls or lines)[0]
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
        for region in ('[data-testid="sectionWeb"]', "main", "body"):
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
            if _is_qwant_internal(href):
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
