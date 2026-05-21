"""Bing Search adapter with consent handling and multi-selector strategy.

Features:
1. Visits bing.com/search?q=... with a homepage warm-up so cookies / consent
   land before the search request.
2. Multiple result-selector strategies (#b_results li.b_algo / .b_algo /
   ol#b_results > li / li.b_ans), with #b_results h2 / li h2 fallback.
3. Cookie consent dialog handling, including the EU "bnp_btn_accept" /
   "id_button_accept" privacy notice and dialog-button fallbacks; also walks
   iframes whose URL contains "consent" / "privacy".
4. Block / no-results detection via URL, title, and body phrases (Bing's
   "no results" / "we couldn't find" / "verify you are human").
5. Bing /ck/a redirect cleaning: extracts the real `u=` parameter and
   base64-decodes when needed.
6. Light human hints (mouse move + small scroll) and `human_delay` jitter
   between navigations.
"""

import base64
import logging
import random
import time
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

BING_HOME = "https://www.bing.com"

# Selectors used to extract organic results, in priority order.
RESULT_SELECTORS = [
    "#b_results li.b_algo",
    ".b_algo",
    "ol#b_results > li.b_algo",
    "ol#b_results > li",
]

# Buttons that accept consent across regional / layout variants.
CONSENT_BUTTON_SELECTORS = [
    "button#bnp_btn_accept",                    # standard Bing privacy notice
    "button#bnp_hfly_accept",                   # alt heavy-flyout id
    "button#id_button_accept",                  # EU consent variant
    "a#bnp_btn_accept",                         # link-styled accept
    "button[aria-label*='Accept' i]",
    "button[aria-label*='Akzeptieren' i]",
    "button[aria-label*='Accepter' i]",
    "button[aria-label*='Aceptar' i]",
    "button[title*='Accept' i]",
    "div#bnp_container button",                 # generic fallback inside notice
    "div[role='dialog'] button",
]

# Phrases that indicate Bing blocked us / showed CAPTCHA / no results.
BLOCK_PHRASES = [
    "verify you are human",
    "verify you're a human",
    "verifying you are human",
    "captcha",
    "unusual traffic",
    "automated requests",
    "this site can't be reached",
]

NO_RESULT_PHRASES = [
    "there are no results for",
    "we couldn't find any results",
    "no results found for",
]


def _decode_bing_redirect(href: str) -> str:
    """Bing wraps outbound clicks in https://www.bing.com/ck/a?...&u=<...>&...

    The `u` parameter is usually a URL prefixed with "a1" and base64-encoded
    (URL-safe alphabet). Decode it back to the real destination. Falls back to
    the raw href if anything goes wrong.
    """
    try:
        if "/ck/a" not in href:
            return href
        parsed = urllib.parse.urlparse(href)
        qs = urllib.parse.parse_qs(parsed.query)
        u = qs.get("u", [""])[0]
        if not u:
            return href

        # Common form: "a1aHR0cHM6Ly8..." (a1 prefix + base64 URL-safe).
        candidate = u
        if candidate.startswith("a1"):
            candidate = candidate[2:]

        # Restore base64 padding.
        pad = (-len(candidate)) % 4
        candidate += "=" * pad

        try:
            decoded = base64.urlsafe_b64decode(candidate).decode("utf-8", "ignore")
        except Exception:
            return href

        if decoded.startswith("http://") or decoded.startswith("https://"):
            return decoded
        return href
    except Exception:
        return href


class BingEngine(BaseEngine):
    name = "bing"
    max_retries = 3

    def __init__(self, page):
        super().__init__(page)
        # Diagnostics for callers / tests.
        self.last_status: dict = {}

    # ------------------------------------------------------------------ search

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # Warm-up: hit the homepage first so cookies and consent settle, then
        # accept consent before submitting the search query.
        if safe_goto(self.page, BING_HOME + "/", timeout=20000, retries=1):
            human_delay(1.2, 2.5)
            self._handle_consent()
            self._human_hints()

        q = urllib.parse.quote(query)
        url = f"{BING_HOME}/search?q={q}&count={limit}"
        log.info("[bing] navigating to %s", url)
        if not safe_goto(self.page, url):
            return []

        human_delay(1.5, 3.0)
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
        for sel in ("#b_results h2", "#b_content h2"):
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
                    log.info("[bing] clicked consent (%s)", sel)
                    human_delay(0.8, 1.6)
                    return
            except Exception:
                continue

        # 2) Some EU layouts load the privacy notice inside an iframe; walk
        # every frame and try the same selectors when the URL hints at it.
        try:
            for frame in self.page.frames:
                furl = (frame.url or "").lower()
                if not any(k in furl for k in ("consent", "privacy", "bing.com")):
                    continue
                for sel in CONSENT_BUTTON_SELECTORS:
                    try:
                        btn = frame.query_selector(sel)
                        if btn:
                            btn.click(timeout=3000)
                            log.info(
                                "[bing] clicked consent inside frame %s (%s)",
                                furl, sel,
                            )
                            human_delay(0.8, 1.6)
                            return
                    except Exception:
                        continue
        except Exception:
            pass

    def _is_blocked(self) -> bool:
        """Detect CAPTCHA / unusual-traffic / no-results interstitial."""
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

        # Bing's challenge surface is much smaller than Google's, but still
        # reachable through cloud edge / abuse responses.
        for phrase in BLOCK_PHRASES:
            if phrase in body or phrase in title:
                log.warning("[bing] block phrase detected: %r", phrase)
                self.last_status["block_reason"] = phrase
                return True

        # Empty-result page (legitimate query but Bing returned nothing). Not a
        # block per se, but worth flagging so callers can retry / rephrase.
        for phrase in NO_RESULT_PHRASES:
            if phrase in body:
                log.info("[bing] no-results page: %r", phrase)
                self.last_status["block_reason"] = f"no_results:{phrase}"
                # Treat as non-block so we don't spin retries; just return [].
                return False

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
        time.sleep(random.uniform(0.3, 0.8))

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

        if not items:
            log.info("[bing] no result selector matched, falling back to h2")
            return self._extract_from_h2s(limit)

        log.info("[bing] using selector %s (%d items)", used, len(items))

        results: list[SearchResult] = []
        for r in items[: limit * 2]:  # over-fetch; some may be ads / news cards
            title_el = r.query_selector("h2 a") or r.query_selector("a.tilk")
            snippet_el = r.query_selector(
                ".b_caption p, .b_lineclamp2, .b_paractl, .b_snippet"
            )

            try:
                title = title_el.inner_text().strip() if title_el else ""
            except Exception:
                title = ""
            try:
                href = title_el.get_attribute("href") if title_el else ""
            except Exception:
                href = ""
            try:
                snippet = snippet_el.inner_text().strip() if snippet_el else ""
            except Exception:
                snippet = ""

            if href:
                href = _decode_bing_redirect(href)

            if title and href:
                results.append(SearchResult(title=title, url=href, snippet=snippet))
            if len(results) >= limit:
                break

        return results

    def _extract_from_h2s(self, limit: int) -> list[SearchResult]:
        """Fallback: extract from h2 elements in the result area."""
        results: list[SearchResult] = []
        try:
            h2s = self.page.query_selector_all("#b_results h2, #b_content h2")
        except Exception:
            h2s = []
        for h2 in h2s[:limit]:
            try:
                a = h2.query_selector("a")
                if not a:
                    continue
                title = (a.inner_text() or "").strip()
                href = a.get_attribute("href") or ""
            except Exception:
                continue
            if href:
                href = _decode_bing_redirect(href)
            if title and href:
                results.append(SearchResult(title=title, url=href))
        return results
