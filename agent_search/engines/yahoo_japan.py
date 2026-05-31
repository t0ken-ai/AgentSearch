"""Yahoo! JAPAN (search.yahoo.co.jp) search adapter — ~25% Japan share.

Yahoo! JAPAN is a separate property from US Yahoo (which is now a
Bing front-end). Backed by Bing under the hood for general web hits
since 2010, but the SERP rendering and ranking are distinct, so it's
worth a dedicated adapter for Japanese-language queries.
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
  // Each result lives in .Algo or .sw-CardBase
  const cards = document.querySelectorAll(
    '.Algo, .sw-CardBase, div.algo, .Web__Search__Result'
  );
  for (const c of cards) {
    if (out.length >= limit) break;
    const a = c.querySelector('a.sw-Card__title, a.Algo__titleAnchor, h3 a, a[href^="http"]');
    if (!a) continue;
    const href = a.getAttribute('href') || '';
    const title = (a.innerText || '').trim();
    if (!href || !title) continue;
    if (seen.has(href)) continue;
    seen.add(href);
    let snippet = '';
    const sn = c.querySelector(
      '.sw-Card__summary, .Algo__abstract, .compTitle ~ div, p'
    );
    if (sn) snippet = (sn.innerText || '').trim();
    out.push({title, url: href, snippet});
  }
  return out;
}
"""


class YahooJapanEngine(BaseEngine):
    name = "yahoo_japan"
    max_retries = 2

    def _do_search(self, query, limit):
        q = urllib.parse.quote(query)
        url = f"https://search.yahoo.co.jp/search?p={q}&fr=top_ga1_sa"
        if not safe_goto(self.page, url, timeout=25000):
            return []
        human_delay(1.0, 2.0)
        try:
            raw = self.page.evaluate(_PARSE_JS, limit) or []
        except Exception as e:
            log.debug("[yahoo_japan] parse failed: %s", e)
            raw = []
        out: list[SearchResult] = []
        for r in raw:
            t = (r.get("title") or "").strip()
            u = (r.get("url") or "").strip()
            if not t or not u:
                continue
            sn = (r.get("snippet") or "").strip()[:320]
            out.append(SearchResult(title=t, url=u, snippet=sn))
            if len(out) >= limit:
                break
        return out
