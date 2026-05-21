"""Pexels image search adapter (HTML scraping, no API key needed).

Strategy
--------
Visit ``https://www.pexels.com/search/<query>/`` and harvest each
``<article class="MediaCard_card_*">`` tile. Inside each card:

* an ``<a class="MediaCard_content_* Link_link_*" href="/photo/<slug>-<id>/">``
  for the detail-page link
* an ``<img>`` with ``src`` / ``srcset`` from ``images.pexels.com``
* a download ``<a href="…?dl=pexels-<photographer-slug>-<userid>-<photoid>.jpg…">``
  whose filename embeds the photographer's slug — that's our only
  reliable source for the photographer name on the search SERP

Pexels does not gate the public search page so a single navigation +
two scrolls is enough to fetch ~24 cards.

Each :class:`SearchResult` carries:

* ``photo_id``    — numeric id from the URL
* ``image_url``   — best-quality preview URL (``w=1280`` candidate from srcset
                    if present, else the eager ``src``)
* ``photographer``       — display name derived from the download URL slug
                           (``"Chinar Minar"`` from ``"pexels-chinar-minar-…"``)
* ``photographer_slug``  — raw slug
* ``alt_text``    — image alt text
"""

from __future__ import annotations

import logging
import random
import re
import time
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

PEXELS_HOME = "https://www.pexels.com"
PEXELS_SEARCH = "https://www.pexels.com/search"

PHOTO_URL_RE = re.compile(
    r"https?://(?:www\.)?pexels\.com/photo/(?:.+-)?(\d+)/?", re.IGNORECASE
)

_PARSE_JS = r"""
(limit) => {
  // Detail-page links live inside <article> cards.
  const detailRe = /pexels\.com\/photo\/(?:(.+?)-)?(\d+)\/?$/;
  const articles = Array.from(document.querySelectorAll('article'));
  const out = [];
  const seen = new Set();
  for (const card of articles) {
    if (out.length >= limit) break;
    // Find the detail link.
    const detail = Array.from(card.querySelectorAll('a[href]'))
      .find(a => detailRe.test(a.href));
    if (!detail) continue;
    const m = detail.href.match(detailRe);
    if (!m) continue;
    const photo_id = m[2];
    const slug = (m[1] || '').toLowerCase();
    if (seen.has(photo_id)) continue;
    seen.add(photo_id);

    const img = card.querySelector('img');
    let image_url = '';
    let alt_text = '';
    if (img) {
      // Prefer 1280-wide if available in srcset; else use eager src.
      const srcset = img.getAttribute('srcset') || '';
      const parts = srcset.split(',').map(s => s.trim()).filter(Boolean);
      // Each part is "URL ###w" — pick the largest width <= 1280, else last.
      let best = '';
      let bestWidth = 0;
      for (const part of parts) {
        const ws = part.match(/^(\S+)\s+(\d+)w$/);
        if (!ws) continue;
        const w = parseInt(ws[2], 10);
        if (w > bestWidth && w <= 1600) {
          bestWidth = w;
          best = ws[1];
        }
      }
      image_url = best || img.getAttribute('src') || '';
      alt_text = (img.getAttribute('alt') || '').trim();
    }

    // Download link includes the photographer slug:
    //   pexels-<photographer-slug>-<userid>-<photoid>.jpg
    let photographer = '';
    let photographer_slug = '';
    const dlA = Array.from(card.querySelectorAll('a[href]'))
      .find(a => /(\?|&)dl=pexels-/i.test(a.href));
    if (dlA) {
      const dm = dlA.href.match(/dl=pexels-([a-z0-9\-]+?)-\d+-\d+\.[a-z]+/i);
      if (dm) {
        photographer_slug = dm[1];
        photographer = photographer_slug
          .split('-')
          .filter(Boolean)
          .map(p => p.charAt(0).toUpperCase() + p.slice(1))
          .join(' ');
      }
    }

    // Title: slug-derived or alt text (with the "Free … Stock Photo"
    // boilerplate trimmed off).
    let title = slug.replace(/-/g, ' ').trim();
    if (!title) {
      title = alt_text
        .replace(/^Free\s+/i, '')
        .replace(/\s+Stock Photo\.?$/i, '');
    }
    if (!title) title = detail.href;

    out.push({
      photo_id, slug, url: detail.href, image_url, alt_text,
      photographer, photographer_slug, title,
    });
  }
  return {articles_seen: articles.length, rows: out};
}
"""


class PexelsEngine(BaseEngine):
    """Pexels image search via the public ``/search/<q>/`` page."""

    name = "pexels"
    max_retries = 2

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        slug = query.strip().replace(" ", "%20")
        url = f"{PEXELS_SEARCH}/{urllib.parse.quote(slug, safe='%20')}/"
        log.info("[pexels] navigating to %s", url)
        if not safe_goto(self.page, url, timeout=30000):
            return []

        for _ in range(3):
            human_delay(1.0, 1.6)
            try:
                self.page.evaluate(
                    "(y) => window.scrollBy(0, y)",
                    random.randint(400, 900),
                )
            except Exception:
                pass

        try:
            data = self.page.evaluate(_PARSE_JS, max(limit, 5)) or {}
        except Exception as e:
            log.warning("[pexels] parse JS failed: %s", e)
            data = {}

        articles_seen = int(data.get("articles_seen") or 0)
        rows = data.get("rows") or []

        self.last_status = {
            "url": getattr(self.page, "url", ""),
            "articles_seen": articles_seen,
            "count": len(rows),
        }

        results: list[SearchResult] = []
        for row in rows:
            url2 = (row.get("url") or "").strip()
            if not url2:
                continue
            title = (row.get("title") or "").strip()[:200] or url2
            photographer = (row.get("photographer") or "").strip()
            photographer_slug = (row.get("photographer_slug") or "").strip()
            image_url = (row.get("image_url") or "").strip()
            alt_text = (row.get("alt_text") or "").strip()
            photo_id = (row.get("photo_id") or "").strip()

            head = []
            if photographer:
                head.append(f"by {photographer}")
            elif photographer_slug:
                head.append(f"@{photographer_slug}")
            head_text = " · ".join(head)
            snippet_parts = []
            if head_text:
                snippet_parts.append(head_text)
            clean_alt = re.sub(r"^Free\s+|\s+Stock Photo\.?$", "", alt_text).strip()
            if clean_alt and clean_alt.lower() != title.lower():
                snippet_parts.append(clean_alt)
            snippet = " — ".join(snippet_parts)[:320]

            r = SearchResult(title=title, url=url2, snippet=snippet)
            r.photo_id = photo_id                   # type: ignore[attr-defined]
            r.image_url = image_url                 # type: ignore[attr-defined]
            r.photographer = photographer           # type: ignore[attr-defined]
            r.photographer_slug = photographer_slug  # type: ignore[attr-defined]
            r.alt_text = alt_text                   # type: ignore[attr-defined]
            results.append(r)
            if len(results) >= limit:
                break
        return results
