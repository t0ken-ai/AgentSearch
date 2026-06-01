"""Pantip Smart Search adapter — Thailand's #1 forum (often called Thai Reddit).

Pantip (pantip.com) is the dominant Thai-language discussion site since
1996. The dedicated SERP at ``search.pantip.com/ss?q=…`` returns ~15
thread / blog / member hits per page, server-rendered, no auth needed.
Result links are wrapped through a ``search.pantip.com/sr?r=…`` redirect
that resolves to the actual ``pantip.com/topic/…`` topic — the redirect
hop happens in the browser at click time, so we keep the redirect URL
as-is (still produces a valid extractable target).

This is the closest equivalent of the existing ``reddit_subreddit``
engine for Thai-speaking users: it's a forum search, not a generic
web search. Use it when a user wants Thai community discussion around
a topic.
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
  const root = document.querySelector('main, body');
  // Pantip's SERP uses RELATIVE hrefs ("/sr?r=…"), so a [href^="http"]
  // attribute filter would miss everything. Read the resolved a.href
  // instead and regex-match the absolute URL.
  for (const a of root.querySelectorAll('a[href]')) {
    if (out.length >= limit) break;
    const href = a.href || '';
    if (!/^https?:\/\/(search\.pantip\.com\/sr\?|(?:www\.)?pantip\.com\/topic\/)/.test(href)) continue;
    if (seen.has(href)) continue;
    const title = (a.innerText || '').replace(/\s+/g, ' ').trim();
    if (title.length < 6) continue;
    seen.add(href);
    let desc = '';
    let parent = a.parentElement;
    for (let i = 0; i < 4 && parent; i++) {
      const p = parent.querySelector('p, .description, .text, .desc, .preview');
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


class PantipEngine(BaseEngine):
    name = "pantip"
    max_retries = 2

    def _do_search(self, query, limit):
        q = urllib.parse.quote(query)
        url = f"https://search.pantip.com/ss?q={q}"
        if not safe_goto(self.page, url, timeout=25000):
            return []
        human_delay(1.0, 2.0)
        try:
            raw = self.page.evaluate(_PARSE_JS, limit) or []
        except Exception as e:
            log.debug("[pantip] parse failed: %s", e)
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
