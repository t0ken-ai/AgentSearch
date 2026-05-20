"""SoundCloud track search adapter via the public ``/search/sounds`` page.

Strategy
--------
Visit ``https://soundcloud.com/search/sounds?q=<query>``. The result list
is server-rendered into ``<li class="searchList__item">`` cards (the
"sounds" tab restricts to tracks rather than mixing in users / playlists).

For every result we extract:

* ``track_url``    — canonical track URL ``/<user_handle>/<track_slug>``
* ``user``         — uploader display name
* ``user_handle``  — uploader URL handle
* ``user_url``     — uploader profile URL
* ``plays``        — formatted play count (raw text from ``.sc-ministats``)
* ``comments``     — formatted comment count (when present)
* ``posted``       — relative posted-time string
* ``genre``        — genre tag (only present for some tracks)
"""

from __future__ import annotations

import logging
import re
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

SC_HOME = "https://soundcloud.com/"
SC_SEARCH = "https://soundcloud.com/search/sounds"

PLAYS_RE = re.compile(r"([\d,.KMB]+)\s*plays?", re.I)
COMMENTS_RE = re.compile(r"View all comments\s*([\d,.KMB]+)", re.I)
POSTED_RE = re.compile(r"Posted\s+(.+?)(?=\s{2,}|$)", re.I)


_PARSE_JS = r"""
(limit) => {
  const lis = Array.from(document.querySelectorAll('li.searchList__item'));
  const out = [];
  const seen = new Set();
  for (const li of lis) {
    if (out.length >= limit) break;
    const titleA = li.querySelector('a.soundTitle__title');
    if (!titleA) continue;          // Skip non-track cards.
    const url = titleA.href.split('?')[0];
    const m = url.match(/^https:\/\/soundcloud\.com\/([^\/]+)\/([^\/]+)$/);
    if (!m) continue;
    const user_handle = m[1];
    const track_slug = m[2];
    const key = `${user_handle}/${track_slug}`;
    if (seen.has(key)) continue;
    seen.add(key);

    const userA = li.querySelector('a.soundTitle__username');
    const user = userA ? userA.textContent.trim() : '';
    const user_url = userA ? userA.href.split('?')[0] : '';

    const stats = Array.from(li.querySelectorAll('.sc-ministats-item'))
      .map(s => s.textContent.replace(/\s+/g, ' ').trim());
    const playsItem = stats.find(s => /play/i.test(s)) || '';
    const commentsItem = stats.find(s => /comment/i.test(s)) || '';

    const timeEl = li.querySelector('.relativeTime, time');
    const posted = timeEl ? timeEl.textContent.replace(/\s+/g, ' ').trim() : '';

    // Genre tag (when present) usually right after the title block.
    const genreEl = li.querySelector('.sc-genre, [class*="genre" i]');
    const genre = genreEl ? genreEl.textContent.trim() : '';

    // The full raw text gives us a fallback if stats didn't match.
    const raw = (li.textContent || '').replace(/\s+/g, ' ').trim();

    out.push({
      title: titleA.textContent.trim(),
      url, user, user_handle, user_url,
      plays_text: playsItem, comments_text: commentsItem,
      posted, genre, raw_text: raw.slice(0, 320),
    });
  }
  return {lis_seen: lis.length, rows: out};
}
"""


class SoundCloudEngine(BaseEngine):
    """SoundCloud track search via the public /search/sounds page."""

    name = "soundcloud"
    max_retries = 2

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        if safe_goto(self.page, SC_HOME, timeout=20000, retries=1):
            human_delay(0.4, 1.0)

        q = urllib.parse.quote_plus(query)
        url = f"{SC_SEARCH}?q={q}"
        log.info("[soundcloud] navigating to %s", url)
        if not safe_goto(self.page, url, timeout=30000):
            return []

        try:
            self.page.wait_for_selector("li.searchList__item", timeout=12000)
        except Exception:
            pass
        # Trigger lazy load.
        for _ in range(2):
            human_delay(0.8, 1.2)
            try:
                self.page.evaluate("(y) => window.scrollBy(0, y)", 500)
            except Exception:
                pass

        try:
            data = self.page.evaluate(_PARSE_JS, max(limit, 5)) or {}
        except Exception as e:
            log.warning("[soundcloud] parse JS failed: %s", e)
            data = {}

        lis_seen = int(data.get("lis_seen") or 0)
        rows = data.get("rows") or []
        self.last_status = {
            "url": getattr(self.page, "url", ""),
            "lis_seen": lis_seen,
            "count": len(rows),
        }

        results: list[SearchResult] = []
        for row in rows:
            url2 = (row.get("url") or "").strip()
            title = (row.get("title") or "").strip()
            if not url2 or not title:
                continue
            user = (row.get("user") or "").strip()
            user_handle = (row.get("user_handle") or "").strip()
            user_url = (row.get("user_url") or "").strip()
            plays_text = (row.get("plays_text") or "").strip()
            comments_text = (row.get("comments_text") or "").strip()
            posted = (row.get("posted") or "").strip()
            genre = (row.get("genre") or "").strip()
            raw_text = (row.get("raw_text") or "").strip()

            plays = ""
            mp = PLAYS_RE.search(plays_text or raw_text)
            if mp:
                plays = mp.group(1)
            comments = ""
            mc = COMMENTS_RE.search(comments_text or raw_text)
            if mc:
                comments = mc.group(1)
            if posted:
                # Collapse "Posted 4 years ago4 years ago" → "4 years ago".
                pm = re.match(r"Posted\s+(\d+\s+\S+\s+ago)", posted)
                if pm:
                    posted = pm.group(1)

            head = []
            if user:
                head.append(f"by {user}")
            if plays:
                head.append(f"▶ {plays}")
            if comments:
                head.append(f"💬 {comments}")
            if posted:
                head.append(posted)
            if genre:
                head.append(genre)
            snippet = " · ".join(head)[:320]

            r = SearchResult(title=title[:200], url=url2, snippet=snippet)
            r.user = user                # type: ignore[attr-defined]
            r.user_handle = user_handle  # type: ignore[attr-defined]
            r.user_url = user_url        # type: ignore[attr-defined]
            r.plays = plays              # type: ignore[attr-defined]
            r.comments = comments        # type: ignore[attr-defined]
            r.posted = posted            # type: ignore[attr-defined]
            r.genre = genre              # type: ignore[attr-defined]
            results.append(r)
            if len(results) >= limit:
                break
        return results
