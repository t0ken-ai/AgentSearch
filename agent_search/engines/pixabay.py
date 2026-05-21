"""Pixabay image search adapter (HTML scraping, no API key needed).

Strategy
--------
Visit ``https://pixabay.com/images/search/<query>/`` and harvest each
gallery card. Cards are wrapped in ``div.container--*`` (the suffix
varies between deploys). Inside each card:

* an ``<a class="link--*" href="/(photos|illustrations|vectors)/<slug>-<id>/">``
* an ``<img>`` with ``src`` / ``srcset`` from ``cdn.pixabay.com``
* a sibling ``<a href="/users/<handle>-<uid>/">`` for the photographer
* a small text node with the view-counter (e.g. ``"727"`` or ``"4.7K"``)

Pixabay does not gate the public search page, so a single navigation
plus a couple of scrolls is enough to fetch ~20 cards.

Each :class:`SearchResult` carries:

* ``photo_id``       — numeric id from the URL (e.g. ``"5499649"``)
* ``image_url``      — best-quality preview URL (``_1280.jpg`` if a srcset
                       2x candidate is present, else the eager ``src``)
* ``user`` / ``user_url`` — photographer handle and profile URL
* ``views``          — view counter text (raw, may be ``"4.7K"``)
* ``content_type``   — ``"photo"`` / ``"illustration"`` / ``"vector"``
* ``tags``           — empty (Pixabay surfaces tags only on detail pages)
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

PIXABAY_HOME = "https://pixabay.com"
PIXABAY_SEARCH = "https://pixabay.com/images/search"

PHOTO_URL_RE = re.compile(
    r"https?://pixabay\.com/(photos|illustrations|vectors|videos)/[^/?#]+-(\d+)/?",
    re.IGNORECASE,
)


_PARSE_JS = r"""
(limit) => {
  // Detail-page anchors look like /photos/<slug>-<id>/ etc.
  const detailRe = /pixabay\.com\/(photos|illustrations|vectors|videos)\/([^/]+?)-(\d+)\/?$/;
  const anchors = Array.from(document.querySelectorAll('a[href]'))
    .filter(a => detailRe.test(a.href));

  const out = [];
  const seen = new Set();
  for (const a of anchors) {
    if (out.length >= limit) break;
    const m = a.href.match(detailRe);
    if (!m) continue;
    const content_type = m[1].replace(/s$/, '');  // photos -> photo
    const slug = m[2];
    const photo_id = m[3];
    if (seen.has(photo_id)) continue;
    seen.add(photo_id);

    const card = a.closest('div[class*="container" i]') || a.parentElement;
    const img = a.querySelector('img');

    // Prefer the 2x candidate from srcset (1280-wide), else src.
    let image_url = '';
    if (img) {
      const srcset = img.getAttribute('srcset') || '';
      const srcsetParts = srcset.split(',').map(s => s.trim()).filter(Boolean);
      const x2 = srcsetParts.find(s => / 2x$/.test(s));
      if (x2) image_url = x2.replace(/ 2x$/, '').trim();
      if (!image_url) image_url = img.getAttribute('src') || '';
    }
    const img_alt = img ? (img.getAttribute('alt') || '').trim() : '';
    const img_title = img ? (img.getAttribute('title') || '').trim() : '';

    let user = '', user_url = '';
    const userA = (card || a).querySelector('a[href*="/users/"]');
    if (userA) {
      user_url = userA.href || '';
      user = (userA.textContent || '').trim();
    }

    // Counter: scan card text for a number (e.g. "727" or "4.7K").
    let views = '';
    if (card) {
      const txt = (card.textContent || '').trim();
      const cm = txt.match(/(\d+(?:\.\d+)?[KkMm]?)/);
      if (cm) views = cm[1];
    }

    // Title: prefer the human-readable slug, then img title/alt.
    let title = slug.replace(/-/g, ' ').trim();
    if (!title && img_title) {
      // "Download free HD stock image of Sea Wave" -> "Sea Wave"
      const tm = img_title.match(/of\s+(.+)$/i);
      title = tm ? tm[1] : img_title;
    }
    if (!title) title = img_alt || a.href;

    out.push({
      content_type, photo_id, slug, title,
      url: a.href, image_url, img_alt, img_title,
      user, user_url, views,
    });
  }
  return {anchors_seen: anchors.length, rows: out};
}
"""


class PixabayEngine(BaseEngine):
    """Pixabay image search via the public ``/images/search/<q>/`` page."""

    name = "pixabay"
    max_retries = 2

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        slug = query.strip().replace(" ", "+")
        url = f"{PIXABAY_SEARCH}/{urllib.parse.quote(slug, safe='+')}/"
        log.info("[pixabay] navigating to %s", url)
        if not safe_goto(self.page, url, timeout=30000):
            return []

        # Let the gallery load + lazy-load images.
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
            log.warning("[pixabay] parse JS failed: %s", e)
            data = {}

        anchors_seen = int(data.get("anchors_seen") or 0)
        rows = data.get("rows") or []

        self.last_status = {
            "url": getattr(self.page, "url", ""),
            "anchors_seen": anchors_seen,
            "count": len(rows),
        }

        results: list[SearchResult] = []
        for row in rows:
            url2 = (row.get("url") or "").strip()
            if not url2:
                continue
            title = (row.get("title") or "").strip()[:200] or url2
            content_type = (row.get("content_type") or "photo").strip()
            user = (row.get("user") or "").strip()
            user_url = (row.get("user_url") or "").strip()
            image_url = (row.get("image_url") or "").strip()
            views = (row.get("views") or "").strip()
            photo_id = (row.get("photo_id") or "").strip()
            img_alt = (row.get("img_alt") or "").strip()

            head = []
            if user:
                head.append(f"by {user}")
            if views:
                head.append(f"👁 {views}")
            head_text = " · ".join(head)
            snippet_parts = []
            if head_text:
                snippet_parts.append(head_text)
            if img_alt and img_alt.lower() != title.lower():
                snippet_parts.append(img_alt)
            snippet = " — ".join(snippet_parts)[:320]

            r = SearchResult(title=title, url=url2, snippet=snippet)
            r.photo_id = photo_id          # type: ignore[attr-defined]
            r.image_url = image_url        # type: ignore[attr-defined]
            r.user = user                  # type: ignore[attr-defined]
            r.user_url = user_url          # type: ignore[attr-defined]
            r.views = views                # type: ignore[attr-defined]
            r.content_type = content_type  # type: ignore[attr-defined]
            r.alt_text = img_alt           # type: ignore[attr-defined]
            r.tags = []                    # type: ignore[attr-defined]
            results.append(r)
            if len(results) >= limit:
                break
        return results
