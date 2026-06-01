"""Mail & Guardian search adapter — South Africa's leading independent paper.

``mg.co.za`` runs a standard WordPress-style search at ``mg.co.za/?s=<q>``
that returns recent articles across Politics / Business / Africa /
Friday / Opinion. The SERP is server-rendered, dated article URLs
follow ``/<section>/<YYYY-MM-DD>-<slug>/``.

Pairs with ``allafrica`` for an Africa duo: allAfrica gives breadth
(50+ countries), M&G gives depth on South African politics/society.
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
  const root = document.querySelector('main, #main, .site-main, body');
  for (const a of root.querySelectorAll('a[href^="https://mg.co.za/"]')) {
    if (out.length >= limit) break;
    const href = a.href || '';
    // Article URLs always include a YYYY-MM-DD date segment in the
    // path. Drop section / tag / utility links that don't.
    if (!/\/\d{4}-\d{2}-\d{2}-/.test(href)) continue;
    if (seen.has(href)) continue;
    const title = (a.innerText || '').replace(/\s+/g, ' ').trim();
    if (title.length < 12) continue;
    seen.add(href);
    let desc = '';
    let parent = a.parentElement;
    for (let i = 0; i < 5 && parent; i++) {
      const p = parent.querySelector('p, .excerpt, .entry-summary, .desc');
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


class MailGuardianEngine(BaseEngine):
    name = "mail_guardian"
    max_retries = 2

    def _do_search(self, query, limit):
        q = urllib.parse.quote(query)
        url = f"https://mg.co.za/?s={q}"
        if not safe_goto(self.page, url, timeout=25000):
            return []
        human_delay(1.0, 2.0)
        try:
            raw = self.page.evaluate(_PARSE_JS, limit) or []
        except Exception as e:
            log.debug("[mail_guardian] parse failed: %s", e)
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
