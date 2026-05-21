"""Icecat product-catalog search adapter.

Icecat (https://icecat.biz) is the world's largest open product-data feed —
they syndicate ~28 M product data-sheets covering 14 000+ brands. They expose
several entry points:

* the public search page at ``https://icecat.biz/en/search?keyword=<q>``
  (free, no auth, but JS-rendered React SPA),
* the open / full XML/JSON feed at ``https://api.icecat.biz/...``
  (free for the *open* catalog but **requires registration / login**, plus
  a per-host rate cap), and
* the bilateral OpenCatalog interface for paying integrators.

We intentionally use the *web search page* here so the adapter works
out-of-the-box with no API key. The page renders client-side via React
(the initial HTML body is essentially ``<div id="ReactContainer"></div>``),
so we drive a real headless browser, wait for the product cards to mount,
and parse them in a single ``page.evaluate`` JS pass.

DOM (Icecat search SPA, May 2026)
---------------------------------

Each product is a card whose root carries a class containing the substring
``mainPart`` — the full class is hashed per build
(``src-routes-search-product-item-raw-style__mainPart--3CHo0``), so we
match by substring. Inside each card:

* ``a[class*='productImage'] img``   — product thumbnail (non-paying
  brands serve the ``/dist/bf5b29be...jpg`` placeholder),
* ``[class*='sponsor']``             — the *data sponsor* (often a
  reseller like "Vodafone", not the manufacturer brand),
* ``a[class*='descriptionTitle']``   — title link; text contains
  ``<span style='background-color: yellow'>`` highlight markup,
* ``[class*='titleContainer'] > p``  — the manufacturer SKU /
  product code (e.g. ``IPHONE6S16``),
* ``[class*='descriptionText']``     — short spec summary.

Product URL pattern::

    /en/p/<sponsor>/<sku>/<category>-[<ean>-]<brand+model>-<numericid>.html

We extract:
  * ``category`` from the first ``-``-delimited segment of the filename, and
  * ``brand`` from the first word of the page-rendered title — falling back
    to the sponsor div, then to the URL ``<brand+model>`` segment.

Returned ``SearchResult`` extension fields
------------------------------------------
* ``r.brand``       — manufacturer brand (best-effort)
* ``r.category``    — Icecat category slug (e.g. ``"smartphones"``)
* ``r.image_url``   — absolute image URL (or empty if placeholder only)
* ``r.specs``       — short spec summary from the descriptionText paragraph
* ``r.product_code``— manufacturer SKU / product code (e.g. ``"IPHONE6S16"``)
* ``r.icecat_id``   — numeric Icecat product id parsed out of the URL
"""

from __future__ import annotations

import logging
import re
import time
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)


_SEARCH_URL = "https://icecat.biz/en/search?keyword={q}"
_SITE_BASE = "https://icecat.biz"

# How long to wait for the React app to mount and render at least one card.
_RESULT_WAIT_MS = 25_000
# How often to poll while waiting for cards (ms).
_POLL_INTERVAL_MS = 250
# Per-attempt navigation timeout (ms).
_NAV_TIMEOUT_MS = 40_000

# Substring selectors that match Icecat's hashed React class names. The
# full class names look like ``...mainPart--3CHo0``; the readable token
# is stable across builds, only the trailing hash changes.
_CARD_SELECTOR = "[class*='mainPart']"
_FALLBACK_CARD_SELECTORS = (
    "[class*='product-item']",
    "[class*='containerImages']",
)


# JS that walks every card on the page and pulls a structured row. We do
# everything in JS to (a) avoid one Python<->browser round-trip per
# element, and (b) cope with the hashed-class react styling by using
# substring class matchers.
_PARSE_JS = r"""
(host) => {
  // Card root. Match by substring because the full class names are hashed
  // per react build (e.g. mainPart--3CHo0).
  let cards = Array.from(document.querySelectorAll("[class*='mainPart']"));

  // Fallback: if mainPart isn't present, climb out from each
  // descriptionTitle anchor until we find a container that also includes
  // the productImage anchor. This makes us robust to react re-bundles
  // that rename the outer wrapper class.
  if (cards.length === 0) {
    const titleAnchors = document.querySelectorAll("a[class*='descriptionTitle']");
    const seen = new Set();
    for (const a of titleAnchors) {
      let el = a;
      for (let i = 0; i < 8 && el.parentElement; i++) {
        el = el.parentElement;
        if (
          el.querySelector("a[class*='productImage']") &&
          el.querySelector("a[class*='descriptionTitle']")
        ) {
          if (!seen.has(el)) {
            seen.add(el);
            cards.push(el);
          }
          break;
        }
      }
    }
  }

  const txt = (el) => (el ? (el.textContent || '') : '').replace(/\s+/g, ' ').trim();
  const absUrl = (raw) => {
    if (!raw) return '';
    try { return new URL(raw, host).toString(); }
    catch (_) { return raw.startsWith('http') ? raw : (host + raw); }
  };

  const out = [];
  for (const c of cards) {
    // Title — the descriptionTitle anchor; strip the yellow-highlight
    // <span> markup MediaWiki-style by going via .textContent.
    const titleA = c.querySelector("a[class*='descriptionTitle']");
    let title = txt(titleA);
    let href = '';
    if (titleA) {
      href = titleA.getAttribute('href') || '';
    }

    // Image. Non-paying brands serve a generic placeholder; we still
    // expose its URL — callers can decide whether to drop it.
    let imageUrl = '';
    let imageTitle = '';
    const imgA = c.querySelector("a[class*='productImage']");
    const img = imgA ? imgA.querySelector('img') : null;
    if (img) {
      imageUrl = img.getAttribute('src') || '';
      imageTitle = img.getAttribute('title') || '';
    }
    // Fallback: any first <img> in the card.
    if (!img) {
      const anyImg = c.querySelector('img');
      if (anyImg) {
        imageUrl = anyImg.getAttribute('src') || '';
        imageTitle = anyImg.getAttribute('title') || '';
      }
    }

    // Sponsor / data owner (often a reseller like "Vodafone").
    let sponsor = '';
    const sponsorEl = c.querySelector("[class*='sponsor']");
    if (sponsorEl) {
      // Strip the "(own your brand)" tooltip text the sponsor div
      // contains as a sub-element.
      let s = txt(sponsorEl);
      s = s.replace(/\(own your brand\)/i, '').trim();
      sponsor = s;
    }

    // Manufacturer SKU / product code — the <p> inside the title
    // container, immediately after the title anchor.
    let productCode = '';
    const titleContainer = c.querySelector("[class*='titleContainer']");
    if (titleContainer) {
      const sku = titleContainer.querySelector('p');
      if (sku) productCode = txt(sku);
    }

    // Specs blurb — the descriptionText paragraph (with the trailing
    // "More" affordance stripped).
    let specs = '';
    const descEl = c.querySelector("[class*='descriptionText']");
    if (descEl) {
      const clone = descEl.cloneNode(true);
      // Remove the in-line "More" link the SPA injects at the end.
      clone.querySelectorAll('a, i').forEach((n) => n.remove());
      specs = txt(clone);
    }

    if (!title || !href) continue;
    out.push({
      title,
      href,
      image_src: imageUrl,
      image_title: imageTitle,
      sponsor,
      product_code: productCode,
      specs,
    });
  }
  return out;
}
"""


# Image placeholder served for non-paying brands. We treat it as "no image".
_PLACEHOLDER_IMG_FRAGMENT = "/dist/bf5b29be"

# Icecat article-URL regex: the filename ends with ``-<numericid>.html``
# (with the leading category slug as the first ``-``-delimited segment).
_FILENAME_RE = re.compile(r"^([^-]+)-(.*?)-(\d+)\.html?$", re.IGNORECASE)


class IcecatEngine(BaseEngine):
    """Search the Icecat open product catalog via its public web search page.

    The Icecat REST API at ``https://api.icecat.biz/...`` would give richer,
    structured data (incl. full feature lists in 70+ languages), but it
    requires a registered Icecat account and a username/password basic-auth
    handshake. To keep the adapter free and login-less we use the public web
    search page instead — same data, just rendered as React cards we parse.
    """

    name = "icecat"

    def _do_search(self, query: str, limit: int = 10) -> list[SearchResult]:
        url = _SEARCH_URL.format(q=urllib.parse.quote_plus(query))
        if not safe_goto(self.page, url, timeout=_NAV_TIMEOUT_MS):
            log.error("[icecat] navigation failed: %s", url)
            return []

        # The body is just <div id="ReactContainer"></div> on first paint;
        # poll until the SPA mounts cards (or we time out).
        deadline = time.monotonic() + _RESULT_WAIT_MS / 1000.0
        last_count = 0
        stable_polls = 0
        while time.monotonic() < deadline:
            try:
                count = self.page.evaluate(
                    "(sel) => document.querySelectorAll(sel).length",
                    _CARD_SELECTOR,
                )
            except Exception as e:
                log.warning("[icecat] selector poll error: %s", e)
                count = 0

            if count and count == last_count:
                stable_polls += 1
                # Two consecutive polls with the same non-zero count means
                # the SPA has finished filling the visible page.
                if stable_polls >= 2:
                    break
            else:
                stable_polls = 0
            last_count = count
            time.sleep(_POLL_INTERVAL_MS / 1000.0)

        if last_count == 0:
            log.warning("[icecat] no product cards rendered within %dms", _RESULT_WAIT_MS)

        # Parse the cards in a single JS pass.
        try:
            rows = self.page.evaluate(_PARSE_JS, _SITE_BASE) or []
        except Exception as e:
            log.error("[icecat] parse evaluate failed: %s", e)
            return []

        results: list[SearchResult] = []
        seen_urls: set[str] = set()
        for row in rows:
            href = row.get("href") or ""
            full_url = self._absolutize(href)
            if not full_url or full_url in seen_urls:
                continue
            seen_urls.add(full_url)

            title = (row.get("title") or "").strip()
            if not title:
                continue

            category, brand_from_url, icecat_id = self._parse_product_url(full_url)
            sponsor = (row.get("sponsor") or "").strip()
            specs = (row.get("specs") or "").strip()
            product_code = (row.get("product_code") or "").strip()
            image_src = (row.get("image_src") or "").strip()

            # Best-effort brand extraction. The page renders the *data sponsor*
            # in the sponsor div (often a reseller, e.g. "Vodafone"), and the
            # title is typically "<sponsor> <brand> <model> <specs>...". The
            # brand we want is therefore the second token of the title when
            # the title starts with the sponsor; otherwise the first token.
            brand = self._extract_brand(title, sponsor) or brand_from_url

            # Skip the placeholder image so callers don't get false-positives
            # on a "valid" image URL that everyone shares.
            image_url = ""
            if image_src and _PLACEHOLDER_IMG_FRAGMENT not in image_src:
                image_url = self._absolutize(image_src)

            snippet_parts: list[str] = []
            if brand:
                snippet_parts.append(brand)
            if category:
                snippet_parts.append(category)
            if specs:
                snippet_parts.append(specs)
            snippet = " · ".join(snippet_parts)

            r = SearchResult(title=title, url=full_url, snippet=snippet)
            r.brand = brand
            r.category = category
            r.image_url = image_url
            r.specs = specs
            r.product_code = product_code
            r.icecat_id = icecat_id
            r.sponsor = sponsor
            results.append(r)
            if len(results) >= limit:
                break

        log.info("[icecat] Found %d results for %r", len(results), query)
        return results

    # ------------------------------------------------------------------ utils
    @staticmethod
    def _absolutize(href: str) -> str:
        if not href:
            return ""
        if href.startswith("http://") or href.startswith("https://"):
            return href
        if href.startswith("//"):
            return "https:" + href
        if href.startswith("/"):
            return _SITE_BASE + href
        return urllib.parse.urljoin(_SITE_BASE + "/", href)

    @staticmethod
    def _parse_product_url(url: str) -> tuple[str, str, str]:
        """Parse ``/en/p/<sponsor>/<sku>/<filename>.html`` → (category, brand, id).

        The filename layout is::

            <category>-[<ean>-]<brand+model>-<numericid>.html

        We always recover the category (first ``-`` segment) and the numeric
        Icecat id (trailing all-digits segment). The middle "<brand+model>"
        portion can be very noisy (it's a slugified product name), so we
        only return it as a *fallback* brand if the title-based extraction
        fails — and we keep just its first ``+``-separated token.
        """
        try:
            path = urllib.parse.urlparse(url).path
        except Exception:
            return "", "", ""
        # path = "/en/p/<sponsor>/<sku>/<filename>.html"
        parts = [p for p in path.split("/") if p]
        if len(parts) < 4 or parts[1] != "p":
            # Not a /en/p/... product URL — bail.
            return "", "", ""
        filename = parts[-1]
        # Drop the .html (or .htm) suffix.
        for suf in (".html", ".htm"):
            if filename.lower().endswith(suf):
                filename = filename[: -len(suf)]
                break
        # Split off the trailing numeric id.
        m = re.match(r"^(.*)-(\d+)$", filename)
        if not m:
            return "", "", ""
        body, icecat_id = m.group(1), m.group(2)
        segs = body.split("-")
        if not segs:
            return "", "", icecat_id
        category = segs[0]
        # The brand-fallback portion is everything between category and id;
        # the *last* segment of that range is "<brand>+<model>".
        brand_fallback = ""
        if len(segs) >= 2:
            brand_model = segs[-1]
            tokens = brand_model.split("+")
            if tokens:
                brand_fallback = tokens[0]
        return category, brand_fallback, icecat_id

    @staticmethod
    def _extract_brand(title: str, sponsor: str) -> str:
        """Pull the manufacturer brand out of the rendered title.

        The title is typically ``"<sponsor> <brand> <model> <specs>..."`` for
        sponsor-owned data (e.g. ``"Vodafone Apple iPhone 6s 11.9 cm..."``),
        and ``"<brand> <model> <specs>..."`` for first-party brand data.

        We strip the sponsor prefix when present, then take the first token
        as the brand. We also reject obviously-numeric or punctuation tokens
        in case the title doesn't follow this shape.
        """
        if not title:
            return ""
        t = title.strip()
        if sponsor:
            sp = sponsor.strip()
            if sp and t.lower().startswith(sp.lower() + " "):
                t = t[len(sp):].strip()
        # First whitespace-delimited token, with surrounding punctuation
        # stripped.
        first = t.split(maxsplit=1)[0] if t else ""
        first = first.strip(" \t,.:;()[]\"'")
        if not first or first.isdigit():
            return ""
        return first
