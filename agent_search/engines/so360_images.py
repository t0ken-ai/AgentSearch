"""360 Images (好搜图片 / image.so.com) search adapter.

360 exposes a public JSON endpoint at ``image.so.com/j`` which
returns full-resolution URLs (``img``), thumbnails (``thumb``), and
source page URLs (``link`` / ``source_url``). Cleaner than scraping
the rendered SERP, so we hit the API directly and fall back to DOM
scraping if it changes.
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


class So360ImagesEngine(ImageSearchEngine):
    name = "so360_images"
    max_retries = 1

    def _do_image_search(self, query, limit):
        q = urllib.parse.quote(query)

        # Seed cookies with the home page first.
        try:
            safe_goto(self.page, "https://image.so.com/", timeout=15000,
                      retries=1)
            human_delay(0.3, 0.6)
        except Exception:
            pass

        api = (
            f"https://image.so.com/j?q={q}&pn={max(30, limit + 10)}"
            f"&pd=0&src=srp"
        )
        out: list[ImageSearchResult] = []
        try:
            self.page.goto(api, timeout=20000, wait_until="domcontentloaded")
            txt = self.page.inner_text("body") or ""
            if txt.strip().startswith("{"):
                data = json.loads(txt)
                items = data.get("list") or data.get("data") or []
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    image_url = (
                        it.get("img") or it.get("imgurl")
                        or it.get("image_url") or ""
                    )
                    thumb = it.get("thumb") or it.get("imgurl") or image_url
                    if not looks_like_image_url(image_url) and \
                       not looks_like_image_url(thumb):
                        continue
                    out.append(ImageSearchResult(
                        image_url=image_url or thumb,
                        thumbnail_url=thumb or image_url,
                        source_page_url=(it.get("link")
                                         or it.get("source_url")
                                         or it.get("source") or ""),
                        title=(it.get("title") or "").strip(),
                        width=int(it.get("width") or 0) or None,
                        height=int(it.get("height") or 0) or None,
                    ))
                    if len(out) >= limit:
                        break
        except Exception as e:
            log.debug("[so360_images] API path failed: %s", e)

        # Fallback to rendered SERP
        if not out:
            url = f"https://image.so.com/i?q={q}"
            try:
                if safe_goto(self.page, url, timeout=20000):
                    human_delay(1.0, 2.0)
                    self.page.evaluate("() => window.scrollBy(0, 2000)")
                    human_delay(0.5, 1.0)
            except Exception:
                pass
            base = self.page.url
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
