"""Daum (다음 / search.daum.net) search adapter — Korea's #2 (~20%, Kakao).

Used as a complement to Naver for cross-source coverage of
Korean-language content. Daum's SERP DOM is fairly stable; we use a
straightforward result-list parser.
"""

from __future__ import annotations

import logging
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)


_PARSE_JS = r"""
(limit) => {
  // Daum's SERP class names are unstable; rely on the universal
  // pattern: every organic result is an anchor pointing off-site, in
  // the main results area, with non-trivial title text. We exclude
  // Kakao / Daum's own ecosystem so we don't surface header chrome.
  const out = [];
  const seen = new Set();
  const ownTlds = ['daum.net', 'kakao.com', 'kakaocdn.net', 'daumcdn.net'];
  const root = document.querySelector('#mArticle, #wrapSearch, main, body');
  for (const a of root.querySelectorAll('a[href^="http"]')) {
    if (out.length >= limit) break;
    const href = a.getAttribute('href') || '';
    if (!href) continue;
    let host;
    try { host = new URL(href, location.href).hostname; } catch (e) { continue; }
    if (ownTlds.some(t => host.endsWith(t))) continue;
    if (seen.has(href)) continue;
    const title = (a.innerText || '').trim();
    if (title.length < 8) continue;
    seen.add(href);
    let desc = '';
    let parent = a.parentElement;
    for (let i = 0; i < 5 && parent; i++) {
      const p = parent.querySelector('p, .desc_g, .f_eb, .desc_info');
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


class DaumEngine(BaseEngine):
    name = "daum"
    max_retries = 2

    def _do_search(self, query, limit):
        q = urllib.parse.quote(query)
        url = f"https://search.daum.net/search?w=tot&q={q}"
        if not safe_goto(self.page, url, timeout=25000):
            return []
        human_delay(1.0, 2.0)
        try:
            raw = self.page.evaluate(_PARSE_JS, limit) or []
        except Exception as e:
            log.debug("[daum] parse failed: %s", e)
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
