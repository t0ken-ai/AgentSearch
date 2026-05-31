"""DuckDuckGo Images search adapter.

DDG's image search is JS-driven: the page first GETs ``duckduckgo.com``
to acquire a one-shot ``vqd`` anti-CSRF token, then GETs
``duckduckgo.com/i.js?q=…&o=json&vqd=…`` for the JSON results.

We let Playwright handle the token acquisition by navigating to the
real image-search URL and reading the rendered DOM, then fall back to
a generic DOM scrape if the layout has shifted.
"""

from __future__ import annotations

import logging
import re
import urllib.parse

from ..core import safe_goto, human_delay
from ._image_base import (
    ImageSearchEngine, ImageSearchResult, absolutize_url,
    looks_like_image_url, scrape_imgs_from_dom,
)

log = logging.getLogger(__name__)


_DDG_PARSE_JS = r"""
() => {
  const out = [];
  // DDG's image grid wraps each tile in a .tile--img link
  const tiles = document.querySelectorAll(
    '.tile--img, .tile--img__media, [data-id^="tile-img-"]'
  );
  for (const t of tiles) {
    const a = t.tagName === 'A' ? t : t.querySelector('a');
    const img = t.querySelector('img');
    if (!img) continue;
    const url = img.getAttribute('data-src') || img.getAttribute('src') || '';
    if (!url) continue;
    out.push({
      url: url,
      pageUrl: a ? (a.getAttribute('href') || '') : '',
      alt: img.getAttribute('alt') || '',
      w: img.naturalWidth || null,
      h: img.naturalHeight || null,
    });
  }
  return out;
}
"""


class DuckDuckGoImagesEngine(ImageSearchEngine):
    name = "duckduckgo_images"
    max_retries = 1

    def _do_image_search(self, query, limit):
        q = urllib.parse.quote(query)
        url = f"https://duckduckgo.com/?q={q}&iax=images&ia=images"
        if not safe_goto(self.page, url, timeout=25000):
            return []
        # The image grid is rendered after a deferred JS bootstrap. Give
        # it a beat + scroll once to materialise more tiles.
        human_delay(1.5, 2.5)
        try:
            self.page.evaluate("() => window.scrollBy(0, 2000)")
            human_delay(0.6, 1.2)
        except Exception:
            pass

        raw = []
        try:
            raw = self.page.evaluate(_DDG_PARSE_JS) or []
        except Exception as e:
            log.debug("[duckduckgo_images] specific parser failed: %s", e)

        base = self.page.url
        out: list[ImageSearchResult] = []
        for r in raw:
            u = absolutize_url(r.get("url", ""), base)
            if not looks_like_image_url(u):
                continue
            out.append(ImageSearchResult(
                image_url=u,
                thumbnail_url=u,
                source_page_url=absolutize_url(r.get("pageUrl", ""), base),
                title=(r.get("alt") or "").strip(),
                width=r.get("w"),
                height=r.get("h"),
            ))
            if len(out) >= limit:
                break

        # Fallback
        if not out:
            for s in scrape_imgs_from_dom(self.page, base_url=base):
                u = s["url"]
                if not looks_like_image_url(u):
                    continue
                out.append(ImageSearchResult(
                    image_url=u, thumbnail_url=u,
                    source_page_url=s.get("pageUrl", ""),
                    title=s.get("alt", ""),
                    width=s.get("w"), height=s.get("h"),
                ))
                if len(out) >= limit:
                    break
        return out
