"""Steam Store search adapter.

Steam has no public, free-tier search API for the storefront — the IStoreService
endpoints are gated behind partner credentials. So we scrape the public
HTML search page:

    https://store.steampowered.com/search/?term=<q>

Each match is a ``<a class="search_result_row">`` link; from it we pull:
    - ``title``  : ``.title`` text (game / DLC / bundle name)
    - ``url``    : the row's ``href`` (already an absolute store URL)
    - ``price``  : the price block (``.search_price`` / ``.discount_final_price``)
                   — handles "Free To Play", regular, and discounted prices
    - ``rating`` : Steam review summary (e.g. "Very Positive (1,000,000)")
                   parsed from the ``data-tooltip-html`` of ``.search_review_summary``
    - ``release``: release date string from ``.search_released``

Age gate handling:
    Mature titles (Cyberpunk 2077, GTA V, ...) trigger Steam's age check
    on the *product* page. The /search/ listing itself is not gated, but
    we still pre-set the standard "born in 2000" cookies on the context
    before navigation so that:
        a) any redirect through the agecheck path lets us through,
        b) the search results consistently include mature titles.

    Cookies set:
        - birthtime=946702800        (Jan 1, 2000 00:00:00 UTC-ish)
        - lastagecheckage=1-January-2000
        - mature_content=1
        - wants_mature_content=1
        - Steam_Language=english     (deterministic English page strings)

    If we still land on the agecheck page (e.g. /agecheck/app/...), we
    fill the year and click "View Page" as a fallback.

The :class:`SearchResult` dataclass only has title/url/snippet/score; we
attach ``price`` and ``rating`` as plain attributes on the instance and
also embed them into ``snippet`` for readability.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlencode

from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)


# Cookies that bypass Steam's age gate. ``birthtime`` is a Unix timestamp;
# 946702800 is roughly 1 Jan 2000 — old enough for any rating threshold.
_AGE_GATE_COOKIES = [
    {"name": "birthtime",            "value": "946702800",        "domain": ".steampowered.com",       "path": "/"},
    {"name": "lastagecheckage",      "value": "1-January-2000",   "domain": ".steampowered.com",       "path": "/"},
    {"name": "mature_content",       "value": "1",                "domain": ".steampowered.com",       "path": "/"},
    {"name": "wants_mature_content", "value": "1",                "domain": ".steampowered.com",       "path": "/"},
    {"name": "Steam_Language",       "value": "english",          "domain": "store.steampowered.com",  "path": "/"},
    # Mirror on the bare apex too — Steam sometimes reads from either.
    {"name": "Steam_Language",       "value": "english",          "domain": ".steampowered.com",       "path": "/"},
]


# JS that runs *in the page* and pulls structured data out of every
# .search_result_row. Returning a JSON-friendly list keeps the Python
# side trivial and avoids round-tripping per-element.
_PARSE_JS = r"""
() => {
  const rows = document.querySelectorAll('a.search_result_row');
  const out = [];
  for (const row of rows) {
    const titleEl = row.querySelector('.title');
    const title = titleEl ? titleEl.textContent.trim() : '';
    if (!title) continue;

    const url = row.getAttribute('href') || '';

    // Price — three shapes in the DOM:
    //   1) .discount_final_price        (sale)
    //   2) .search_price                (regular / Free To Play)
    //   3) .search_price strike + text  (no-discount old layout)
    let price = '';
    const discountFinal = row.querySelector('.discount_final_price');
    if (discountFinal && discountFinal.textContent.trim()) {
      price = discountFinal.textContent.trim();
    } else {
      const priceEl = row.querySelector('.search_price');
      if (priceEl) {
        // Strip any <strike>...</strike> (old MSRP) and keep the remainder.
        const clone = priceEl.cloneNode(true);
        clone.querySelectorAll('strike, .search_discount').forEach(n => n.remove());
        price = (clone.textContent || '').replace(/\s+/g, ' ').trim();
      }
    }

    // Rating tooltip — e.g. "Very Positive<br>89% of the 1,234 user reviews
    // for this game are positive.". We pass the raw HTML up to Python for
    // robust parsing.
    let ratingTooltip = '';
    const reviewEl = row.querySelector('.search_review_summary');
    if (reviewEl) {
      ratingTooltip = reviewEl.getAttribute('data-tooltip-html') || '';
    }

    const releaseEl = row.querySelector('.search_released');
    const release = releaseEl ? releaseEl.textContent.trim() : '';

    out.push({
      title,
      url,
      price,
      ratingTooltip,
      release,
    });
  }
  return out;
}
"""


class SteamEngine(BaseEngine):
    """Search the Steam Store via the public HTML search page."""

    name = "steam"
    SEARCH_URL = "https://store.steampowered.com/search/"

    NAV_TIMEOUT = 30000

    # ----------------------------------------------------------- helpers
    def _set_age_gate_cookies(self) -> None:
        """Pre-set the cookies that bypass Steam's age verification."""
        try:
            ctx = self.page.context
        except Exception as e:
            log.warning("[steam] cannot access page.context to set cookies: %s", e)
            return
        try:
            ctx.add_cookies(_AGE_GATE_COOKIES)
        except Exception as e:
            log.warning("[steam] add_cookies failed: %s", e)

    def _maybe_pass_age_gate(self) -> None:
        """If we got bounced to the agecheck page, fill year and submit.

        The /search/ listing itself isn't gated, so this is a defensive
        fallback for when Steam decides to interpose anyway.
        """
        try:
            url = (self.page.url or "").lower()
        except Exception:
            url = ""
        if "/agecheck" not in url:
            return
        log.info("[steam] age gate detected, attempting to bypass")
        try:
            # New-style agecheck: <select id="ageYear">
            self.page.evaluate(
                "() => {"
                "  const y = document.querySelector('#ageYear');"
                "  if (y) y.value = '2000';"
                "  const btns = document.querySelectorAll("
                "    'a.btn_blue_steamui, a#view_product_page_btn, a.btnv6_blue_hoverfade'"
                "  );"
                "  for (const b of btns) {"
                "    if ((b.textContent || '').match(/view\\s*page|continue|enter/i)) {"
                "      b.click();"
                "      return;"
                "    }"
                "  }"
                "  if (btns.length) btns[0].click();"
                "}"
            )
            self.page.wait_for_load_state("domcontentloaded", timeout=self.NAV_TIMEOUT)
        except Exception as e:
            log.warning("[steam] age gate bypass failed: %s", e)

    @staticmethod
    def _parse_rating(tooltip_html: str) -> str:
        """Pull a short rating string out of Steam's review tooltip HTML.

        Tooltip looks like::

            Very Positive<br>89% of the 1,234 user reviews ...

        We collapse it into ``"Very Positive (1,234)"``. If it's the
        "Need more reviews" form we just return that summary verbatim.
        Returns ``""`` when no rating is available.
        """
        if not tooltip_html:
            return ""
        # Split on <br> (any case / variant)
        parts = re.split(r"<br\s*/?>", tooltip_html, flags=re.IGNORECASE)
        summary = re.sub(r"<[^>]+>", "", parts[0]).strip() if parts else ""
        detail = re.sub(r"<[^>]+>", "", parts[1]).strip() if len(parts) > 1 else ""

        # Try to extract the user-review count, e.g. "1,234 user reviews".
        count = ""
        m = re.search(r"the\s+([\d,]+)\s+user reviews", detail, flags=re.IGNORECASE)
        if m:
            count = m.group(1)

        if summary and count:
            return f"{summary} ({count})"
        return summary or detail

    # --------------------------------------------------------------- main
    def _do_search(self, query: str, limit: int = 10) -> list[SearchResult]:
        params = {
            "term": query,
            "category1": "998",  # "Games" — keeps results focused on games
        }
        # Steam ignores unknown params, so we can keep this simple. We do
        # *not* pass a limit param: the page returns ~25 results by default
        # and we slice client-side.
        url = f"{self.SEARCH_URL}?{urlencode(params)}"

        self._set_age_gate_cookies()

        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=self.NAV_TIMEOUT)
        except Exception as e:
            log.error("[steam] navigation to %s failed: %s", url, e)
            return []

        self._maybe_pass_age_gate()

        # Wait for at least one row, but don't fail the whole search if the
        # selector never appears — fall through and let the parser return [].
        try:
            self.page.wait_for_selector(
                "a.search_result_row", timeout=self.NAV_TIMEOUT
            )
        except Exception as e:
            log.warning("[steam] no search_result_row appeared: %s", e)

        try:
            raw = self.page.evaluate(_PARSE_JS) or []
        except Exception as e:
            log.error("[steam] page.evaluate failed: %s", e)
            return []

        results: list[SearchResult] = []
        for item in raw[: max(1, int(limit))]:
            title = (item.get("title") or "").strip()
            row_url = (item.get("url") or "").strip()
            if not title or not row_url:
                continue

            price = (item.get("price") or "").strip()
            rating = self._parse_rating(item.get("ratingTooltip") or "")
            release = (item.get("release") or "").strip()

            # Build a human-readable snippet: "Price · Rating · Released ..."
            snippet_parts = []
            if price:
                snippet_parts.append(price)
            if rating:
                snippet_parts.append(rating)
            if release:
                snippet_parts.append(f"Released {release}")
            snippet = " · ".join(snippet_parts)

            sr = SearchResult(title=title, url=row_url, snippet=snippet)
            # Extension fields — accessible as r.price / r.rating / r.release
            # by callers that know about them.
            sr.price = price
            sr.rating = rating
            sr.release = release
            results.append(sr)

        log.info("[steam] Found %d results for %r", len(results), query)
        return results
