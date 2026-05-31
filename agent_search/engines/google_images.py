"""Google Images search adapter.

Strategy: navigate to ``google.com/search?q=X&tbm=isch``, scrape the
rendered DOM. Google's image SERP is heavily JS-driven and the *true*
original URL is buried in inline JSON; the cheaper, reliable path is
to grab the rendered ``<img>`` thumbnails — they're directly
downloadable from Google's CDN.

Trade-off: ``image_url`` is the on-Google thumbnail (typically
~200-500px wide). For the agent's "show me ~N images of X" workflow
this is the right choice — fast, works without clicking each card.
The ``source_page_url`` (when available) lets the agent re-extract
the original at full resolution if needed.
"""

from __future__ import annotations

import logging
import urllib.parse

from ..core import safe_goto, human_delay
from ._image_base import (
    ImageSearchEngine, ImageSearchResult, absolutize_url,
    looks_like_image_url, scrape_imgs_from_dom,
)

log = logging.getLogger(__name__)


# Card-level scraper: walk Google's image-result containers (which keep
# changing — we try a few selectors), grab the <img> + its parent
# anchor's href.
_GOOGLE_PARSE_JS = r"""
(limit) => {
  const out = [];
  const seen = new Set();
  // Google rotates these container selectors every few months. We try
  // them all and take whichever yields the most cards.
  const containerSelectors = [
    'div.eA0Zlc.WghbWd.FnEtTd.mkpRId.m3LIae.RLdvSe',  // post-2024 grid
    'div[data-ri]',                                     // legacy data-ri
    'div.isv-r',                                        // older grid
    'div.rg_bx',                                        // very old
    'div.eA0Zlc',                                       // partial match
  ];
  let cards = [];
  for (const sel of containerSelectors) {
    const list = document.querySelectorAll(sel);
    if (list.length > cards.length) cards = Array.from(list);
  }
  // If specific selectors miss, fall back to "any <a> wrapping an <img>".
  if (cards.length === 0) {
    cards = Array.from(document.querySelectorAll('a:has(img)'));
  }
  for (const card of cards) {
    if (out.length >= limit * 3) break;
    const img = card.querySelector('img');
    if (!img) continue;
    let url = img.getAttribute('src') ||
              img.getAttribute('data-src') ||
              img.getAttribute('data-iurl') ||
              '';
    if (!url) continue;
    if (seen.has(url)) continue;
    // The parent anchor's href usually points to the source-page (or
    // /imgres?... which embeds it).
    let pageUrl = '';
    const a = card.tagName === 'A' ? card : card.querySelector('a[href]');
    if (a) pageUrl = a.getAttribute('href') || '';
    seen.add(url);
    out.push({
      url: url,
      pageUrl: pageUrl,
      alt: img.getAttribute('alt') || '',
      w: img.naturalWidth || null,
      h: img.naturalHeight || null,
    });
  }
  return out;
}
"""


class GoogleImagesEngine(ImageSearchEngine):
    name = "google_images"
    max_retries = 1

    def _do_image_search(self, query, limit):
        q = urllib.parse.quote(query)
        url = (
            f"https://www.google.com/search?q={q}"
            f"&tbm=isch&hl=en&safe=off"
        )
        if not safe_goto(self.page, url, timeout=25000):
            return []
        human_delay(1.0, 2.0)
        # Scroll once to trigger lazy-load of more thumbnails
        try:
            self.page.evaluate("() => window.scrollBy(0, 1500)")
            human_delay(0.5, 1.0)
        except Exception:
            pass

        try:
            raw = self.page.evaluate(_GOOGLE_PARSE_JS, limit) or []
        except Exception as e:
            log.debug("[google_images] specific parser failed: %s", e)
            raw = []

        # Fallback to generic DOM scrape if specific parser came up dry.
        if not raw:
            base = self.page.url
            scraped = scrape_imgs_from_dom(self.page, base_url=base)
            raw = [{"url": s["url"], "pageUrl": s["pageUrl"],
                    "alt": s["alt"], "w": s["w"], "h": s["h"]}
                   for s in scraped]

        base = self.page.url
        out = []
        for r in raw:
            u = absolutize_url(r.get("url", ""), base)
            if not looks_like_image_url(u):
                continue
            page_u = absolutize_url(r.get("pageUrl", ""), base)
            # Google wraps source pages in /imgres?...&imgrefurl=<real>
            if "/imgres" in page_u or "google.com/url" in page_u:
                try:
                    qs = urllib.parse.parse_qs(
                        urllib.parse.urlparse(page_u).query
                    )
                    real = (qs.get("imgrefurl") or qs.get("url") or [""])[0]
                    if real:
                        page_u = real
                except Exception:
                    pass
            out.append(ImageSearchResult(
                image_url=u,
                thumbnail_url=u,
                source_page_url=page_u,
                title=(r.get("alt") or "").strip(),
                width=r.get("w"),
                height=r.get("h"),
            ))
            if len(out) >= limit:
                break
        return out
