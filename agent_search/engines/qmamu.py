"""Qmamu search adapter — India's homegrown privacy-first web search.

Qmamu (qmamu.com) is the most credible 2026 attempt at an Indian-built
general web search engine — it crawls and ranks its own index rather
than wrapping Google/Bing, and ships verticals for Web / Images / News
/ Maps. The SERP is a JS-rendered SPA, so we wait a few seconds after
``safe_goto`` before reading anchors. We then apply the defensive
off-host-anchor pattern used by ``coccoc.py`` / ``mail_ru.py`` because
result class names are obfuscated and change between builds.
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
  const root = document.querySelector('main, #results, #search, body');
  for (const a of root.querySelectorAll('a[href^="http"]')) {
    if (out.length >= limit) break;
    const href = a.getAttribute('href') || '';
    if (!href) continue;
    let host;
    try { host = new URL(href, location.href).hostname; } catch (e) { continue; }
    // Drop Qmamu's own header/footer/vertical-tab anchors.
    if (host.endsWith('qmamu.com')) continue;
    if (seen.has(href)) continue;
    const title = (a.innerText || '').trim();
    if (title.length < 8) continue;
    seen.add(href);
    let desc = '';
    let parent = a.parentElement;
    for (let i = 0; i < 6 && parent; i++) {
      const p = parent.querySelector('p, .desc, .snippet, .description');
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


class QmamuEngine(BaseEngine):
    name = "qmamu"
    max_retries = 2

    def _do_search(self, query, limit):
        q = urllib.parse.quote(query)
        url = f"https://qmamu.com/search?q={q}"
        if not safe_goto(self.page, url, timeout=25000):
            return []
        # SPA: results are fetched + rendered after navigation. Wait
        # for them to settle. ~4s is enough on a typical residential
        # link; we still bail early via the limit check.
        time.sleep(4.0)
        human_delay(0.5, 1.5)
        try:
            raw = self.page.evaluate(_PARSE_JS, limit) or []
        except Exception as e:
            log.debug("[qmamu] parse failed: %s", e)
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
