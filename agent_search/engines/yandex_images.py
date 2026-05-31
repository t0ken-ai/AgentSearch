"""Yandex Images search adapter.

Yandex stores per-result metadata as a JSON blob on each
``.serp-item`` element via the ``data-bem`` attribute. We parse that
to get the full original URL + thumb + source page + dimensions in
one shot. Falls back to a generic DOM scrape on layout changes.
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


_YANDEX_PARSE_JS = r"""
() => {
  const out = [];
  const items = document.querySelectorAll('.serp-item, .SerpItem');
  for (const it of items) {
    const bem = it.getAttribute('data-bem');
    if (!bem) continue;
    try {
      const data = JSON.parse(bem);
      // The relevant payload is typically under 'serp-item' key.
      const meta = data['serp-item'] || data['SerpItem'] || data;
      if (!meta) continue;
      const img = meta.img_href || meta.imgHref || '';
      const thumb = (meta.preview && meta.preview[0] && meta.preview[0].url) || '';
      const page = meta.url || (meta.snippet && meta.snippet.url) || '';
      const w = (meta.preview && meta.preview[0] && meta.preview[0].w) || meta.w;
      const h = (meta.preview && meta.preview[0] && meta.preview[0].h) || meta.h;
      const title = (meta.snippet && meta.snippet.title) || meta.alt || '';
      out.push({img, thumb, page, w, h, title});
    } catch (e) { /* skip */ }
  }
  return out;
}
"""


class YandexImagesEngine(ImageSearchEngine):
    name = "yandex_images"
    max_retries = 1

    def _do_image_search(self, query, limit):
        q = urllib.parse.quote(query)
        url = f"https://yandex.com/images/search?text={q}"
        if not safe_goto(self.page, url, timeout=25000):
            return []
        human_delay(1.2, 2.2)
        try:
            self.page.evaluate("() => window.scrollBy(0, 2000)")
            human_delay(0.5, 1.0)
        except Exception:
            pass

        raw = []
        try:
            raw = self.page.evaluate(_YANDEX_PARSE_JS) or []
        except Exception as e:
            log.debug("[yandex_images] specific parser failed: %s", e)

        base = self.page.url
        out: list[ImageSearchResult] = []
        for r in raw:
            img = absolutize_url(r.get("img", ""), base)
            thumb = absolutize_url(r.get("thumb", ""), base)
            page = absolutize_url(r.get("page", ""), base)
            chosen = img or thumb
            if not looks_like_image_url(chosen):
                continue
            out.append(ImageSearchResult(
                image_url=chosen,
                thumbnail_url=thumb or chosen,
                source_page_url=page,
                title=(r.get("title") or "").strip(),
                width=r.get("w") or None,
                height=r.get("h") or None,
            ))
            if len(out) >= limit:
                break

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
