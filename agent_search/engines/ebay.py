"""eBay search adapter via the public ``/sch/i.html`` SERP.

Strategy
--------
Visit the eBay homepage first (so cookies are set, otherwise eBay
returns ``Access Denied`` to a cold ``/sch/`` request), then navigate
to::

    https://www.ebay.com/sch/i.html?_nkw=<query>

The result list uses ``<li id="item...">`` cards. The class names are
hashed CSS-in-JS so we can't rely on ``s-item__title`` / ``s-item__price``
anymore — instead we use:

* the first ``span.su-styled-text.primary.default`` for the title
* any ``span.s-card__price`` for the price (joined when ranged)
* a regex sweep of the card text for shipping / condition / location /
  seller / feedback%

Each :class:`SearchResult` carries:

* ``item_id``   — numeric eBay listing id from ``/itm/<id>``
* ``price``     — first price (or ``"<low> to <high>"`` range)
* ``condition`` — ``"Brand New"`` / ``"Pre-Owned"`` / ``"Open Box"`` / …
* ``shipping`` — shipping cost text (e.g. ``"+HKD 36.18 delivery"``)
* ``location`` — listing location (e.g. ``"China"``)
* ``seller``   — seller handle
* ``feedback`` — seller feedback summary (e.g. ``"99.3% positive (9.3K)"``)
* ``image_url`` — listing thumbnail
"""

from __future__ import annotations

import logging
import re
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

EBAY_HOME = "https://www.ebay.com/"
EBAY_SEARCH = "https://www.ebay.com/sch/i.html"

ITEM_ID_RE = re.compile(r"/itm/(?:[^/?#]+/)?(\d{8,})")
LOCATION_RE = re.compile(r"Located in\s+([^.+]+?)(?=Free returns|$|\s{2,})", re.I)
SHIPPING_RE = re.compile(r"(\+?\s*[A-Z]{1,3}\$?[\d,]+\.\d{2}\s*delivery|\+?\s*Free\s+(?:shipping|delivery)|"
                         r"\+?\s*[A-Z]{1,3}\s*[\d,]+\.\d{2}\s*delivery)", re.I)
FEEDBACK_RE = re.compile(r"(\d{2,3}(?:\.\d)?%\s*positive\s*\([\d,.KMB]+\))", re.I)
CONDITION_RE = re.compile(r"\b(Brand\s+New|Pre-Owned|New\s+\(Other\)|Open\s+Box|Refurbished|Used)\b", re.I)


_PARSE_JS = r"""
(limit) => {
  const lis = Array.from(document.querySelectorAll('li[id^="item"]'));
  const out = [];
  const seen = new Set();
  for (const li of lis) {
    if (out.length >= limit) break;
    const a = li.querySelector('a[href*="/itm/"]');
    if (!a) continue;
    const url = a.href.split('?')[0];
    const m = url.match(/\/itm\/(?:[^/?#]+\/)?(\d{8,})/);
    if (!m) continue;
    const item_id = m[1];
    if (seen.has(item_id)) continue;
    seen.add(item_id);

    // Title: first .su-styled-text.primary.default, fallback to a's text.
    let title = '';
    const titleEl = li.querySelector('span.su-styled-text.primary.default') ||
                    li.querySelector('h3') ||
                    li.querySelector('span[class*="title" i]');
    if (titleEl) title = titleEl.textContent.trim();
    if (!title) title = (a.textContent || '').trim();

    // Price(s).
    const priceEls = Array.from(li.querySelectorAll('span.s-card__price'));
    const priceStrs = priceEls.map(e => e.textContent.trim()).filter(Boolean);
    let price = '';
    if (priceStrs.length === 1) {
      price = priceStrs[0];
    } else if (priceStrs.length >= 2) {
      const numeric = priceStrs.filter(s => /\d/.test(s));
      if (numeric.length >= 2) price = `${numeric[0]} to ${numeric[numeric.length - 1]}`;
      else price = priceStrs.join(' ');
    }

    // Condition text.
    const condEl = li.querySelector('span.su-styled-text.secondary.default');
    const condition = condEl ? condEl.textContent.trim() : '';

    // Image URL.
    const img = li.querySelector('img');
    const image_url = img ? (img.getAttribute('src') || '') : '';

    // Raw text for regex extraction of shipping / location / seller / feedback.
    const raw = (li.textContent || '').replace(/\s+/g, ' ').trim();

    out.push({
      item_id, url, title, price, condition, image_url, raw_text: raw,
    });
  }
  return {lis_seen: lis.length, rows: out};
}
"""


class EbayEngine(BaseEngine):
    """eBay search via the public /sch/i.html SERP."""

    name = "ebay"
    max_retries = 2

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # Cold-hit /sch/ returns Access Denied. Warm up on homepage first.
        if not safe_goto(self.page, EBAY_HOME, timeout=20000, retries=1):
            log.warning("[ebay] homepage warmup failed")
        else:
            human_delay(0.5, 1.2)
            try:
                self.page.evaluate("(y) => window.scrollBy(0, y)", 400)
            except Exception:
                pass

        q = urllib.parse.quote_plus(query)
        url = f"{EBAY_SEARCH}?_nkw={q}"
        log.info("[ebay] navigating to %s", url)
        if not safe_goto(self.page, url, timeout=30000):
            return []

        try:
            self.page.wait_for_selector('li[id^="item"]', timeout=12000)
        except Exception:
            pass
        human_delay(0.6, 1.4)

        try:
            data = self.page.evaluate(_PARSE_JS, max(limit, 5)) or {}
        except Exception as e:
            log.warning("[ebay] parse JS failed: %s", e)
            data = {}

        lis_seen = int(data.get("lis_seen") or 0)
        rows = data.get("rows") or []
        self.last_status = {
            "url": getattr(self.page, "url", ""),
            "lis_seen": lis_seen,
            "count": len(rows),
        }

        results: list[SearchResult] = []
        for row in rows:
            url2 = (row.get("url") or "").strip()
            item_id = (row.get("item_id") or "").strip()
            title = (row.get("title") or "").strip()
            if not url2 or not item_id or not title:
                continue
            if title.lower() in ("opens in a new window or tab", "shop on ebay"):
                # eBay's "anchor" promo card.
                continue

            price = (row.get("price") or "").strip()
            condition = (row.get("condition") or "").strip()
            image_url = (row.get("image_url") or "").strip()
            raw = (row.get("raw_text") or "").strip()

            shipping = ""
            mship = SHIPPING_RE.search(raw)
            if mship:
                shipping = mship.group(0).strip()
            location = ""
            mloc = LOCATION_RE.search(raw)
            if mloc:
                location = mloc.group(1).strip()
            feedback = ""
            mfb = FEEDBACK_RE.search(raw)
            if mfb:
                feedback = mfb.group(1).strip()
            if not condition:
                mc = CONDITION_RE.search(raw)
                if mc:
                    condition = mc.group(1).strip()
            seller = self._extract_seller(raw, feedback)

            head = []
            if price:
                head.append(price)
            if condition:
                head.append(condition)
            if shipping:
                head.append(shipping)
            if location:
                head.append(location)
            if seller:
                head.append(f"by {seller}")
            if feedback:
                head.append(feedback)
            snippet = " · ".join(head)[:320]

            r = SearchResult(title=title[:200], url=url2, snippet=snippet)
            r.item_id = item_id           # type: ignore[attr-defined]
            r.price = price               # type: ignore[attr-defined]
            r.condition = condition       # type: ignore[attr-defined]
            r.shipping = shipping         # type: ignore[attr-defined]
            r.location = location         # type: ignore[attr-defined]
            r.seller = seller             # type: ignore[attr-defined]
            r.feedback = feedback         # type: ignore[attr-defined]
            r.image_url = image_url       # type: ignore[attr-defined]
            results.append(r)
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _extract_seller(raw: str, feedback: str) -> str:
        """Seller handle precedes the feedback %-positive text."""
        if not feedback:
            return ""
        idx = raw.find(feedback)
        if idx <= 0:
            return ""
        # Look backwards for the seller token.
        before = raw[max(0, idx - 80) : idx].strip()
        # The handle is usually the last whitespace-delimited token.
        m = re.search(r"([A-Za-z0-9._\-]{3,32})\s*$", before)
        if not m:
            return ""
        candidate = m.group(1)
        # Skip if it's a known stop word.
        if candidate.lower() in ("ebay", "free", "returns"):
            return ""
        return candidate
