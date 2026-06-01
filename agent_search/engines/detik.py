"""Detik search adapter — Indonesia's largest news portal (~1998–present).

``detik.com`` runs an internal SERP at
``https://www.detik.com/search/searchall?query=<q>`` that returns
articles from every Detik subdomain (``news.detik.com``,
``travel.detik.com``, ``finance.detik.com``, ``sport.detik.com``,
``20.detik.com`` for video, …). The page hydrates client-side, so we
wait briefly after navigation. Each organic hit lives in
``article.list-content__item`` with a single title anchor and a
section badge — we keep ``list-content__item`` matches and skip the
auto-playing ``slider-snap__item`` carousel.
"""

from __future__ import annotations

import logging
import time
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)


_PARSE_JS = r"""
(limit) => {
  const out = [];
  const seen = new Set();
  // Primary container Detik uses on every search vertical.
  const cards = document.querySelectorAll(
    'article.list-content__item, .list-content__item'
  );
  // CSS selector commas return the first DOM-order match, NOT the first
  // selector that matches — so probe selectors individually, in order
  // of preference, so the clean `.media__title` headline beats the
  // section badge that's rendered as <h2>.
  const TITLE_SELECTORS = [
    '.media__title',
    '.list-content__item-title',
    '.title',
    'h3',
    'h2',
  ];
  for (const c of cards) {
    if (out.length >= limit) break;
    const a = c.querySelector('a[href^="http"]');
    if (!a) continue;
    const href = a.getAttribute('href') || '';
    if (!href || seen.has(href)) continue;
    let title = '';
    for (const sel of TITLE_SELECTORS) {
      const t = c.querySelector(sel);
      if (t && t.innerText && t.innerText.trim().length >= 8) {
        title = t.innerText.replace(/\s+/g, ' ').trim();
        break;
      }
    }
    if (!title) {
      title = (a.innerText || '').replace(/\s+/g, ' ').trim();
    }
    if (title.length < 8) continue;
    seen.add(href);
    let snippet = '';
    const sn = c.querySelector(
      '.media__desc, .desc, .description, .list-content__item-desc, p'
    );
    if (sn) snippet = (sn.innerText || '').trim();
    out.push({title, url: href, snippet});
  }
  return out;
}
"""


class DetikEngine(BaseEngine):
    name = "detik"
    max_retries = 2

    def _do_search(self, query, limit):
        q = urllib.parse.quote(query)
        url = f"https://www.detik.com/search/searchall?query={q}"
        if not safe_goto(self.page, url, timeout=25000):
            return []
        # SPA hydration — give it a few seconds to render the result list.
        time.sleep(3.5)
        human_delay(0.5, 1.5)
        try:
            raw = self.page.evaluate(_PARSE_JS, limit) or []
        except Exception as e:
            log.debug("[detik] parse failed: %s", e)
            raw = []
        out: list[SearchResult] = []
        for r in raw:
            t = (r.get("title") or "").strip()
            u = (r.get("url") or "").strip()
            if not t or not u:
                continue
            out.append(SearchResult(
                title=t, url=u, snippet=(r.get("snippet") or "")[:320],
            ))
            if len(out) >= limit:
                break
        return out
