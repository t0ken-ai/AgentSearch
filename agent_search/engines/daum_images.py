"""Daum Images search adapter."""

from __future__ import annotations

import logging
import urllib.parse

from ..core import safe_goto, human_delay
from ._image_base import (
    ImageSearchEngine, ImageSearchResult, looks_like_image_url,
    scrape_imgs_from_dom,
)

log = logging.getLogger(__name__)


class DaumImagesEngine(ImageSearchEngine):
    name = "daum_images"
    max_retries = 1

    def _do_image_search(self, query, limit):
        q = urllib.parse.quote(query)
        url = f"https://search.daum.net/search?w=img&q={q}"
        if not safe_goto(self.page, url, timeout=25000):
            return []
        human_delay(1.0, 2.0)
        try:
            self.page.evaluate("() => window.scrollBy(0, 2000)")
            human_delay(0.5, 1.0)
        except Exception:
            pass

        base = self.page.url
        out: list[ImageSearchResult] = []
        for s in scrape_imgs_from_dom(self.page, base_url=base, min_dim=80):
            u = s["url"]
            if not looks_like_image_url(u):
                continue
            if "logo" in u.lower() or "/icon/" in u.lower():
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
