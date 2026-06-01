"""Thairath search adapter — Thailand's largest newspaper (~1948–present).

``thairath.co.th`` runs a path-based search at
``https://www.thairath.co.th/search/<query>`` that returns recent
articles across news / sport / money / foreign / society sections.
The page is server-rendered Thai-language HTML; titles include both
the headline and a leading section badge that we strip via the same
defensive off-host pattern used by ``coccoc.py``.
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
  const root = document.querySelector('main, #main, body');
  for (const a of root.querySelectorAll('a[href]')) {
    if (out.length >= limit) break;
    const href = a.href || '';
    // Only keep on-host article paths: /news/, /sport/, /money/, /video/,
    // /newspaper/. Drop /search/, /tag/, /author/, ad-network anchors.
    if (!/^https?:\/\/(www\.)?thairath\.co\.th\/(news|sport|money|foreign|society|video|newspaper|content|lifestyle|local|scoop|business|entertain|world|tech)\//.test(href)) continue;
    if (/\/search\//.test(href) || /\/tag\//.test(href)) continue;
    // Strip the #aWQ9... anchor fragments Thairath appends for
    // tracking — they bloat the URL but the canonical hit is the
    // path before '#'. Keep the original url though, the fragment
    // doesn't break extract.
    const clean = href.split('#')[0];
    if (seen.has(clean)) continue;
    const title = (a.innerText || '').replace(/\s+/g, ' ').trim();
    if (title.length < 10) continue;
    seen.add(clean);
    out.push({title, url: clean, snippet: ''});
  }
  return out;
}
"""


class ThairathEngine(BaseEngine):
    name = "thairath"
    max_retries = 2

    def _do_search(self, query, limit):
        q = urllib.parse.quote(query)
        url = f"https://www.thairath.co.th/search/{q}"
        if not safe_goto(self.page, url, timeout=25000):
            return []
        human_delay(1.0, 2.0)
        try:
            raw = self.page.evaluate(_PARSE_JS, limit) or []
        except Exception as e:
            log.debug("[thairath] parse failed: %s", e)
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
