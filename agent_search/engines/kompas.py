"""Kompas search adapter — Indonesia's largest daily newspaper (~1965).

``search.kompas.com/search/?q=<q>`` returns articles across every
Kompas Gramedia subdomain — ``megapolitan.kompas.com``,
``otomotif.kompas.com``, ``tekno.kompas.com``, ``regional.kompas.com``,
etc. The SERP is server-rendered so a brief settle is enough; we use
the same defensive off-host pattern as ``coccoc.py``, restricted to
``*.kompas.com`` article paths.
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
  for (const a of root.querySelectorAll('a[href^="http"]')) {
    if (out.length >= limit) break;
    const href = a.getAttribute('href') || '';
    if (!href) continue;
    let host;
    try { host = new URL(href, location.href).hostname; } catch (e) { continue; }
    // Keep only Kompas-network article URLs (sub-domain.kompas.com/read/…).
    if (!host.endsWith('kompas.com')) continue;
    if (host === 'search.kompas.com' || host === 'plus.kompas.com') continue;
    if (!/\/read\//.test(href)) continue;
    if (seen.has(href)) continue;
    const title = (a.innerText || '').replace(/\s+/g, ' ').trim();
    if (title.length < 10) continue;
    seen.add(href);
    let desc = '';
    let parent = a.parentElement;
    for (let i = 0; i < 5 && parent; i++) {
      const p = parent.querySelector(
        '.article__subtitle, .article__lead, .desc, p'
      );
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


class KompasEngine(BaseEngine):
    name = "kompas"
    max_retries = 2

    def _do_search(self, query, limit):
        q = urllib.parse.quote(query)
        url = f"https://search.kompas.com/search/?q={q}"
        if not safe_goto(self.page, url, timeout=25000):
            return []
        human_delay(1.0, 2.0)
        try:
            raw = self.page.evaluate(_PARSE_JS, limit) or []
        except Exception as e:
            log.debug("[kompas] parse failed: %s", e)
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
