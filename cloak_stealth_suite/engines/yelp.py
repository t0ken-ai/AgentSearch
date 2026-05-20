"""Yelp business search adapter.

Yelp exposes a public, login-free search at:

    https://www.yelp.com/search?find_desc=<query>&find_loc=<location>

The page is server-rendered with full business cards even for unauthenticated
users, but Yelp also runs:

* A Cloudflare / PerimeterX-style anti-bot layer (the infamous
  "press and hold" / "Just a moment..." challenge) on hot IP ranges.
* A location modal ("What's your home address?" / "Set your location")
  when ``find_loc`` is missing or ambiguous. Always pass a city.
* A GDPR cookie banner for EU traffic.

Card layout (Yelp SERP, 2024–2025) — modern build uses CSS-modules with
hashed suffixes (``y-css-...``), so we anchor on stable testids and
walk by content/href shape:

    <div data-testid="serp-ia-card">
      <h3>
        <a href="/biz/joes-pizza-new-york-2?osq=...">Joe's Pizza</a>
      </h3>
      <div role="img" aria-label="4.5 star rating"></div>
      <span>4.5</span>                          <!-- numeric rating -->
      <span>(1,234 reviews)</span>              <!-- review count -->
      <span>Greenwich Village</span>            <!-- neighborhood -->
      <span>$$</span>                           <!-- price range -->
      <span>Open until 4:00 AM</span>           <!-- hours -->
      <a href="/search?find_desc=Pizza&find_loc=...">Pizza</a>,
      <a href="/search?find_desc=Italian&find_loc=...">Italian</a>
    </div>

The SERP does **not** render full street addresses — only neighborhood /
locality names. We surface that as ``r.address`` (the closest equivalent
visible on a SERP card); full street addresses are only available on the
business detail page (``/biz/<slug>``).

We do all parsing in one ``page.evaluate`` JS pass and canonicalize the
business URL to ``https://www.yelp.com/biz/<slug>`` so callers always get
a stable, shareable link (the in-card anchor carries an ``?osq=...``
query-string that depends on the originating SERP).

Sponsored / promoted cards have anchors that point at ``/adredir?...``
instead of ``/biz/<slug>``. We skip those — only organic ``/biz/`` cards
make it into the result list.

Returned ``SearchResult`` extension fields
------------------------------------------
* ``r.rating``        — float-as-string, e.g. ``"4.5"``
* ``r.review_count``  — string, e.g. ``"1,234"`` or ``"3k"`` (Yelp short form)
* ``r.category``      — string, e.g. ``"Pizza, Italian"``
* ``r.address``       — string (neighborhood as shown on SERP),
                        e.g. ``"Greenwich Village"``
* ``r.price_range``   — string, e.g. ``"$$"`` (one to four ``$`` signs)
* ``r.slug``          — string, e.g. ``"joes-pizza-new-york-2"``
"""

from __future__ import annotations

import logging
import random
import time
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)


_SEARCH_URL = "https://www.yelp.com/search"

# How long to wait for the first business card to render (ms).
_RESULT_WAIT_MS = 25_000
# Per-attempt navigation timeout (ms).
_NAV_TIMEOUT_MS = 40_000

# Selectors used to count / wait for result cards. Tried in order;
# whichever matches first wins. Yelp shuffles markup between A/B variants
# so we list multiple shapes (the JS parser tries them in the same order).
#
# The most stable anchor is the ``serp-ia-card`` testid, which Yelp has
# kept stable across the recent React rewrites. ``mainAttributes`` and
# any ``[data-testid]`` containing a ``/biz/`` link are kept as fallbacks.
_RESULT_SELECTORS: tuple[str, ...] = (
    'div[data-testid="serp-ia-card"]',
    'li[data-testid^="serp-ia-card"]',
    'div.businessName__09f24__HG_pC',
    # Generic fallback: any container with a /biz/ link as a direct child.
    'div.mainAttributes__09f24__26-vh',
)

# Cookie / GDPR consent buttons. Yelp uses OneTrust on EU traffic;
# best-effort only.
_CONSENT_BUTTON_SELECTORS: tuple[str, ...] = (
    "#onetrust-accept-btn-handler",
    "button#onetrust-accept-btn-handler",
    "button[aria-label*='Accept' i]",
    "button:has-text('Accept all')",
    "button:has-text('Accept All')",
    "button:has-text('I Accept')",
)

# Modal-dismiss selectors for Yelp's "set your location" / signup popovers.
_MODAL_DISMISS_SELECTORS: tuple[str, ...] = (
    "button[aria-label='Close']",
    "button[aria-label='close']",
    "button[aria-label*='close' i]",
    "button[aria-label*='Dismiss' i]",
    # Yelp-specific signup overlay close button (CSS modules hash suffix —
    # we only match the prefix).
    "button[class*='signupModal']",
)

# URL fragments / titles / phrases that mean Yelp forced us through their
# anti-bot challenge.
_BLOCK_URL_FRAGMENTS: tuple[str, ...] = (
    "/blocked",
    "/captcha",
    "perimeterx.net",
)
_BLOCK_TITLE_PHRASES: tuple[str, ...] = (
    "just a moment",
    "access denied",
    "press & hold",
    "press and hold",
    "verify you are a human",
)
_BLOCK_BODY_PHRASES: tuple[str, ...] = (
    "press & hold to confirm",
    "press and hold to confirm",
    "checking your browser before",
    "verifying you are human",
    "additional verification required",
    "please verify you are a human",
)


# JS that walks every business card and pulls a structured row. We pass
# the host string in so the parser can absolutise the relative ``href``
# and rewrite the path to ``/biz/<slug>`` (stripping the ``?osq=...``
# tracking query).
#
# Yelp's SERP uses CSS-modules with hashed suffixes (``y-css-...``) that
# change between deploys, so we don't depend on stable class names.
# Instead we classify the text content of each ``<span>`` inside the card
# by shape:
#
#   "4.5"                  -> rating (also available via aria-label)
#   "(1,234 reviews)"      -> review count
#   "(3k reviews)"         -> review count (Yelp short form)
#   "$$"                   -> price range
#   "Open until 10:00 PM"  -> hours (skipped)
#   "Williamsburg"         -> neighborhood (used as `address`)
#
# Categories are anchors with ``href`` containing both ``find_desc=`` and
# ``find_loc=`` (Yelp's category-search links). We skip the ``/adredir``
# tracking redirector entirely so sponsored cards don't pollute output.
_PARSE_JS = r"""
(host) => {
  const cardSelectors = [
    'div[data-testid="serp-ia-card"]',
    'li[data-testid^="serp-ia-card"]',
    'div.businessName__09f24__HG_pC',
    'div.mainAttributes__09f24__26-vh',
  ];

  let cards = [];
  let usedSel = '';
  for (const sel of cardSelectors) {
    const found = document.querySelectorAll(sel);
    if (found && found.length) {
      cards = Array.from(found);
      usedSel = sel;
      break;
    }
  }

  // Last-resort fallback: gather every /biz/ anchor and treat its closest
  // ancestor `<li>` or `<div>` as the card. Useful when Yelp ships fresh
  // class hashes we haven't seen.
  if (!cards.length) {
    const anchors = document.querySelectorAll('a[href*="/biz/"]');
    const seen = new Set();
    cards = [];
    for (const a of anchors) {
      const c = a.closest('li, div');
      if (c && !seen.has(c)) {
        seen.add(c);
        cards.push(c);
      }
    }
    if (cards.length) usedSel = 'a[href*="/biz/"]';
  }

  const txt = (el) => el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : '';

  const cleanBizUrl = (raw) => {
    if (!raw) return ['', ''];
    let u;
    try {
      u = new URL(raw, host);
    } catch (e) {
      return [raw, ''];
    }
    const m = u.pathname.match(/^\/biz\/([^\/]+)/);
    if (!m) return [raw, ''];
    const slug = m[1];
    return ['https://www.yelp.com/biz/' + slug, slug];
  };

  // Extract the numeric rating from an aria-label like "4.5 star rating".
  const parseRating = (label) => {
    if (!label) return '';
    const m = label.match(/([0-9](?:\.[0-9])?)\s*star/i);
    return m ? m[1] : '';
  };

  // "(1,234 reviews)" / "(3k reviews)" / "(947)"  ->  "1,234" / "3k" / "947"
  const REVIEW_RE = /^\(\s*([0-9][0-9,\.]*[kKmM]?)\s*(?:reviews?)?\s*\)$/;
  const PRICE_RE = /^\${1,4}$/;
  const NUMERIC_RATING_RE = /^[0-5](?:\.[0-9])?$/;
  const HOURS_RE = /^(open|closed|opens|open\s+now|closed\s+now|24\s*hours)\b/i;
  // "until 10:00 PM" (just the leftover from a flex-wrapped hours block).
  const HOURS_TAIL_RE = /^until\s+\d/i;

  const out = [];
  const seenSlugs = new Set();

  for (const c of cards) {
    // ------- 1) Title + URL ----------------------------------------
    // Sponsored cards put the title anchor on /adredir; we skip those.
    // Organic cards expose at least one /biz/<slug> anchor.
    let titleAnchor =
         c.querySelector('h3 a[href^="/biz/"]')
      || c.querySelector('h2 a[href^="/biz/"]')
      || c.querySelector('a[href^="/biz/"][role="link"]');

    if (!titleAnchor) {
      const candidates = c.querySelectorAll('a[href^="/biz/"]');
      for (const a of candidates) {
        if ((a.textContent || '').trim().length > 0) {
          titleAnchor = a;
          break;
        }
      }
    }
    if (!titleAnchor) continue;

    const rawHref = titleAnchor.getAttribute('href') || '';
    const [url, slug] = cleanBizUrl(rawHref);
    if (!slug || seenSlugs.has(slug)) continue;

    let title = (titleAnchor.textContent || '').replace(/\s+/g, ' ').trim();
    title = title.replace(/^\s*\d+\.\s*/, '');  // strip "1. " ranking prefix
    if (!title) continue;

    // ------- 2) Rating ---------------------------------------------
    let rating = '';
    const ratingEl =
         c.querySelector('[role="img"][aria-label$="star rating"]')
      || c.querySelector('[role="img"][aria-label*="star rating"]')
      || c.querySelector('[aria-label$="star rating"]')
      || c.querySelector('div[aria-label*="star rating"]');
    if (ratingEl) {
      rating = parseRating(ratingEl.getAttribute('aria-label') || '');
    }

    // ------- 3) Walk every <span> in the card and classify by content
    //           Yelp ships rating/review-count/neighborhood/price/hours
    //           as a flat row of <span>s right after the rating widget.
    let reviewCount = '';
    let priceRange = '';
    let address = '';   // SERP shows neighborhood, not street address.
    const spans = c.querySelectorAll('span');
    // We want the first plausible neighborhood span — Yelp puts the
    // neighborhood between the review-count and the price; once we've
    // seen the review-count we start considering "loose" spans.
    let sawReviewCount = false;
    for (const sp of spans) {
      const t = txt(sp);
      if (!t) continue;
      // Skip nested compound spans (e.g. parent of "Open" + "until 4 PM"
      // whose text concatenates both children); we'll see the leaf spans
      // separately.
      if (sp.querySelector('span')) continue;

      if (!reviewCount) {
        const m = t.match(REVIEW_RE);
        if (m) {
          reviewCount = m[1];
          sawReviewCount = true;
          continue;
        }
      }
      if (!priceRange && PRICE_RE.test(t)) {
        priceRange = t;
        continue;
      }
      // Numeric rating duplicate ("4.5") — skip; we already have the
      // value from aria-label.
      if (NUMERIC_RATING_RE.test(t)) continue;
      // Hours strings ("Open until 10:00 PM", "Closed until 11 AM",
      // "until 4:00 AM") — skip.
      if (HOURS_RE.test(t) || HOURS_TAIL_RE.test(t)) continue;

      // Once we've passed the review-count, the next non-price /
      // non-hours span is the neighborhood. We accept short text only
      // (neighborhoods are typically <60 chars; anything longer is
      // likely a review snippet that bled into a span).
      if (sawReviewCount && !address && t.length > 0 && t.length < 80) {
        address = t;
      }
    }

    // ------- 4) Categories -----------------------------------------
    // Yelp renders categories as a row of /search?find_desc=X&find_loc=Y
    // anchors right under the metadata row. We require both query params
    // so we don't pick up the page-header search links (which don't
    // include find_loc) or unrelated /search links.
    const catNames = [];
    const seenCats = new Set();
    const catAnchors = c.querySelectorAll(
      'a[href*="/search?"][href*="find_desc="][href*="find_loc="]'
    );
    for (const a of catAnchors) {
      const t = txt(a);
      if (!t || PRICE_RE.test(t)) continue;
      // Defensive: drop anchors that wrap an entire snippet block (their
      // text would be very long).
      if (t.length > 60) continue;
      if (seenCats.has(t)) continue;
      seenCats.add(t);
      catNames.push(t);
    }
    const category = catNames.join(', ');

    seenSlugs.add(slug);
    out.push({
      title,
      url,
      slug,
      rating,
      review_count: reviewCount,
      category,
      address,
      price_range: priceRange,
    });
  }

  return { usedSelector: usedSel, count: cards.length, items: out };
}
"""


class YelpEngine(BaseEngine):
    """Search Yelp via the public ``/search`` page."""

    name = "yelp"
    # The primary path is robust enough that we keep BaseEngine's outer
    # retry loop short; the test wrapper handles per-attempt diagnostics.
    max_retries = 2

    def __init__(self, page):
        super().__init__(page)
        # Diagnostics for callers / tests.
        self.last_status: dict = {}
        # Track which strategy produced results so tests can report it.
        self.last_strategy: str = ""
        # Default location used when callers don't pass one. Yelp insists
        # on a location to render full SERP cards; if missing, the page
        # may bounce to a "set your location" prompt.
        self.default_location: str = "San Francisco, CA"

    # ------------------------------------------------------------------ search
    def search(  # type: ignore[override]
        self,
        query: str,
        limit: int = 10,
        location: str | None = None,
    ) -> list[SearchResult]:
        """Override BaseEngine.search to accept a `location` kwarg."""
        # Stash for _do_search (BaseEngine.search calls _do_search without
        # the kwarg, so we tunnel it through an instance attribute).
        self._location = location or self.default_location
        return super().search(query, limit=limit)

    # ------------------------------------------------------------------ core
    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        location = getattr(self, "_location", None) or self.default_location

        params = {
            "find_desc": query,
            "find_loc": location,
        }
        url = f"{_SEARCH_URL}?{urllib.parse.urlencode(params)}"
        log.info("[yelp] navigating to %s", url)

        if not safe_goto(self.page, url, timeout=_NAV_TIMEOUT_MS):
            log.warning("[yelp] nav failed")
            return []

        human_delay(2.0, 3.5)
        self._handle_consent()
        self._dismiss_modal()
        self._human_hints()

        try:
            self.page.wait_for_selector(
                ", ".join(_RESULT_SELECTORS), timeout=_RESULT_WAIT_MS
            )
        except Exception as e:
            log.info("[yelp] result-selector wait failed: %s", e)

        if self._is_blocked():
            log.warning(
                "[yelp] blocked: %s", self.last_status.get("block_reason")
            )
            return []

        results = self._extract_results(limit)
        if results:
            self.last_strategy = "primary"
        return results

    # -------------------------------------------------------------- diagnostics
    def selector_counts(self) -> dict[str, int]:
        """Return how many elements each candidate selector matches."""
        counts: dict[str, int] = {}
        for sel in _RESULT_SELECTORS:
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        # Always include the last-resort /biz/ anchor count.
        try:
            counts['a[href*="/biz/"]'] = len(
                self.page.query_selector_all('a[href*="/biz/"]')
            )
        except Exception:
            counts['a[href*="/biz/"]'] = -1
        return counts

    # ----------------------------------------------------------------- helpers
    def _handle_consent(self) -> None:
        """Click any GDPR / cookie consent button if present."""
        for sel in _CONSENT_BUTTON_SELECTORS:
            try:
                btn = self.page.query_selector(sel)
                if btn:
                    btn.click(timeout=2500)
                    log.info("[yelp] clicked consent (%s)", sel)
                    human_delay(0.8, 1.5)
                    return
            except Exception:
                continue

    def _dismiss_modal(self) -> None:
        """Dismiss "set your location" / signup popovers."""
        for sel in _MODAL_DISMISS_SELECTORS:
            try:
                btn = self.page.query_selector(sel)
                if btn:
                    btn.click(timeout=2500)
                    log.info("[yelp] dismissed modal (%s)", sel)
                    human_delay(0.5, 1.2)
                    return
            except Exception:
                continue
        # Best-effort: send Esc in case a focus-trapped modal is up.
        try:
            self.page.keyboard.press("Escape")
        except Exception:
            pass

    def _human_hints(self) -> None:
        """Light human-like activity: mouse move + small scroll."""
        try:
            self.page.mouse.move(
                random.randint(120, 480),
                random.randint(120, 480),
                steps=10,
            )
        except Exception:
            pass
        try:
            self.page.evaluate(
                "() => window.scrollBy(0, Math.floor(Math.random()*500) + 200)"
            )
        except Exception:
            pass
        time.sleep(random.uniform(0.4, 1.0))

    def _is_blocked(self) -> bool:
        """Detect Cloudflare / PerimeterX challenge or similar gate."""
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

        for frag in _BLOCK_URL_FRAGMENTS:
            if frag in url:
                self.last_status["block_reason"] = f"url:{frag}"
                return True

        for needle in _BLOCK_TITLE_PHRASES:
            if needle in title:
                self.last_status["block_reason"] = f"title:{needle}"
                return True

        for phrase in _BLOCK_BODY_PHRASES:
            if phrase in body:
                self.last_status["block_reason"] = f"phrase:{phrase}"
                return True

        return False

    # ---------------------------------------------------------------- extraction
    def _extract_results(self, limit: int) -> list[SearchResult]:
        try:
            payload = self.page.evaluate(_PARSE_JS, "https://www.yelp.com")
        except Exception as e:
            log.error("[yelp] page.evaluate failed: %s", e)
            return []

        if not payload:
            return []

        used = payload.get("usedSelector", "")
        raw_count = payload.get("count", 0)
        items = payload.get("items", []) or []
        log.info(
            "[yelp] selector=%s raw_cards=%d parsed=%d",
            used or "<none>", raw_count, len(items),
        )

        results: list[SearchResult] = []
        seen_slugs: set[str] = set()
        for item in items:
            slug = (item.get("slug") or "").strip()
            title = (item.get("title") or "").strip()
            url = (item.get("url") or "").strip()
            if not title or not url or not slug:
                continue
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)

            rating = (item.get("rating") or "").strip()
            review_count = (item.get("review_count") or "").strip()
            category = (item.get("category") or "").strip()
            address = (item.get("address") or "").strip()
            price_range = (item.get("price_range") or "").strip()

            # Compose a human-readable snippet:
            #   "4.5★ (1,234) · $$ · Pizza, Italian · 7 Carmine St, ..."
            parts: list[str] = []
            if rating:
                if review_count:
                    parts.append(f"{rating}\u2605 ({review_count})")
                else:
                    parts.append(f"{rating}\u2605")
            elif review_count:
                parts.append(f"({review_count} reviews)")
            if price_range:
                parts.append(price_range)
            if category:
                parts.append(category)
            if address:
                parts.append(address)
            snippet = " \u00b7 ".join(parts)

            sr = SearchResult(title=title, url=url, snippet=snippet)
            # Extension fields — accessible as r.rating / r.review_count / ...
            # by callers that know about them.
            sr.rating = rating
            sr.review_count = review_count
            sr.category = category
            sr.address = address
            sr.price_range = price_range
            sr.slug = slug
            results.append(sr)

            if len(results) >= max(1, int(limit)):
                break

        log.info("[yelp] returned %d results", len(results))
        return results
