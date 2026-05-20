"""Goodreads book search adapter via the public ``/search`` page.

Strategy
--------
Visit ``https://www.goodreads.com/search?q=<query>`` and harvest each
``<tr itemtype="http://schema.org/Book">`` row. Goodreads exposes
schema.org microdata so the structure is very stable.

For every result we extract:

* ``goodreads_id`` — numeric book id from the URL slug
* ``title``       — book title (incl. series suffix like ``"(Dune, #1)"``)
* ``author``      — primary author display name
* ``image_url``   — cover thumbnail URL
* ``avg_rating``  — average rating (e.g. ``"4.29"``)
* ``rating_count`` — total rating count (e.g. ``"1,668,265"``)
"""

from __future__ import annotations

import logging
import re
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

GOODREADS_HOME = "https://www.goodreads.com/"
GOODREADS_SEARCH = "https://www.goodreads.com/search"

BOOK_ID_RE = re.compile(r"/book/show/(\d+)")
RATING_RE = re.compile(r"([\d.]+)\s*avg rating\s*[—-]\s*([\d,]+)\s*ratings?", re.I)


_PARSE_JS = r"""
(limit) => {
  const rows = Array.from(document.querySelectorAll('tr[itemtype="http://schema.org/Book"]'));
  const out = [];
  const seen = new Set();
  for (const tr of rows) {
    if (out.length >= limit) break;
    const titleA = tr.querySelector('a.bookTitle');
    if (!titleA) continue;
    const href = titleA.href.split('?')[0];
    const m = href.match(/\/book\/show\/(\d+)/);
    const goodreads_id = m ? m[1] : '';
    if (!goodreads_id || seen.has(goodreads_id)) continue;
    seen.add(goodreads_id);

    let title = (titleA.querySelector('span[itemprop="name"]') || titleA).textContent.trim();
    title = title.replace(/\s+/g, ' ');

    const authorA = tr.querySelector('a.authorName, span[itemprop="author"] a');
    const author = authorA ? authorA.textContent.trim() : '';

    const cover = tr.querySelector('img.bookCover, img');
    const image_url = cover ? (cover.src || '') : '';

    const ratingEl = tr.querySelector('span.minirating');
    const rating_text = ratingEl ? ratingEl.textContent.trim() : '';

    out.push({
      goodreads_id,
      url: `https://www.goodreads.com/book/show/${goodreads_id}`,
      title, author, image_url, rating_text,
    });
  }
  return {rows_seen: rows.length, rows: out};
}
"""


class GoodreadsEngine(BaseEngine):
    """Goodreads book search via the public /search page."""

    name = "goodreads"
    max_retries = 2

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # Light warm-up.
        if safe_goto(self.page, GOODREADS_HOME, timeout=20000, retries=1):
            human_delay(0.4, 1.0)

        q = urllib.parse.quote_plus(query)
        url = f"{GOODREADS_SEARCH}?q={q}"
        log.info("[goodreads] navigating to %s", url)
        if not safe_goto(self.page, url, timeout=30000):
            return []

        try:
            self.page.wait_for_selector(
                'tr[itemtype="http://schema.org/Book"]',
                timeout=10000,
            )
        except Exception:
            pass
        human_delay(0.6, 1.2)

        try:
            data = self.page.evaluate(_PARSE_JS, max(limit, 5)) or {}
        except Exception as e:
            log.warning("[goodreads] parse JS failed: %s", e)
            data = {}

        rows_seen = int(data.get("rows_seen") or 0)
        rows = data.get("rows") or []
        self.last_status = {
            "url": getattr(self.page, "url", ""),
            "rows_seen": rows_seen,
            "count": len(rows),
        }

        results: list[SearchResult] = []
        for row in rows:
            url2 = (row.get("url") or "").strip()
            gr_id = (row.get("goodreads_id") or "").strip()
            title = (row.get("title") or "").strip()
            if not url2 or not gr_id or not title:
                continue
            author = (row.get("author") or "").strip()
            image_url = (row.get("image_url") or "").strip()
            rating_text = (row.get("rating_text") or "").strip()

            avg_rating = ""
            rating_count = ""
            m = RATING_RE.search(rating_text)
            if m:
                avg_rating = m.group(1)
                rating_count = m.group(2)

            head = []
            if author:
                head.append(f"by {author}")
            if avg_rating:
                head.append(
                    f"⭐ {avg_rating}" + (f" ({rating_count})" if rating_count else "")
                )
            snippet = " · ".join(head)[:320]

            r = SearchResult(title=title[:200], url=url2, snippet=snippet)
            r.goodreads_id = gr_id          # type: ignore[attr-defined]
            r.author = author               # type: ignore[attr-defined]
            r.avg_rating = avg_rating       # type: ignore[attr-defined]
            r.rating_count = rating_count   # type: ignore[attr-defined]
            r.image_url = image_url         # type: ignore[attr-defined]
            results.append(r)
            if len(results) >= limit:
                break
        return results
