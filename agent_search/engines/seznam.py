"""Seznam.cz search adapter — Czech Republic's largest local engine (~30%).

Seznam is one of the few European markets where a non-Google search
engine still has meaningful share. The SERP at ``search.seznam.cz``
exposes a clean result list that's easy to scrape.
"""

from __future__ import annotations

import logging
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)


_PARSE_JS = r"""
(limit) => {
  // Seznam uses obfuscated CSS-in-JS class names that change every
  // build (e.g. .Zb3aae93df9). Skip class-based selectors entirely —
  // the SERP layout pairs a title anchor with a URL-display anchor for
  // every organic hit. We pull the title anchor by looking for any
  // anchor whose href is on a different host AND whose visible text
  // is non-trivial.
  const own = location.hostname;
  const out = [];
  const seen = new Set();
  // Walk every anchor in the main results area. Heuristically the
  // results live under the first <main> or under [role="main"].
  const root = document.querySelector('[role="main"], main') || document.body;
  for (const a of root.querySelectorAll('a[href^="http"]')) {
    if (out.length >= limit) break;
    const href = a.getAttribute('href') || '';
    if (!href) continue;
    let host;
    try { host = new URL(href, location.href).hostname; } catch (e) { continue; }
    if (host === own) continue;
    if (host.endsWith('.seznam.cz') || host.endsWith('.szn.cz')) continue;
    if (seen.has(href)) continue;
    const title = (a.innerText || '').trim();
    // Real titles are >=10 chars; URL-display anchors (the second of
    // each pair) just contain a short host text — drop them.
    if (title.length < 10) continue;
    // Walk up to a reasonable card and grab the description.
    let desc = '';
    let parent = a.parentElement;
    for (let i = 0; i < 5 && parent; i++) {
      const p = parent.querySelector('p');
      if (p && p !== a && p.innerText && p.innerText.length > 20) {
        desc = p.innerText.trim();
        break;
      }
      parent = parent.parentElement;
    }
    seen.add(href);
    out.push({title, url: href, snippet: desc});
  }
  return out;
}
"""


class SeznamEngine(BaseEngine):
    name = "seznam"
    max_retries = 2

    def _do_search(self, query, limit):
        q = urllib.parse.quote(query)
        url = f"https://search.seznam.cz/?q={q}"
        if not safe_goto(self.page, url, timeout=25000):
            return []
        human_delay(1.0, 2.0)
        try:
            raw = self.page.evaluate(_PARSE_JS, limit) or []
        except Exception as e:
            log.debug("[seznam] parse failed: %s", e)
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
