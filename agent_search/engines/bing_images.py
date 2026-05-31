"""Bing Images search adapter.

Bing's image SERP wraps each result in a ``.iusc`` element with a
``m=...`` JSON attribute that exposes both the original URL (``murl``)
and thumbnail (``turl``) plus dimensions and a source-page URL
(``purl``). Much cleaner than Google's, so we can return real
full-resolution URLs.
"""

from __future__ import annotations

import json
import logging
import urllib.parse

from ..core import safe_goto, human_delay
from ._image_base import (
    ImageSearchEngine, ImageSearchResult, absolutize_url,
    looks_like_image_url, scrape_imgs_from_dom,
)

log = logging.getLogger(__name__)


_BING_PARSE_JS = r"""
() => {
  const out = [];
  // .iusc holds the JSON metadata; .imgpt is the older selector.
  const cards = document.querySelectorAll('.iusc, a.iusc');
  for (const card of cards) {
    const m = card.getAttribute('m');
    if (!m) continue;
    try {
      const data = JSON.parse(m);
      out.push({
        murl: data.murl || '',
        turl: data.turl || '',
        purl: data.purl || '',
        title: data.t || '',
        desc: data.desc || '',
      });
    } catch (e) { /* skip malformed */ }
  }
  return out;
}
"""


class BingImagesEngine(ImageSearchEngine):
    name = "bing_images"
    max_retries = 1

    def _do_image_search(self, query, limit):
        q = urllib.parse.quote(query)
        url = f"https://www.bing.com/images/search?q={q}&form=HDRSC2&first=1"
        if not safe_goto(self.page, url, timeout=25000):
            return []
        human_delay(1.0, 2.0)
        try:
            self.page.evaluate("() => window.scrollBy(0, 1500)")
            human_delay(0.4, 0.8)
        except Exception:
            pass

        raw = []
        try:
            raw = self.page.evaluate(_BING_PARSE_JS) or []
        except Exception as e:
            log.debug("[bing_images] iusc parser failed: %s", e)

        out: list[ImageSearchResult] = []
        if raw:
            for r in raw:
                murl = (r.get("murl") or "").strip()
                turl = (r.get("turl") or "").strip()
                if not (murl or turl):
                    continue
                if not looks_like_image_url(murl) and not looks_like_image_url(turl):
                    continue
                out.append(ImageSearchResult(
                    image_url=murl or turl,
                    thumbnail_url=turl or murl,
                    source_page_url=(r.get("purl") or "").strip(),
                    title=(r.get("title") or r.get("desc") or "").strip(),
                ))
                if len(out) >= limit:
                    break

        # Fallback to generic DOM scrape on failure
        if not out:
            base = self.page.url
            for s in scrape_imgs_from_dom(self.page, base_url=base):
                u = s["url"]
                if not looks_like_image_url(u):
                    continue
                out.append(ImageSearchResult(
                    image_url=u,
                    thumbnail_url=u,
                    source_page_url=s.get("pageUrl", ""),
                    title=s.get("alt", ""),
                    width=s.get("w"), height=s.get("h"),
                ))
                if len(out) >= limit:
                    break
        return out
