"""IMDB title (movie / TV) search adapter via the public ``/find`` page.

Strategy
--------
Visit the IMDB homepage first (so cookies are set), then navigate to::

    https://www.imdb.com/find/?q=<query>&s=tt

The result list is a React-hydrated React/SPA component:
``<ul>`` of ``li.ipc-metadata-list-summary-item`` cards. We wait for
``networkidle`` then harvest each card via ``page.evaluate``.

For every result we extract:

* ``imdb_id``     — ``tt`` id from the title link
* ``year``        — release year (or first year of a series)
* ``content_type`` — ``"movie"`` (default), ``"tv"``, ``"video"``, ``"short"``,
                    ``"video_game"``, ``"miniseries"`` (best-effort
                    classification from the metadata bullets)
* ``runtime``     — runtime string (``"2h 28m"`` / ``"44m"``)
* ``rating``      — content rating (``"PG-13"`` / ``"R"`` / etc., when present)
* ``imdb_rating`` — IMDB user rating (e.g. ``"8.8"``)
* ``vote_count``  — vote-count string (e.g. ``"2.8M"``)
* ``image_url``   — poster URL
"""

from __future__ import annotations

import logging
import re
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

IMDB_HOME = "https://www.imdb.com/"
IMDB_FIND = "https://www.imdb.com/find/"

TT_RE = re.compile(r"/title/(tt\d+)")


_PARSE_JS = r"""
(limit) => {
  const items = Array.from(document.querySelectorAll('li.ipc-metadata-list-summary-item'));
  const seen = new Set();
  const out = [];
  for (const li of items) {
    if (out.length >= limit) break;
    const a = li.querySelector('a.ipc-metadata-list-summary-item__t') ||
              li.querySelector('a[href*="/title/tt"]');
    if (!a) continue;
    const m = a.href.match(/\/title\/(tt\d+)/);
    if (!m) continue;
    const tt = m[1];
    if (seen.has(tt)) continue;
    seen.add(tt);

    const url = `https://www.imdb.com/title/${tt}/`;
    let title = (a.textContent || '').trim();
    if (!title) {
      // Fallback: parse out raw text "Title <Year>..."
      const raw = (li.textContent || '').trim();
      const m2 = raw.match(/^(.*?)(\d{4})/);
      if (m2) title = m2[1].trim();
    }

    // Metadata bullets — typically [year, runtime/episodes, rating-or-tag, type]
    const bulletEls = Array.from(li.querySelectorAll(
      'ul.ipc-metadata-list-summary-item__tl li, ' +
      'ul.ipc-inline-list li.ipc-inline-list__item, ' +
      'ul li'
    ));
    const bullets = bulletEls.map(b => (b.textContent || '').trim())
      .filter(t => t && t.length < 50);
    const dedupedBullets = [];
    const seenB = new Set();
    for (const b of bullets) {
      if (seenB.has(b)) continue;
      seenB.add(b);
      dedupedBullets.push(b);
    }

    // Poster URL.
    const img = li.querySelector('img');
    const poster = img ? (img.getAttribute('src') || '') : '';

    // Try to surface the IMDB rating + vote count from the raw text:
    //   "...8.8 (2.8M)Rate..."
    const raw = (li.textContent || '').trim();
    const ratingMatch = raw.match(/(\d\.\d)\s*\(([\d,.KMB]+)\)/);
    const imdb_rating = ratingMatch ? ratingMatch[1] : '';
    const vote_count  = ratingMatch ? ratingMatch[2] : '';

    out.push({
      imdb_id: tt, url, title,
      bullets: dedupedBullets,
      poster, imdb_rating, vote_count,
    });
  }
  return {items_seen: items.length, rows: out};
}
"""


class ImdbEngine(BaseEngine):
    """IMDB title search via the public /find page."""

    name = "imdb"
    max_retries = 2

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # Warm up on the homepage to set cookies.
        if safe_goto(self.page, IMDB_HOME, timeout=20000, retries=1):
            human_delay(0.4, 1.0)

        q = urllib.parse.quote_plus(query)
        url = f"{IMDB_FIND}?q={q}&s=tt"
        log.info("[imdb] navigating to %s", url)
        # Use a longer wait_until since the SPA hydrates async.
        try:
            self.page.goto(url, timeout=35000, wait_until="domcontentloaded")
        except Exception as e:
            log.warning("[imdb] goto failed: %s", e)
            return []

        # Wait for the result list to render.
        try:
            self.page.wait_for_selector(
                "li.ipc-metadata-list-summary-item",
                timeout=15000,
            )
        except Exception:
            pass
        human_delay(1.2, 2.4)

        try:
            data = self.page.evaluate(_PARSE_JS, max(limit, 5)) or {}
        except Exception as e:
            log.warning("[imdb] parse JS failed: %s", e)
            data = {}

        items_seen = int(data.get("items_seen") or 0)
        rows = data.get("rows") or []
        self.last_status = {
            "url": getattr(self.page, "url", ""),
            "items_seen": items_seen,
            "count": len(rows),
        }

        results: list[SearchResult] = []
        for row in rows:
            url2 = (row.get("url") or "").strip()
            tt = (row.get("imdb_id") or "").strip()
            title = (row.get("title") or "").strip()
            if not url2 or not tt:
                continue

            bullets = [b.strip() for b in (row.get("bullets") or []) if b.strip()]
            year, runtime, rating, content_type = self._parse_bullets(bullets)
            poster = (row.get("poster") or "").strip()
            imdb_rating = (row.get("imdb_rating") or "").strip()
            vote_count = (row.get("vote_count") or "").strip()

            head = []
            if year:
                head.append(year)
            if content_type:
                head.append(content_type)
            if runtime:
                head.append(runtime)
            if rating:
                head.append(rating)
            if imdb_rating:
                head.append(f"⭐ {imdb_rating}" + (f" ({vote_count})" if vote_count else ""))
            snippet = " · ".join(head)[:320]

            r = SearchResult(title=title or url2, url=url2, snippet=snippet)
            r.imdb_id = tt                # type: ignore[attr-defined]
            r.year = year                 # type: ignore[attr-defined]
            r.content_type = content_type # type: ignore[attr-defined]
            r.runtime = runtime           # type: ignore[attr-defined]
            r.rating = rating             # type: ignore[attr-defined]
            r.imdb_rating = imdb_rating   # type: ignore[attr-defined]
            r.vote_count = vote_count     # type: ignore[attr-defined]
            r.image_url = poster          # type: ignore[attr-defined]
            results.append(r)
            if len(results) >= limit:
                break
        return results

    @staticmethod
    def _parse_bullets(bullets: list[str]) -> tuple[str, str, str, str]:
        """Return (year, runtime, rating, content_type) parsed out of bullet list."""
        year = ""
        runtime = ""
        rating = ""
        content_type = "movie"  # default
        for b in bullets:
            if not year and re.fullmatch(r"\d{4}(?:[-–]\d{4})?", b):
                year = b
                continue
            if not runtime and re.fullmatch(r"\d+h(?:\s*\d+m)?|\d+m", b):
                runtime = b
                continue
            # Rating tags are short and ALL-CAPS-ish, but they can be
            # noisy ("Not Rated", "TV-14", "PG-13", "R", "Unrated"...).
            low = b.lower()
            if low in ("video", "tv movie", "tv special", "tv series",
                      "miniseries", "short", "video game", "podcast series",
                      "podcast episode", "tv mini series", "tv mini-series"):
                if low == "video":
                    content_type = "video"
                elif "mini" in low:
                    content_type = "miniseries"
                elif "series" in low:
                    content_type = "tv"
                elif "short" in low:
                    content_type = "short"
                elif "game" in low:
                    content_type = "video_game"
                elif "podcast" in low:
                    content_type = "podcast"
                elif "tv" in low:
                    content_type = "tv"
                continue
            # Episode counter for series.
            if re.fullmatch(r"\d+\s*eps?\.?", low):
                content_type = "tv"
                continue
            if not rating and re.fullmatch(
                r"PG(?:-13)?|G|R|NC-17|TV-(?:Y|Y7|G|PG|14|MA)|Not Rated|Unrated|Approved|Passed",
                b,
            ):
                rating = b
                continue
        return (year, runtime, rating, content_type)
