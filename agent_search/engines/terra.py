"""Terra (terra.com.br) search adapter — large Brazilian web portal.

``terra.com.br/busca?q=<q>`` is wired to a Google Custom Search Engine
(class prefix ``.gs-webResult``). It scopes to Terra-network properties
plus partner outlets, so it's a useful Brazilian SERP that complements
``g1`` (Globo) — Terra has stronger entertainment / lifestyle / sport
coverage where Globo has stronger hard news.

The SERP loads in two phases: first the GCSE iframe shell, then the
result list. We wait ~5 s after navigation, then read each
``.gs-webResult.gs-result`` card. Titles in this layout come bundled
with a host suffix ("Headline...www.terra.com.br") which we strip.
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
  // GCSE result wrappers — `.gs-webResult.gs-result` is the primary
  // organic card; we exclude the wrapper-only `.gsc-webResult` to
  // avoid double-counting the same hit.
  const cards = document.querySelectorAll('.gs-webResult.gs-result');
  for (const c of cards) {
    if (out.length >= limit) break;
    // Probe selectors individually (a CSS comma selector returns the
    // FIRST DOM-order match, not the first selector that matches —
    // so e.g. `a.gs-title, a[href]` would pick the wrong anchor when
    // a wrapper anchor appears earlier in the DOM than `.gs-title`).
    let a = c.querySelector('a.gs-title');
    if (!a) a = c.querySelector('a[href]');
    if (!a) continue;
    const href = a.href || '';
    if (!href || seen.has(href)) continue;
    if (!/^https?:\/\//.test(href)) continue;
    // Use textContent to dodge headless innerText quirks where the
    // anchor renders empty until styles are applied.
    let title = (a.textContent || a.innerText || '').replace(/\s+/g, ' ').trim();
    // Strip a trailing "...www.host.tld" segment GCSE sometimes glues on.
    title = title.replace(/\s*\.\.\.\s*(?:www\.)?[a-z0-9-]+\.[a-z.]+\s*$/i, '');
    title = title.replace(/\.\.\.$/, '').trim();
    if (title.length < 10) continue;
    seen.add(href);
    let snippet = '';
    const sn = c.querySelector('.gs-snippet, .gs-bidi-start-align');
    if (sn) snippet = (sn.textContent || sn.innerText || '').replace(/\s+/g, ' ').trim();
    out.push({title, url: href, snippet});
  }
  return out;
}
"""


class TerraEngine(BaseEngine):
    name = "terra"
    max_retries = 2

    def _do_search(self, query, limit):
        q = urllib.parse.quote(query)
        url = f"https://www.terra.com.br/busca?q={q}"
        if not safe_goto(self.page, url, timeout=30000):
            return []
        # GCSE iframe load + render takes ~4-5 s consistently.
        time.sleep(5.0)
        human_delay(0.5, 1.5)
        try:
            raw = self.page.evaluate(_PARSE_JS, limit) or []
        except Exception as e:
            log.debug("[terra] parse failed: %s", e)
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
