"""Unsplash image search adapter (HTML scraping, no API key needed).

Strategy
--------
Visit ``https://unsplash.com/s/photos/<query>`` and harvest each
``<figure itemtype="https://schema.org/ImageObject">`` card. Each card
exposes:

* an ``<a itemprop="contentUrl">`` linking to ``/photos/<slug>-<id>``
* an ``<img>`` with the rendered photo (and a ``srcset`` of CDN sizes)
* an ``<a href="/@<username>">`` linking to the photographer's profile
* an ``<img alt="Go to <Name>'s profile">`` — the photographer's display name

Unsplash has no aggressive bot wall on the public search page, so a
single navigation + a small scroll is enough to get ~60 figures.

Each :class:`SearchResult` carries:

* ``photo_id``    — id from URL slug (e.g. ``"lD252H6SW14"``)
* ``image_url``   — canonical CDN image URL
* ``photographer`` — display name (e.g. ``"Ales Krivec"``)
* ``photographer_username`` — handle (e.g. ``"aleskrivec"``)
* ``alt_text``    — image alt text describing the photo
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

UNSPLASH_HOME = "https://unsplash.com"
UNSPLASH_SEARCH = "https://unsplash.com/s/photos"

PHOTO_URL_RE = re.compile(
    r"https?://unsplash\.com/photos/(?:([^/?#]+)-)?([A-Za-z0-9_\-]+)/?"
)

_PARSE_JS = r"""
(limit) => {
  const figs = Array.from(
    document.querySelectorAll('figure[itemtype="https://schema.org/ImageObject"]')
  );
  const out = [];
  const seen = new Set();
  for (const fig of figs) {
    if (out.length >= limit) break;
    const photoLink = fig.querySelector('a[itemprop="contentUrl"]') ||
                      fig.querySelector('a[href*="/photos/"]');
    if (!photoLink) continue;
    let url = photoLink.href || photoLink.getAttribute('href') || '';
    if (url.startsWith('/')) url = 'https://unsplash.com' + url;

    // Pull the photo id from the URL slug. Unsplash ids are usually 11
    // chars of [A-Za-z0-9_-], but legacy URLs may have shorter ids.
    let photo_id = '';
    let slug = '';
    let m = url.match(/\/photos\/(.+)-([A-Za-z0-9_\-]{11})\/?(?:[?#].*)?$/);
    if (m) {
      slug = m[1];
      photo_id = m[2];
    } else {
      m = url.match(/\/photos\/(?:(.+)-)?([A-Za-z0-9_\-]+)\/?(?:[?#].*)?$/);
      if (m) {
        slug = m[1] || '';
        photo_id = m[2];
      }
    }
    if (seen.has(photo_id)) continue;
    seen.add(photo_id);

    // The photo image is the largest <img> inside the contentUrl link.
    let imgEl = photoLink.querySelector('img[srcset]') || photoLink.querySelector('img');
    if (!imgEl) {
      // Fallback: any non-profile <img> in the figure.
      const imgs = Array.from(fig.querySelectorAll('img'))
        .filter(i => !(i.src || '').includes('profile'));
      imgEl = imgs[imgs.length - 1] || fig.querySelector('img');
    }
    const image_url = imgEl ? (imgEl.getAttribute('src') || '') : '';
    const alt_text = imgEl ? (imgEl.getAttribute('alt') || '').trim() : '';

    // Photographer link looks like /@username.
    const userLink = fig.querySelector('a[href^="/@"]');
    let photographer_username = '';
    let photographer = '';
    if (userLink) {
      const href = userLink.getAttribute('href') || '';
      const um = href.match(/^\/@([A-Za-z0-9_\-]+)/);
      photographer_username = um ? um[1] : '';
      photographer = (userLink.textContent || '').trim();
      if (!photographer) {
        // Profile avatar img has alt="Go to <Name>'s profile".
        const avatar = userLink.querySelector('img[alt]');
        if (avatar) {
          const alt = (avatar.getAttribute('alt') || '').trim();
          const nm = alt.match(/Go to (.*?)(?:'s profile)?$/i);
          if (nm) photographer = nm[1].trim();
        }
      }
    }

    // Title preference: photo alt text, else slug humanized, else URL.
    let title = alt_text;
    if (!title || /go to .*profile/i.test(title)) {
      title = slug.replace(/-/g, ' ').trim();
    }
    if (!title) title = url;

    out.push({
      photo_id, slug, url, image_url, alt_text,
      photographer, photographer_username,
      title,
    });
  }
  return {figures_seen: figs.length, rows: out};
}
"""


class UnsplashEngine(BaseEngine):
    """Unsplash photo search via the public ``/s/photos/<q>`` page."""

    name = "unsplash"
    max_retries = 2

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}

    # ------------------------------------------------------------------ search

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # Unsplash uses dashes in URL paths, but encodeURIComponent works too.
        slug = query.strip().replace(" ", "-")
        url = f"{UNSPLASH_SEARCH}/{urllib.parse.quote(slug, safe='-')}"
        log.info("[unsplash] navigating to %s", url)
        if not safe_goto(self.page, url, timeout=30000):
            return []

        # Let the masonry grid render. Scroll to trigger lazy loading.
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
            log.warning("[unsplash] parse JS failed: %s", e)
            data = {}

        figures_seen = int(data.get("figures_seen") or 0)
        rows = data.get("rows") or []

        self.last_status = {
            "url": getattr(self.page, "url", ""),
            "figures_seen": figures_seen,
            "count": len(rows),
        }

        results: list[SearchResult] = []
        for row in rows:
            url2 = (row.get("url") or "").strip()
            if not url2:
                continue
            title = (row.get("title") or "").strip()[:200]
            if not title:
                title = url2
            photographer = (row.get("photographer") or "").strip()
            photographer_username = (row.get("photographer_username") or "").strip()
            image_url = (row.get("image_url") or "").strip()
            alt_text = (row.get("alt_text") or "").strip()
            photo_id = (row.get("photo_id") or "").strip()
            slug = (row.get("slug") or "").strip()

            head = []
            if photographer:
                head.append(f"by {photographer}")
            elif photographer_username:
                head.append(f"@{photographer_username}")
            head_text = " · ".join(head)
            snippet_parts = []
            if head_text:
                snippet_parts.append(head_text)
            if alt_text and alt_text != title:
                snippet_parts.append(alt_text)
            snippet = " — ".join(snippet_parts)[:320]

            r = SearchResult(title=title, url=url2, snippet=snippet)
            r.photo_id = photo_id                            # type: ignore[attr-defined]
            r.slug = slug                                    # type: ignore[attr-defined]
            r.image_url = image_url                          # type: ignore[attr-defined]
            r.photographer = photographer                    # type: ignore[attr-defined]
            r.photographer_username = photographer_username  # type: ignore[attr-defined]
            r.alt_text = alt_text                            # type: ignore[attr-defined]
            results.append(r)
            if len(results) >= limit:
                break
        return results
