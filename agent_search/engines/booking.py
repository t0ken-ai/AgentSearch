"""Booking.com adapter — hotel / accommodation search.

Booking.com is one of the canonical "DataDome heavy" travel sites — most
naive scrapers get 403'd within a few requests. CloakBrowser's stealth
patches get us through; what's left is parsing a DOM that Booking
A/B tests heavily.

Strategy:
1. Hit ``https://www.booking.com/searchresults.html?ss=<query>``.
2. Dismiss the cookie consent banner (EU users).
3. Wait for the property card grid to render.
4. For each card (``[data-testid='property-card']``), pull:
     * name           ← ``[data-testid='title']`` text
     * url            ← anchor wrapping the title (deep link with
                          query params we strip down)
     * rating         ← ``[data-testid='review-score'] / .a3b8729ab1``
                          aria-label parses cleanly
     * review_count   ← matched from "(<n> reviews)" pattern
     * price          ← ``[data-testid='price-and-discounted-price']``
     * area           ← ``[data-testid='address']`` text
     * stars          ← ``[data-testid='rating-stars']`` aria-label
5. We do not auto-paginate — Booking SERPs already pack 25 hotels per
   page, which is plenty for an agent.

Login note: Booking lets anonymous users browse SERPs; only checkout
needs login. The default flow here is anonymous.
"""

import logging
import re
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

CONSENT_BUTTONS = [
    'button#onetrust-accept-btn-handler',
    'button[aria-label*="Accept" i]',
    'button:has-text("Accept")',
    'button:has-text("OK")',
    'button[data-tt="cookies-accept"]',
]

CARD_SELECTORS = [
    "[data-testid='property-card']",
    "div.sr_property_block",
    "div.sr-card",
]

PRICE_RE = re.compile(
    r"(?:US\$|\$|€|£|¥|HK\$|S\$|A\$|C\$)\s*[\d,]+(?:\.\d+)?",
    re.IGNORECASE,
)

REVIEWS_RE = re.compile(r"([\d,]+)\s*reviews?", re.IGNORECASE)


class BookingEngine(BaseEngine):
    name = "booking"

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        q = urllib.parse.quote(query)
        url = (
            "https://www.booking.com/searchresults.html"
            f"?ss={q}&lang=en-us"
        )
        log.info("[booking] %s", url)
        if not safe_goto(self.page, url, timeout=30000):
            return []
        human_delay(2.0, 3.5)

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
            log.warning("[booking] no property cards matched")
            return []
        log.info("[booking] selector %s → %d cards", used, len(cards))

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
                if not btn:
                    continue
                btn.click(timeout=2000)
                log.info("[booking] dismissed consent")
                human_delay(0.6, 1.2)
                return
            except Exception:
                continue

    def _parse_card(self, card) -> SearchResult | None:
        try:
            text = (card.inner_text() or "").strip()
        except Exception:
            text = ""

        # Title + URL.
        title = ""
        href = ""
        try:
            t_el = (
                card.query_selector("[data-testid='title']")
                or card.query_selector("a[data-testid='title-link']")
                or card.query_selector("h3 a")
                or card.query_selector("h2 a")
            )
            if t_el:
                title = (t_el.inner_text() or "").strip()
                href = t_el.get_attribute("href") or ""
                if not href:
                    a = t_el.query_selector("a") or t_el.evaluate_handle("e => e.closest('a')")
                    if a and hasattr(a, "get_attribute"):
                        href = a.get_attribute("href") or ""
            if not href:
                # Card-wide anchor.
                a = card.query_selector("a[data-testid='title-link']") or card.query_selector("a")
                if a:
                    href = a.get_attribute("href") or ""
        except Exception:
            pass
        if href.startswith("/"):
            href = "https://www.booking.com" + href

        # Rating.
        rating: float | None = None
        try:
            rs_el = card.query_selector("[data-testid='review-score']")
            if rs_el:
                aria = (rs_el.get_attribute("aria-label") or "").strip()
                m = re.search(r"(\d+(?:\.\d+)?)", aria or rs_el.inner_text() or "")
                if m:
                    try:
                        rating = float(m.group(1))
                    except ValueError:
                        pass
        except Exception:
            pass

        # Review count.
        review_count: int | None = None
        rc = REVIEWS_RE.search(text)
        if rc:
            try:
                review_count = int(rc.group(1).replace(",", ""))
            except ValueError:
                pass

        # Price.
        price = ""
        try:
            p_el = (
                card.query_selector("[data-testid='price-and-discounted-price']")
                or card.query_selector("[data-testid='price']")
                or card.query_selector("span.bui-price-display__value")
            )
            if p_el:
                price = (p_el.inner_text() or "").strip()
        except Exception:
            pass
        if not price:
            m = PRICE_RE.search(text)
            if m:
                price = m.group(0).strip()

        # Area / address.
        area = ""
        try:
            a_el = (
                card.query_selector("[data-testid='address']")
                or card.query_selector("span[data-testid='address']")
            )
            if a_el:
                area = (a_el.inner_text() or "").strip()
        except Exception:
            pass

        # Star rating (1-5 stars displayed as icons).
        stars: int | None = None
        try:
            star_el = card.query_selector("[data-testid='rating-stars'], [data-testid='rating-squares']")
            if star_el:
                aria = (star_el.get_attribute("aria-label") or "").lower()
                m = re.search(r"(\d)\s*(?:star|squares?)", aria)
                if m:
                    stars = int(m.group(1))
                else:
                    # Fallback: count star icon children.
                    stars = len(star_el.query_selector_all("[data-testid='star-icon'], svg"))
                    if stars == 0:
                        stars = None
        except Exception:
            pass

        snippet_bits = []
        if stars:
            snippet_bits.append("⭐" * stars)
        if rating is not None:
            snippet_bits.append(f"score {rating}")
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
            "stars": stars,
        })
        return result
