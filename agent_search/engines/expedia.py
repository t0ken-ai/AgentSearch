"""Expedia adapter — hotel search (free-text destination).

Expedia is structurally similar to Booking — same DataDome-class anti-bot,
similar card-grid SERP. Free-text queries work for hotels only; flights
need structured origin/destination/dates and are deferred.

URL: ``https://www.expedia.com/Hotel-Search?destination=<query>``

Strategy mirrors ``booking.py``:
1. Navigate, dismiss the cookie/onboarding banners.
2. Wait for the property list (``ul[data-stid='property-list']`` items, or
   ``[data-stid='lodging-card-responsive']`` cards).
3. Parse name, url, rating, review_count, price, area.

Selectors here are stid-based (``data-stid``) which Expedia changes less
often than its CSS class names.
"""

import logging
import re
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

CONSENT_BUTTONS = [
    'button[data-testid="accept-cookies"]',
    'button[aria-label*="Accept" i]',
    'button:has-text("Accept all")',
    'button:has-text("Got it")',
]

CARD_SELECTORS = [
    "[data-stid='lodging-card-responsive']",
    "li[data-stid='lodging-card-responsive']",
    "[data-stid='property-card']",
    "ul[data-stid='property-list'] li",
]

PRICE_RE = re.compile(
    r"(?:US\$|\$|€|£|¥|HK\$|S\$|A\$|C\$)\s*[\d,]+(?:\.\d+)?", re.IGNORECASE
)
REVIEWS_RE = re.compile(r"\(?([\d,]+)\)?\s*reviews?", re.IGNORECASE)
RATING_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:/\s*10)?(?:\s*out of)?", re.IGNORECASE)


class ExpediaEngine(BaseEngine):
    name = "expedia"

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        q = urllib.parse.quote(query)
        url = (
            "https://www.expedia.com/Hotel-Search"
            f"?destination={q}&adults=2"
        )
        log.info("[expedia] %s", url)
        if not safe_goto(self.page, url, timeout=35000):
            return []
        human_delay(2.5, 4.0)
        self._dismiss_consent()

        cards = []
        used = None
        for sel in CARD_SELECTORS:
            try:
                cards = self.page.query_selector_all(sel)
            except Exception:
                cards = []
            if cards:
                used = sel
                break
        if not cards:
            log.warning("[expedia] no property cards matched")
            return []
        log.info("[expedia] selector %s → %d cards", used, len(cards))

        results: list[SearchResult] = []
        for c in cards[: limit * 2]:
            r = self._parse_card(c)
            if r and r.title:
                results.append(r)
            if len(results) >= limit:
                break
        return results

    def _dismiss_consent(self):
        for sel in CONSENT_BUTTONS:
            try:
                btn = self.page.query_selector(sel)
                if btn:
                    btn.click(timeout=2000)
                    human_delay(0.4, 0.9)
                    return
            except Exception:
                continue

    def _parse_card(self, card) -> SearchResult | None:
        try:
            text = (card.inner_text() or "").strip()
        except Exception:
            text = ""

        title = ""
        href = ""
        try:
            t_el = (
                card.query_selector("h3")
                or card.query_selector("h2")
                or card.query_selector("[data-stid='content-hotel-title']")
            )
            if t_el:
                title = (t_el.inner_text() or "").strip()
            a = card.query_selector("a[data-stid='open-hotel-information']") or card.query_selector("a[href*='/h']")
            if a:
                href = a.get_attribute("href") or ""
        except Exception:
            pass
        if href.startswith("/"):
            href = "https://www.expedia.com" + href

        # Rating (Expedia uses /10 score) — match via aria-label or text.
        rating: float | None = None
        try:
            r_el = card.query_selector("[aria-label*='out of 10' i]") or card.query_selector(
                "[data-stid='content-hotel-reviews-badge']"
            )
            aria = ""
            if r_el:
                aria = (r_el.get_attribute("aria-label") or r_el.inner_text() or "").strip()
            m = RATING_RE.search(aria or "")
            if m:
                try:
                    rating = float(m.group(1))
                except ValueError:
                    pass
        except Exception:
            pass

        review_count: int | None = None
        rc = REVIEWS_RE.search(text)
        if rc:
            try:
                review_count = int(rc.group(1).replace(",", ""))
            except ValueError:
                pass

        price = ""
        try:
            p_el = card.query_selector("[data-stid='price-summary']") or card.query_selector(
                "[data-test-id='price-summary-message-line']"
            )
            if p_el:
                price = (p_el.inner_text() or "").strip()
        except Exception:
            pass
        if not price:
            m = PRICE_RE.search(text)
            if m:
                price = m.group(0).strip()

        area = ""
        try:
            a_el = card.query_selector("[data-stid='content-hotel-neighborhood']")
            if a_el:
                area = (a_el.inner_text() or "").strip()
        except Exception:
            pass

        snippet_bits = []
        if rating is not None:
            snippet_bits.append(f"{rating}/10")
        if review_count is not None:
            snippet_bits.append(f"{review_count} reviews")
        if area:
            snippet_bits.append(area)
        if price:
            snippet_bits.append(price)
        snippet = " · ".join(snippet_bits)

        if not title:
            return None
        result = SearchResult(title=title, url=href, snippet=snippet, score=review_count)
        result.__dict__.update({
            "rating": rating,
            "review_count": review_count,
            "price": price,
            "area": area,
        })
        return result
