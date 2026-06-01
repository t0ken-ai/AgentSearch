"""G1 (Globo) search adapter — Brazil's largest news network.

``g1.globo.com/busca/?q=<q>`` returns articles from across the entire
Globo network (g1.globo.com, sao paulo / rio / regional editions,
``valor.globo.com``, ``oglobo.globo.com``, ``cbn.globoradio.globo.com``,
``ge.globo.com`` for sports, …). The SERP is server-rendered after a
brief client hydration — we wait ~5 s for the result widget cards.

Each organic hit lives in ``.widget--card.widget--info``; the
container's ``innerText`` looks like ``"G1\\nHeadline goes here\\n\\n."``
so we split on newlines, drop the leading section badge ("G1", "Globo
Esporte", …) and use the next non-empty line as the title.
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
  // Globo's search SERP renders each organic hit as a "widget--info"
  // card inside the .results container.
  const cards = document.querySelectorAll(
    '.widget--card.widget--info, .widget--info, .search-result .widget--card'
  );
  for (const c of cards) {
    if (out.length >= limit) break;
    const a = c.querySelector('a[href]');
    if (!a) continue;
    const href = a.href || '';
    if (!href || seen.has(href)) continue;
    // Drop sub-section / vertical aggregations (no /noticia/ slug).
    if (!/globo\.com\//.test(href)) continue;
    // Title strategy: the first non-empty line of innerText is the
    // section badge ("G1", "Globo Esporte", …); the second is the
    // headline. Fall back to the anchor text.
    const lines = (c.innerText || '').split('\n').map(s => s.trim()).filter(Boolean);
    let title = '';
    if (lines.length >= 2) title = lines[1];
    else if (lines.length === 1) title = lines[0];
    if (!title || title.length < 8) {
      title = (a.innerText || '').trim();
    }
    title = title.replace(/\s+/g, ' ').trim();
    if (title.length < 8) continue;
    seen.add(href);
    let snippet = '';
    if (lines.length >= 3) snippet = lines.slice(2).join(' ').trim();
    out.push({title, url: href, snippet});
  }
  return out;
}
"""


class G1Engine(BaseEngine):
    name = "g1"
    max_retries = 2

    def _do_search(self, query, limit):
        q = urllib.parse.quote(query)
        url = f"https://g1.globo.com/busca/?q={q}"
        if not safe_goto(self.page, url, timeout=30000):
            return []
        # Globo's SERP hydrates client-side after the initial HTML.
        time.sleep(4.5)
        human_delay(0.5, 1.5)
        try:
            raw = self.page.evaluate(_PARSE_JS, limit) or []
        except Exception as e:
            log.debug("[g1] parse failed: %s", e)
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
