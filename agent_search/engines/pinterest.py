"""Pinterest pin search adapter via the public ``/search/pins`` page.

Strategy
--------
Visit ``https://www.pinterest.com/search/pins/?q=<query>``. Pinterest
renders the masonry grid of pins server-side; even without auth we get
~28 visible ``/pin/<id>/`` anchors per page-load with ~14 images
hydrated and ``alt`` descriptions. A small scroll triggers more.

For every pin we extract:

* ``pin_id``    — numeric pin id from the URL
* ``image_url`` — best-quality preview from ``srcset`` (``736x`` if available)
* ``alt_text``  — image alt text (also used as the title)

Pinterest does not expose pin engagement counters (saves / clicks)
on the listed search SERP for unauthenticated users — to get those
you'd need to deeplink into the pin detail page. The search adapter
returns the discovery surface only.
"""

from __future__ import annotations

import logging
import random
import re
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

PINTEREST_HOME = "https://www.pinterest.com/"
PINTEREST_SEARCH = "https://www.pinterest.com/search/pins/"

PIN_ID_RE = re.compile(r"/pin/(\d+)/?")


_PARSE_JS = r"""
(limit) => {
  const anchors = Array.from(document.querySelectorAll('a[href*="/pin/"]'));
  const out = [];
  const seen = new Set();
  for (const a of anchors) {
    if (out.length >= limit) break;
    const m = a.href.match(/\/pin\/(\d+)\/?(?:\?.*)?$/);
    if (!m) continue;
    const pin_id = m[1];
    if (seen.has(pin_id)) continue;
    seen.add(pin_id);

    const img = a.querySelector('img[alt]') ||
                a.querySelector('img[srcset]') ||
                a.querySelector('img');
    let image_url = '';
    let alt_text = '';
    if (img) {
      alt_text = (img.getAttribute('alt') || '').trim();
      // Pick the largest srcset candidate.
      const srcset = img.getAttribute('srcset') || '';
      const parts = srcset.split(',').map(s => s.trim()).filter(Boolean);
      let best = '';
      let bestX = 0;
      for (const part of parts) {
        const m2 = part.match(/^(\S+)\s+(\d+(?:\.\d+)?)x$/);
        if (!m2) continue;
        const x = parseFloat(m2[2]);
        if (x > bestX) {
          bestX = x;
          best = m2[1];
        }
      }
      // 'best' may be a 236x/474x/736x i.pinimg.com URL.
      // Pinterest CDN keeps the same path, just different size.
      image_url = best || img.getAttribute('src') || '';
      // Upgrade to 736x where possible so callers get the high-res file.
      image_url = image_url.replace(/i\.pinimg\.com\/(?:236x|474x)\//,
                                    'i.pinimg.com/736x/');
    }

    const url = `https://www.pinterest.com/pin/${pin_id}/`;
    out.push({pin_id, url, image_url, alt_text});
  }
  return {anchors_seen: anchors.length, rows: out};
}
"""


class PinterestEngine(BaseEngine):
    """Pinterest pin search via the public /search/pins page."""

    name = "pinterest"
    max_retries = 2

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # No homepage warmup needed — /search/pins/ works directly.
        q = urllib.parse.quote(query)
        url = f"{PINTEREST_SEARCH}?q={q}"
        log.info("[pinterest] navigating to %s", url)
        if not safe_goto(self.page, url, timeout=30000):
            return []

        try:
            self.page.wait_for_selector('a[href*="/pin/"]', timeout=15000)
        except Exception:
            pass

        # Trigger lazy-load to hydrate more images.
        for _ in range(3):
            human_delay(0.8, 1.4)
            try:
                self.page.evaluate(
                    "(y) => window.scrollBy(0, y)",
                    random.randint(500, 900),
                )
            except Exception:
                pass

        try:
            data = self.page.evaluate(_PARSE_JS, max(limit * 2, 10)) or {}
        except Exception as e:
            log.warning("[pinterest] parse JS failed: %s", e)
            data = {}

        anchors_seen = int(data.get("anchors_seen") or 0)
        rows = data.get("rows") or []
        self.last_status = {
            "url": getattr(self.page, "url", ""),
            "anchors_seen": anchors_seen,
            "count": len(rows),
        }

        # Prefer rows that actually have an alt_text + image_url, fallback to bare ids.
        def quality(row: dict) -> int:
            return (1 if row.get("alt_text") else 0) + (1 if row.get("image_url") else 0)

        rows_sorted = sorted(rows, key=quality, reverse=True)

        results: list[SearchResult] = []
        for row in rows_sorted:
            url2 = (row.get("url") or "").strip()
            pin_id = (row.get("pin_id") or "").strip()
            if not url2 or not pin_id:
                continue
            alt_text = (row.get("alt_text") or "").strip()
            image_url = (row.get("image_url") or "").strip()

            title = alt_text[:200] if alt_text else f"Pin {pin_id}"
            head = []
            if image_url:
                head.append("📷")
            snippet = " · ".join(head)
            if alt_text:
                snippet = (snippet + " — " + alt_text) if snippet else alt_text
            snippet = snippet[:320]

            r = SearchResult(title=title, url=url2, snippet=snippet)
            r.pin_id = pin_id          # type: ignore[attr-defined]
            r.image_url = image_url    # type: ignore[attr-defined]
            r.alt_text = alt_text      # type: ignore[attr-defined]
            results.append(r)
            if len(results) >= limit:
                break
        return results
