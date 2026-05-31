"""Cốc Cốc search adapter — Vietnam's largest local engine (~50%).

Cốc Cốc bundles a Chromium browser with a built-in search engine and
holds an unusually large local market share for Vietnamese-language
content. The web SERP at ``coccoc.com/search`` is HTML-rendered.
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
  const root = document.querySelector('main, #search, #search-results, body');
  for (const a of root.querySelectorAll('a[href^="http"]')) {
    if (out.length >= limit) break;
    const href = a.getAttribute('href') || '';
    if (!href) continue;
    let host;
    try { host = new URL(href, location.href).hostname; } catch (e) { continue; }
    if (host.endsWith('coccoc.com')) continue;
    if (seen.has(href)) continue;
    const title = (a.innerText || '').trim();
    if (title.length < 8) continue;
    seen.add(href);
    let desc = '';
    let parent = a.parentElement;
    for (let i = 0; i < 5 && parent; i++) {
      const p = parent.querySelector('.snippet, .description, p');
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


class CocCocEngine(BaseEngine):
    name = "coccoc"
    max_retries = 2

    def _do_search(self, query, limit):
        q = urllib.parse.quote(query)
        url = f"https://coccoc.com/search?query={q}"
        if not safe_goto(self.page, url, timeout=25000):
            return []
        human_delay(1.0, 2.0)
        try:
            raw = self.page.evaluate(_PARSE_JS, limit) or []
        except Exception as e:
            log.debug("[coccoc] parse failed: %s", e)
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
