"""BBC News search adapter (UK general / world news).

Direct path: https://www.bbc.co.uk/search?q=<query>
Fallback chain: Google site:bbc.co.uk → Bing → DuckDuckGo
"""

from __future__ import annotations

import re
from ._news_base import NewsBaseEngine


_PARSE_JS = r"""
(limit) => {
  // BBC's search SPA renders <article> cards or <li role="listitem">.
  const sels = [
    'div[type="search"]',
    'li[role="listitem"]',
    'article',
    'div[data-testid*="result" i]',
  ];
  let cards = [];
  for (const s of sels) {
    cards = Array.from(document.querySelectorAll(s));
    if (cards.length) break;
  }
  const out = [];
  const seen = new Set();
  for (const card of cards) {
    if (out.length >= limit) break;
    const a = card.querySelector('a[href*="/news/"], a[href*="/sport/"]');
    if (!a) continue;
    const url = a.href;
    if (seen.has(url)) continue;
    seen.add(url);
    const titleEl = card.querySelector('h2, h3, p[role="text"]');
    const title = (titleEl ? titleEl.textContent : a.textContent).trim();
    const snipEl = card.querySelector('p:not([role="text"])');
    const snippet = snipEl ? snipEl.textContent.trim() : '';
    const dateEl = card.querySelector('time, [data-testid*="date"]');
    const published = dateEl ? dateEl.textContent.trim() : '';
    out.push({title, url, snippet, published});
  }
  return out;
}
"""


class BBCEngine(NewsBaseEngine):
    name = "bbc"
    HOME_URL = "https://www.bbc.co.uk/"
    SEARCH_URL = "https://www.bbc.co.uk/search?q={query}"
    HOST_RE = re.compile(
        r"https?://(?:www\.)?bbc\.(?:co\.uk|com)/(?:news|sport|culture|future|"
        r"travel|worklife|reel)/", re.IGNORECASE,
    )
    SITE_TITLE_SUFFIXES = (" - BBC News", " - BBC", " | BBC News", " | BBC")

    def _parse_direct(self, limit: int) -> list[dict]:
        try:
            return self.page.evaluate(_PARSE_JS, max(limit, 5)) or []
        except Exception:
            return []
