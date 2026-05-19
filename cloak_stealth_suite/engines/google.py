"""Google Search adapter with extra anti-bot stealth.

Features:
1. Random rotation between Google domains (.com / .co.uk / .ca).
2. Consent / cookie dialog handling, including consent.google.com iframe variants.
3. Multiple result-selector strategies (div.g, .tF2Cxc, data-sokoban-container,
   div.MjjYud, with #search h3 / #rso h3 fallback).
4. CAPTCHA / sorry-page detection via URL, title and body phrases.
5. Light human hints (mouse move + small scroll) and a homepage warm-up to
   make the visit look less like a fresh "search-only" hit.
"""

import logging
import random
import time
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

# Rotate between different Google domains/paths to reduce detection.
GOOGLE_DOMAINS = [
    "https://www.google.com",
    "https://www.google.com",
    "https://www.google.com",
    "https://www.google.ca",
]

# Selectors used to extract organic results, in priority order.
RESULT_SELECTORS = [
    "div.g",
    ".tF2Cxc",
    "div[data-sokoban-container]",
    "div.MjjYud",
]

# Buttons that accept consent across regional / layout variants.
CONSENT_BUTTON_SELECTORS = [
    "button#L2AGLb",                            # legacy "I agree"
    "button[aria-label*='Accept all' i]",       # English
    "button[aria-label*='Accept All' i]",
    "button[aria-label*='Akzeptieren' i]",      # German
    "button[aria-label*='Accepter' i]",         # French
    "button[aria-label*='Aceptar' i]",          # Spanish
    "form[action*='consent'] button",           # generic consent form
    "div[role='dialog'] button",                # cookie dialog button
]

# Phrases that indicate Google blocked us / showed CAPTCHA.
BLOCK_PHRASES = [
    "unusual traffic",
    "our systems have detected",
    "before you continue",
    "to continue, please type",
    "captcha",
    "i'm not a robot",
    "automated queries",
    "sending automated requests",
]


class GoogleEngine(BaseEngine):
    name = "google"
    max_retries = 3

    def __init__(self, page):
        super().__init__(page)
        # Diagnostics for callers / tests.
        self.last_status: dict = {}

    # ------------------------------------------------------------------ search

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        domain = random.choice(GOOGLE_DOMAINS)

        # Warm-up: hit the homepage first so we land with cookies set, then
        # accept consent before submitting the search query.
        if safe_goto(self.page, domain + "/", timeout=20000, retries=1):
            human_delay(1.5, 3)
            self._handle_consent()
            self._human_hints()

        q = urllib.parse.quote(query)
        url = f"{domain}/search?q={q}&hl=en&num={limit}"
        log.info("[google] navigating to %s", url)
        if not safe_goto(self.page, url):
            return []

        human_delay(2.5, 4.5)
        self._handle_consent()
        self._human_hints()

        if self._is_blocked():
            return []

        return self._extract_results(limit)

    # -------------------------------------------------------------- diagnostics

    def selector_counts(self) -> dict[str, int]:
        """Return the number of elements each result selector matches."""
        counts: dict[str, int] = {}
        for sel in RESULT_SELECTORS:
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        for sel in ("#search h3", "#rso h3"):
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        return counts

    # ------------------------------------------------------------------ helpers

    def _handle_consent(self):
        """Click consent / cookie acceptance, including iframe variants."""
        # 1) Buttons in the top-level frame.
        for sel in CONSENT_BUTTON_SELECTORS:
            try:
                btn = self.page.query_selector(sel)
                if btn:
                    btn.click(timeout=3000)
                    log.info("[google] clicked consent (%s)", sel)
                    human_delay(1, 2)
                    return
            except Exception:
                continue

        # 2) consent.google.com landing page is sometimes loaded inside an
        # iframe; walk every frame and try the same selectors.
        try:
            for frame in self.page.frames:
                furl = frame.url or ""
                if "consent" not in furl.lower():
                    continue
                for sel in CONSENT_BUTTON_SELECTORS:
                    try:
                        btn = frame.query_selector(sel)
                        if btn:
                            btn.click(timeout=3000)
                            log.info(
                                "[google] clicked consent inside frame %s (%s)",
                                furl, sel,
                            )
                            human_delay(1, 2)
                            return
                    except Exception:
                        continue
        except Exception:
            pass

    def _is_blocked(self) -> bool:
        """Detect CAPTCHA / sorry / unusual-traffic interstitial."""
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

        if "/sorry/" in url or "sorry" in title:
            log.warning("[google] sorry page: title=%r url=%r", title, url)
            self.last_status["block_reason"] = "sorry"
            return True

        for phrase in BLOCK_PHRASES:
            if phrase in body:
                log.warning("[google] block phrase detected: %r", phrase)
                self.last_status["block_reason"] = phrase
                return True

        return False

    def _human_hints(self):
        """Light human-like activity: mouse move + small scroll."""
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
        time.sleep(random.uniform(0.4, 1.0))

    # ---------------------------------------------------------------- extraction

    def _extract_results(self, limit: int) -> list[SearchResult]:
        """Extract results using multiple selector strategies."""
        items = []
        used = None
        for sel in RESULT_SELECTORS:
            items = self.page.query_selector_all(sel)
            if items:
                used = sel
                break

        if not items:
            log.info("[google] no result selector matched, falling back to h3")
            return self._extract_from_h3s(limit)

        log.info("[google] using selector %s (%d items)", used, len(items))

        results: list[SearchResult] = []
        for r in items[:limit]:
            title_el = r.query_selector("h3")
            link_el = (
                r.query_selector("a[href^='http']")
                or r.query_selector("a[href^='/url']")
            )
            snippet_el = r.query_selector(
                ".VwiC3b, [data-sncf], .lEBKkf, span.aCOpRe"
            )

            title = title_el.inner_text().strip() if title_el else ""
            href = link_el.get_attribute("href") if link_el else ""
            snippet = snippet_el.inner_text().strip() if snippet_el else ""

            # Clean Google redirect URLs.
            if href and href.startswith("/url?"):
                parsed = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
                href = parsed.get("q", [href])[0]

            if title and href and "google.com" not in href:
                results.append(SearchResult(title=title, url=href, snippet=snippet))

        return results

    def _extract_from_h3s(self, limit: int) -> list[SearchResult]:
        """Fallback: extract from h3 elements in the search area."""
        results: list[SearchResult] = []
        h3s = self.page.query_selector_all("#search h3, #rso h3")
        for h3 in h3s[:limit]:
            try:
                title = h3.inner_text().strip()
            except Exception:
                continue
            try:
                href = self.page.evaluate(
                    "(el) => { let p = el; while(p) { if(p.tagName === 'A' && p.href) return p.href; p = p.parentElement; } return ''; }",
                    h3,
                )
            except Exception:
                href = ""
            if title and href and "google.com" not in href:
                results.append(SearchResult(title=title, url=href))
        return results
