"""Google Maps adapter — local business search.

Google Maps is the agent's biggest local-search blind spot in our default
toolkit. The two top open-source scrapers (gosom 4.1k⭐, omkarcloud 2.6k⭐)
have heavy demand exactly because Maps doesn't have a free public API for
the data agents actually want — name, rating, review count, address, phone,
website, category.

Strategy:
1. Hit https://www.google.com/maps/search/<query>/ — Maps' canonical
   query URL. Locale defaults to en; results auto-localise to the
   browser's locale otherwise.
2. Wait for the results feed (role="feed") to populate.
3. Scroll the feed panel a few times to surface more cards (results
   load lazily). We scroll inside the feed, not the whole page —
   Maps uses an internal scroll container.
4. For each card (role="article"), pull:
     * name           ← the first .fontHeadlineSmall / heading text
     * url            ← the canonical "Directions to / About this
                          place" anchor on the card
     * rating         ← parsed from "X.Y stars (Z reviews)" aria-label
     * review_count   ← same aria-label
     * address        ← text node after rating
     * category       ← text node before rating (e.g. "Restaurant")
     * phone          ← text matching phone-like pattern (best-effort)
     * website        ← anchor with aria-label "Website"
5. Stop when we've collected `limit` results.

Selectors are role / aria-based (Google's CSS classes are obfuscated and
change on every deploy). When the role tree is unavailable we fall back
to text scanning.
"""

import logging
import re
import time
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

# Maps consent gate (EU users) — click "Reject all" to land on the SERP.
CONSENT_BUTTONS = [
    'button[aria-label*="Reject"]',
    'button[aria-label*="reject"]',
    'button:has-text("Reject all")',
    'button:has-text("I agree")',
    'button[aria-label*="Accept"]',
]

# Number of times we scroll the results feed to surface more cards. Each
# scroll typically reveals 5-10 more places.
DEFAULT_SCROLLS = 3

# Pause between scrolls — too fast and Maps detects automation; too slow
# and we waste wall-clock time.
SCROLL_PAUSE_S = 1.2

# Used to extract "4.5 stars 123 reviews" from aria-label values.
RATING_RE = re.compile(
    r"(?P<rating>\d+(?:\.\d+)?)\s*(?:star|out of)\b"
    r".{0,20}?(?P<count>\d[\d,]*)\s*review",
    re.IGNORECASE,
)

# Fallback simpler pattern for "4.5" alone.
RATING_RE_SHORT = re.compile(r"(\d+(?:\.\d+)?)\s*star", re.IGNORECASE)

# Phone numbers — broad, intentionally permissive (Maps strings vary by
# locale). We post-filter to require at least 7 digits total.
PHONE_RE = re.compile(r"[\+\(]?[\d][\d\s\-\(\)\.]{6,}\d")


def _parse_rating(aria: str) -> tuple[float | None, int | None]:
    if not aria:
        return None, None
    m = RATING_RE.search(aria)
    if m:
        try:
            return float(m.group("rating")), int(m.group("count").replace(",", ""))
        except ValueError:
            pass
    m2 = RATING_RE_SHORT.search(aria)
    if m2:
        try:
            return float(m2.group(1)), None
        except ValueError:
            pass
    return None, None


def _phone_from_text(text: str) -> str | None:
    if not text:
        return None
    for m in PHONE_RE.finditer(text):
        candidate = m.group(0).strip()
        digit_count = sum(1 for ch in candidate if ch.isdigit())
        if digit_count >= 7:
            return candidate
    return None


class GoogleMapsEngine(BaseEngine):
    name = "google_maps"

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        q = urllib.parse.quote(query)
        url = f"https://www.google.com/maps/search/{q}/?hl=en"
        log.info("[gmaps] navigating to %s", url)
        if not safe_goto(self.page, url, timeout=45000):
            return []

        human_delay(2.0, 3.5)
        self._dismiss_consent()

        # Wait for either the feed (multi-result) or the place panel
        # (single-result, common when Maps thinks the query is unambiguous).
        try:
            self.page.wait_for_selector(
                '[role="feed"], [role="main"]',
                timeout=15000,
            )
        except Exception:
            log.warning("[gmaps] feed/main never appeared — likely consent-gated")

        # Scroll the feed panel a few times to surface lazy-loaded cards.
        scrolls_done = self._scroll_feed(DEFAULT_SCROLLS, target_count=limit)

        articles = self._collect_articles(limit)

        self.last_status = {
            "url": self.page.url,
            "scrolls": scrolls_done,
            "articles_seen": len(articles),
        }
        log.info(
            "[gmaps] %d articles after %d scrolls", len(articles), scrolls_done
        )

        results: list[SearchResult] = []
        for art in articles:
            r = self._parse_article(art)
            if r and r.title:
                results.append(r)
            if len(results) >= limit:
                break

        # Edge case: Maps showed a single-place panel rather than a feed.
        # Try to extract that one place if our feed parse came up empty.
        if not results:
            single = self._parse_single_place_panel()
            if single:
                results.append(single)

        return results

    # --------------------------------------------------------------- helpers

    def _dismiss_consent(self):
        for sel in CONSENT_BUTTONS:
            try:
                btn = self.page.query_selector(sel)
                if not btn:
                    continue
                btn.click(timeout=2500)
                log.info("[gmaps] dismissed consent (%s)", sel)
                human_delay(0.8, 1.4)
                return
            except Exception:
                continue

    def _scroll_feed(self, max_scrolls: int, target_count: int) -> int:
        """Scroll the inner feed container to surface more lazy-loaded cards.

        Maps uses an internal scrollable element with role="feed" — scrolling
        the window does nothing, you have to scroll inside the feed.
        """
        if max_scrolls <= 0:
            return 0

        scrolls = 0
        prev_count = 0
        for _ in range(max_scrolls):
            try:
                count = self.page.evaluate(
                    """
                    () => {
                        const feed = document.querySelector('[role="feed"]');
                        if (!feed) return 0;
                        feed.scrollTop = feed.scrollHeight;
                        return feed.querySelectorAll('[role="article"]').length;
                    }
                    """
                )
            except Exception:
                break
            scrolls += 1
            if count and count >= target_count and count == prev_count:
                # We have enough cards and the count stopped growing.
                break
            prev_count = count or 0
            time.sleep(SCROLL_PAUSE_S)
        return scrolls

    def _collect_articles(self, limit: int):
        try:
            articles = self.page.query_selector_all('[role="feed"] [role="article"]')
        except Exception:
            articles = []
        if not articles:
            try:
                # Fallback: any article on the page.
                articles = self.page.query_selector_all('[role="article"]')
            except Exception:
                articles = []
        return articles[: limit * 2]

    # ---------------------------------------------------------------- parsing

    def _parse_article(self, art) -> SearchResult | None:
        try:
            full_text = (art.inner_text() or "").strip()
        except Exception:
            full_text = ""

        # Name lives in the first heading-style anchor inside the card.
        name = ""
        try:
            link = art.query_selector("a[aria-label]")
            if link:
                aria = (link.get_attribute("aria-label") or "").strip()
                if aria and len(aria) < 200:
                    # aria-label is usually exactly the place name.
                    name = aria
        except Exception:
            pass

        # Some cards put the name as the first non-empty line of text.
        if not name and full_text:
            for line in full_text.split("\n"):
                line = line.strip()
                if line and len(line) < 100 and not line.replace(".", "").replace(",", "").isdigit():
                    name = line
                    break

        # URL — the place's canonical "/maps/place/..." anchor.
        href = ""
        try:
            for a in art.query_selector_all("a"):
                h = a.get_attribute("href") or ""
                if "/maps/place/" in h:
                    href = h
                    break
        except Exception:
            pass
        if href.startswith("/"):
            href = "https://www.google.com" + href

        # Rating + review count: try aria-label first (most reliable).
        rating: float | None = None
        review_count: int | None = None
        try:
            rated = art.query_selector('[role="img"][aria-label]')
            if rated:
                aria = rated.get_attribute("aria-label") or ""
                rating, review_count = _parse_rating(aria)
        except Exception:
            pass
        if rating is None and full_text:
            rating, review_count = _parse_rating(full_text)

        # Website link (when present, separate from the place link).
        website = ""
        try:
            for a in art.query_selector_all('a[aria-label]'):
                aria = (a.get_attribute("aria-label") or "").lower()
                if "website" in aria:
                    website = a.get_attribute("href") or ""
                    break
        except Exception:
            pass

        # Category / address — text tokens. Maps puts category before the
        # rating row and address (often with "·" separator) after it.
        category = ""
        address = ""
        if full_text:
            lines = [ln.strip() for ln in full_text.split("\n") if ln.strip()]
            # Heuristic: line containing "·" usually has category + sub-info
            # or address + sub-info. Take the longest line that looks
            # address-like (contains a digit, not all rating text).
            for ln in lines:
                if "·" in ln and not address and "review" not in ln.lower():
                    parts = [p.strip() for p in ln.split("·")]
                    # Last part of the dot-joined line is often the address
                    # when the line also contains digits.
                    last = parts[-1]
                    if any(ch.isdigit() for ch in last) and len(last) > 5:
                        address = last
                    if not category and parts and len(parts[0]) < 60:
                        category = parts[0]

        phone = _phone_from_text(full_text)

        snippet_bits = []
        if category:
            snippet_bits.append(category)
        if rating is not None:
            snippet_bits.append(
                f"⭐ {rating}" + (f" ({review_count} reviews)" if review_count else "")
            )
        if address:
            snippet_bits.append(address)
        snippet = " · ".join(snippet_bits)

        if not name:
            return None

        result = SearchResult(title=name, url=href, snippet=snippet, score=review_count)
        # Stamp engine-specific extras onto the dict so the JSON output gets them.
        result.__dict__["rating"] = rating
        result.__dict__["review_count"] = review_count
        result.__dict__["address"] = address
        result.__dict__["category"] = category
        result.__dict__["phone"] = phone
        result.__dict__["website"] = website
        return result

    def _parse_single_place_panel(self) -> SearchResult | None:
        """Maps sometimes goes straight to a single-place sidebar."""
        try:
            heading = self.page.query_selector('h1.DUwDvf, h1[class*="DUwDvf"], h1')
            if not heading:
                return None
            name = (heading.inner_text() or "").strip()
            if not name:
                return None
        except Exception:
            return None

        try:
            full = self.page.inner_text("body")
        except Exception:
            full = ""

        rating, review_count = _parse_rating(full or "")
        phone = _phone_from_text(full or "")
        website = ""
        try:
            wbtn = self.page.query_selector('a[aria-label*="Website"]')
            if wbtn:
                website = wbtn.get_attribute("href") or ""
        except Exception:
            pass

        snippet_bits = []
        if rating is not None:
            snippet_bits.append(f"⭐ {rating}" + (f" ({review_count} reviews)" if review_count else ""))
        snippet = " · ".join(snippet_bits)

        result = SearchResult(title=name, url=self.page.url, snippet=snippet, score=review_count)
        result.__dict__.update({
            "rating": rating, "review_count": review_count,
            "address": "", "category": "", "phone": phone, "website": website,
        })
        return result
