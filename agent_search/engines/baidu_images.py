"""Baidu Images (百度图片) search adapter.

Baidu exposes a public JSON endpoint at
``image.baidu.com/search/acjson`` that returns full-resolution URLs
(``thumbURL`` / ``middleURL`` / ``hoverURL`` / ``replaceUrl[]``). We
hit it directly via Playwright (so we get the same anti-bot + cookie
context as a real browser) instead of going through the rendered page.

When the JSON endpoint changes shape, we fall back to scraping the
DOM at ``image.baidu.com/search/index``.
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


class BaiduImagesEngine(ImageSearchEngine):
    name = "baidu_images"
    max_retries = 1

    def _do_image_search(self, query, limit):
        q = urllib.parse.quote(query)
        # First seed cookies by hitting the image home page.
        try:
            safe_goto(self.page, "https://image.baidu.com/", timeout=20000,
                      retries=1)
            human_delay(0.4, 0.8)
        except Exception:
            pass

        # JSON endpoint — pn = page offset, rn = results per page.
        rn = max(30, limit + 10)
        json_url = (
            f"https://image.baidu.com/search/acjson?tn=resultjson_com"
            f"&ipn=rj&word={q}&pn=0&rn={rn}&logid=0"
        )
        # We use page.goto and read response bytes via the rendered text.
        raw_json = ""
        try:
            self.page.goto(json_url, timeout=20000, wait_until="domcontentloaded")
            try:
                raw_json = self.page.inner_text("body") or ""
            except Exception:
                raw_json = ""
        except Exception as e:
            log.debug("[baidu_images] json endpoint nav failed: %s", e)

        out: list[ImageSearchResult] = []
        if raw_json and raw_json.strip().startswith("{"):
            try:
                data = json.loads(raw_json)
                items = data.get("data") or []
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    image_url = (
                        it.get("hoverURL")
                        or it.get("middleURL")
                        or it.get("thumbURL")
                        or ""
                    )
                    # Sometimes Baidu only ships the obfuscated objURL,
                    # not directly downloadable, plus a replaceUrl[] with
                    # plain ones. Walk replaceUrl for an http(s) one.
                    if not image_url:
                        for cand in (it.get("replaceUrl") or []):
                            if isinstance(cand, dict):
                                u = cand.get("ObjURL") or cand.get("FromURL") or ""
                                if u.startswith("http"):
                                    image_url = u
                                    break
                    if not looks_like_image_url(image_url):
                        continue
                    out.append(ImageSearchResult(
                        image_url=image_url,
                        thumbnail_url=it.get("thumbURL") or image_url,
                        source_page_url=it.get("fromPageTitleEnc") and
                            it.get("fromURL") or it.get("fromURL") or "",
                        title=(it.get("fromPageTitleEnc")
                               or it.get("fromPageTitle")
                               or it.get("desc") or "").strip(),
                        width=it.get("width") or None,
                        height=it.get("height") or None,
                    ))
                    if len(out) >= limit:
                        break
            except Exception as e:
                log.debug("[baidu_images] JSON parse failed: %s", e)

        # Fallback to rendered DOM at the user-facing URL.
        if not out:
            human_url = (
                f"https://image.baidu.com/search/index?tn=baiduimage"
                f"&word={q}"
            )
            try:
                if safe_goto(self.page, human_url, timeout=20000):
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
