"""Amazon Search adapter.

Amazon runs an aggressive anti-scrape stack (CAPTCHA, IP-rate gating,
behavioural checks), so this adapter is conservative:

1. Tries the primary host (``amazon.com``) first.
2. If it gets a CAPTCHA / "Robot Check" / empty result page, falls back to
   regional hosts that gate scrapers less aggressively, in order:
       amazon.co.uk → amazon.com.au
3. Pre-clears the cookie / consent banner before parsing.
4. Detects CAPTCHA pages explicitly and treats them as "blocked".

Search URL: ``https://<host>/s?k=<query>``

Each result row is a ``div[data-component-type="s-search-result"]`` (or
``div.s-result-item[data-asin]``). We do all parsing in a single
``page.evaluate`` JS pass so per-element round-trips don't dominate, then
we canonicalize the URL to ``https://<host>/dp/<ASIN>`` so callers always
get a stable, shareable link (the in-card anchor carries
``ref=…&qid=…&sr=…`` tracking parameters that expire quickly).

Returned ``SearchResult`` extension fields
------------------------------------------
* ``r.price``         — string, e.g. ``"$129.99"`` (full ``.a-offscreen`` text)
* ``r.rating``        — string, e.g. ``"4.5 out of 5 stars"``
* ``r.reviews_count`` — string, e.g. ``"1,234"``
* ``r.image_url``     — string, product thumbnail (``img.s-image``)
* ``r.asin``          — string, Amazon Standard Identification Number
* ``r.host``          — string, host that returned this result (useful when we
                         fall back to a regional store)
"""

from __future__ import annotations

import logging
import random
import time
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)


# Hosts to try in order. amazon.co.uk and amazon.com.au tend to gate
# headless scrapers less aggressively than amazon.com, so we keep them
# as fallbacks that are tried only when the primary host is blocked.
_HOSTS: tuple[str, ...] = (
    "https://www.amazon.com",
    "https://www.amazon.co.uk",
    "https://www.amazon.com.au",
)

_SEARCH_PATH = "/s"

# How long to wait for the first product card to render (ms).
_RESULT_WAIT_MS = 20_000
# Per-attempt navigation timeout (ms).
_NAV_TIMEOUT_MS = 35_000

# Selectors used to count / wait for result cards. Tried in order; whichever
# matches first wins. Amazon shuffles markup across A/B variants, so we list
# multiple shapes (the JS parser tries them in the same order).
_RESULT_SELECTORS: tuple[str, ...] = (
    'div[data-component-type="s-search-result"]',
    'div.s-result-item[data-asin]:not([data-asin=""])',
    'div.s-search-result',
)

# Cookie / consent banner buttons. Amazon uses ``sp-cc-accept`` for its EU
# cookie banner; localized variants tend to share the same id. We keep a
# couple of fallbacks for older markup.
_CONSENT_BUTTON_SELECTORS: tuple[str, ...] = (
    "#sp-cc-accept",
    "input#sp-cc-accept",
    "button#sp-cc-accept",
    "input[name='accept']",
    "button[name='accept']",
    "input[data-cel-widget='sp-cc-accept']",
)

# Phrases / URL fragments that mean Amazon forced us through their CAPTCHA
# / robot-check gate.
_BLOCK_URL_FRAGMENTS: tuple[str, ...] = (
    "/errors/validatecaptcha",
    "/ap/cvf",
)
_BLOCK_TITLE_PHRASES: tuple[str, ...] = (
    "robot check",
    "sorry, we just need to make sure",
)
_BLOCK_BODY_PHRASES: tuple[str, ...] = (
    "type the characters you see",
    "enter the characters you see below",
    "to discuss automated access to amazon",
    "sorry! something went wrong",
    "we just need to make sure you're not a robot",
    "hello! please confirm you are a human",
)


# JS that walks every result card and pulls a structured row. We pass the
# host string in so the parser can absolutise the relative ``href`` and
# rewrite the path to ``/dp/<ASIN>`` (stripping ref/sr/qid tracking).
_PARSE_JS = r"""
(host) => {
  const cardSelectors = [
    'div[data-component-type="s-search-result"]',
    'div.s-result-item[data-asin]',
    'div.s-search-result',
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

  const txt = (el) => el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : '';

  const out = [];
  for (const c of cards) {
    const asin = c.getAttribute('data-asin') || '';
    // Sponsored placeholder rows have an empty data-asin -> skip.
    if (!asin) continue;

    // 1) Title — h2 holds the product name; modern markup nests a span.
    let title =
         txt(c.querySelector('h2 .a-text-normal'))
      || txt(c.querySelector('h2 a span'))
      || txt(c.querySelector('h2 span'))
      || txt(c.querySelector('h2'));
    if (!title) {
      const aria = c.querySelector('h2 a[aria-label]');
      if (aria) title = (aria.getAttribute('aria-label') || '').trim();
    }

    // 2) URL — prefer an /dp/ASIN anchor.
    let href = '';
    const titleAnchor =
         c.querySelector('h2 a.a-link-normal[href]')
      || c.querySelector('h2 a[href]')
      || c.querySelector('a.a-link-normal[href*="/dp/"]')
      || c.querySelector('a[href*="/dp/"]');
    if (titleAnchor) href = titleAnchor.getAttribute('href') || '';

    let url = '';
    if (href) {
      try {
        url = new URL(href, host).toString();
      } catch (e) {
        url = host + href;
      }
    }
    // Canonicalize: drop tracking, force /dp/ASIN. Amazon search
    // anchors look like ``/F75-Pro/dp/B0D14N2QZF/ref=sr_1_1?dib=…&qid=…&sr=…``
    // — we always rewrite the path to the bare ``/dp/<ASIN>`` (and clear
    // search / hash) so callers get a stable, shareable canonical URL.
    if (asin && url) {
      try {
        const u = new URL(url);
        u.search = '';
        u.hash = '';
        u.pathname = '/dp/' + asin;
        url = u.toString();
      } catch (e) {
        // Leave url as-is on parse failure.
      }
    }

    // 3) Price — .a-offscreen carries the full localized price string,
    //    e.g. "$129.99" / "£99.99" / "AU$149.00". Some Free / coupon-only
    //    rows expose only .a-price-whole; keep both as fallbacks.
    let price =
         txt(c.querySelector('.a-price .a-offscreen'))
      || txt(c.querySelector('span.a-price-whole'))
      || txt(c.querySelector('.a-color-price'));

    // 4) Rating — modern Amazon search no longer puts the alt text inside
    //    the star icon; it now lives on the popover trigger's aria-label.
    //    The popover label looks like "4.6 out of 5 stars, rating details"
    //    — strip the trailing ", rating details" noise.
    let rating = '';
    const ratingPopover = c.querySelector('[aria-label*="out of 5 stars"]');
    if (ratingPopover) {
      rating = (ratingPopover.getAttribute('aria-label') || '').trim();
    }
    if (!rating) {
      rating =
           txt(c.querySelector('.a-icon-star-small .a-icon-alt'))
        || txt(c.querySelector('.a-icon-star .a-icon-alt'))
        || txt(c.querySelector('i.a-icon-star-small > span.a-icon-alt'));
    }
    rating = rating.replace(/,\s*rating details\s*$/i, '').trim();

    // 5) Reviews count — current markup exposes it on
    //    ``<a aria-label="1,487 ratings" class="a-link-normal s-underline-text…">``
    //    sibling of the rating popover. Older variants used
    //    ``a[href*="#customerReviews"] .a-size-base`` and a bare
    //    ``span.a-size-base.s-underline-text``; we keep both as fallbacks.
    let reviewsCount = '';
    const ratingsAnchor = c.querySelector('a[aria-label$="ratings"], a[aria-label$="rating"], a[aria-label*=" ratings"]');
    if (ratingsAnchor) {
      const lbl = (ratingsAnchor.getAttribute('aria-label') || '').trim();
      // "1,487 ratings" -> "1,487"
      const m = lbl.match(/^([\d,\.]+)/);
      if (m) reviewsCount = m[1];
    }
    if (!reviewsCount) {
      const popoverFooter = c.querySelector('a[href*="#customerReviews"] .a-size-base');
      if (popoverFooter) reviewsCount = txt(popoverFooter);
    }
    if (!reviewsCount) {
      const small = c.querySelector('span.a-size-base.s-underline-text');
      if (small) reviewsCount = txt(small);
    }
    if (!reviewsCount) {
      // Last-resort: any small a-size-base span near the rating that
      // looks like a comma-formatted number (e.g. "1,234").
      const candidates = c.querySelectorAll('a span.a-size-base');
      for (const cand of candidates) {
        const t = txt(cand);
        if (/^[\d,\.]+$/.test(t)) { reviewsCount = t; break; }
      }
    }

    // 6) Image — every product card has an img.s-image thumbnail.
    const img = c.querySelector('img.s-image');
    const image_url = img ? (img.getAttribute('src') || '') : '';

    if (!title || !url) continue;

    out.push({
      title,
      url,
      price,
      rating,
      reviews_count: reviewsCount,
      image_url,
      asin,
    });
  }

  return { usedSelector: usedSel, count: cards.length, items: out };
}
"""


class AmazonEngine(BaseEngine):
    """Search Amazon via the public ``/s?k=...`` page."""

    name = "amazon"
    # Primary already cycles through 3 hosts internally, so we keep BaseEngine's
    # outer retry loop short.
    max_retries = 2

    def __init__(self, page):
        super().__init__(page)
        # Diagnostics for callers / tests.
        self.last_status: dict = {}
        # Track which host produced results so tests can report it.
        self.last_strategy: str = ""

    # ------------------------------------------------------------------ search
    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        for host in _HOSTS:
            results = self._search_host(host, query, limit)
            if results:
                self.last_strategy = host
                return results
            log.warning(
                "[amazon] %s yielded 0 (or blocked); trying next host", host
            )
            human_delay(2.0, 4.0)
        return []

    # -------------------------------------------------------------- per-host
    def _search_host(self, host: str, query: str, limit: int) -> list[SearchResult]:
        q = urllib.parse.quote(query)
        url = f"{host}{_SEARCH_PATH}?k={q}"
        log.info("[amazon] navigating to %s", url)

        if not safe_goto(self.page, url, timeout=_NAV_TIMEOUT_MS):
            log.warning("[amazon] %s nav failed", host)
            return []

        human_delay(1.5, 3.0)
        self._handle_consent()
        self._human_hints()

        try:
            self.page.wait_for_selector(
                ", ".join(_RESULT_SELECTORS), timeout=_RESULT_WAIT_MS
            )
        except Exception as e:
            log.info("[amazon] result selector wait failed (%s): %s", host, e)

        if self._is_blocked():
            log.warning(
                "[amazon] %s blocked: %s",
                host, self.last_status.get("block_reason"),
            )
            return []

        return self._extract_results(host, limit)

    # ---------------------------------------------------------- diagnostics
    def selector_counts(self) -> dict[str, int]:
        """Return how many elements each candidate selector matches."""
        counts: dict[str, int] = {}
        for sel in _RESULT_SELECTORS:
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        return counts

    # ---------------------------------------------------------------- helpers
    def _handle_consent(self) -> None:
        """Click any cookie / consent button if present."""
        for sel in _CONSENT_BUTTON_SELECTORS:
            try:
                btn = self.page.query_selector(sel)
                if btn:
                    btn.click(timeout=2500)
                    log.info("[amazon] clicked consent (%s)", sel)
                    human_delay(0.5, 1.2)
                    return
            except Exception:
                continue

    def _human_hints(self) -> None:
        """Light human-like activity: mouse move + small scroll."""
        try:
            self.page.mouse.move(
                random.randint(120, 480),
                random.randint(120, 480),
                steps=8,
            )
        except Exception:
            pass
        try:
            self.page.evaluate(
                "() => window.scrollBy(0, Math.floor(Math.random()*400) + 200)"
            )
        except Exception:
            pass
        time.sleep(random.uniform(0.4, 0.9))

    def _is_blocked(self) -> bool:
        """Detect Amazon's CAPTCHA / robot-check page."""
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
    def _extract_results(self, host: str, limit: int) -> list[SearchResult]:
        try:
            payload = self.page.evaluate(_PARSE_JS, host)
        except Exception as e:
            log.error("[amazon] page.evaluate failed: %s", e)
            return []

        if not payload:
            return []

        used = payload.get("usedSelector", "")
        raw_count = payload.get("count", 0)
        items = payload.get("items", []) or []
        log.info(
            "[amazon] selector=%s raw_cards=%d parsed=%d host=%s",
            used or "<none>", raw_count, len(items), host,
        )

        results: list[SearchResult] = []
        seen_asins: set[str] = set()
        for item in items:
            asin = (item.get("asin") or "").strip()
            title = (item.get("title") or "").strip()
            url = (item.get("url") or "").strip()
            if not title or not url or not asin:
                continue
            if asin in seen_asins:
                continue
            seen_asins.add(asin)

            price = (item.get("price") or "").strip()
            rating = (item.get("rating") or "").strip()
            reviews_count = (item.get("reviews_count") or "").strip()
            image_url = (item.get("image_url") or "").strip()

            # Build a human-readable snippet: "$129.99 · 4.5 out of 5 stars · (1,234 reviews)"
            parts: list[str] = []
            if price:
                parts.append(price)
            if rating:
                parts.append(rating)
            if reviews_count:
                parts.append(f"({reviews_count} reviews)")
            snippet = " · ".join(parts)

            sr = SearchResult(title=title, url=url, snippet=snippet)
            # Extension fields — accessible as r.price / r.rating / ...
            # by callers that know about them.
            sr.price = price
            sr.rating = rating
            sr.reviews_count = reviews_count
            sr.image_url = image_url
            sr.asin = asin
            sr.host = host
            results.append(sr)

            if len(results) >= max(1, int(limit)):
                break

        log.info("[amazon] returned %d results from %s", len(results), host)
        return results
