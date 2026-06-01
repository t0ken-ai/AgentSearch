"""allAfrica search adapter — pan-African news aggregator (50+ countries).

``allafrica.com`` aggregates news from ~140 African outlets in
English, French, Arabic, and Portuguese. Its search at
``https://allafrica.com/search/?search_string=<q>`` returns story IDs
under ``/stories/<numeric>.html``. The SERP is fully server-rendered
HTML so no extra wait is needed.

This is the broadest "Africa search" we have: a single query covers
Nigeria, Kenya, South Africa, Ghana, Ethiopia, Morocco, Egypt, …
without picking one country's outlet.
"""

from __future__ import annotations

import logging
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)


_PARSE_JS = r"""
(limit) => {
  const out = [];
  const seen = new Set();
  const root = document.querySelector('main, #content, body');
  for (const a of root.querySelectorAll('a[href]')) {
    if (out.length >= limit) break;
    const href = a.href || '';
    // Match canonical story URL: allafrica.com/stories/<id>.html.
    if (!/^https?:\/\/(?:[a-z]+\.)?allafrica\.com\/stories\/\d+\.html/.test(href)) continue;
    if (seen.has(href)) continue;
    const title = (a.innerText || '').replace(/\s+/g, ' ').trim();
    if (title.length < 12) continue;
    seen.add(href);
    let desc = '';
    let parent = a.parentElement;
    for (let i = 0; i < 4 && parent; i++) {
      const p = parent.querySelector('p, .summary, .desc, .description');
      if (p && p !== a && p.innerText && p.innerText.length > 20) {
        desc = p.innerText.trim();
        break;
      }
      parent = parent.parentElement;
    }
    out.push({title, url: href, snippet: desc});
  }
  return out;
}
"""


class AllAfricaEngine(BaseEngine):
    name = "allafrica"
    max_retries = 2

    def _do_search(self, query, limit):
        q = urllib.parse.quote(query)
        url = f"https://allafrica.com/search/?search_string={q}"
        # allAfrica's search backend can be slow to respond under
        # stealth UAs (~30-40 s on a cold cache), so we give it a
        # generous timeout. ``safe_goto`` already retries internally.
        if not safe_goto(self.page, url, timeout=50000):
            return []
        human_delay(1.5, 2.5)
        try:
            raw = self.page.evaluate(_PARSE_JS, limit) or []
        except Exception as e:
            log.debug("[allafrica] parse failed: %s", e)
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
