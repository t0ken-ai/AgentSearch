"""Reuters search adapter (international news agency).

Direct path: https://www.reuters.com/site-search/?query=<query>
Reuters' search SPA aggressively gates content; we rely heavily on the
Google/Bing/DuckDuckGo `site:` fallback.
"""

from __future__ import annotations

import re
from ._news_base import NewsBaseEngine


_PARSE_JS = r"""
(limit) => {
  const sels = [
    'li.search-results__item',
    'li[class*="search-results__item"]',
    'article',
    'a[data-testid*="Title" i]',
  ];
  let cards = [];
  for (const s of sels) {
    cards = Array.from(document.querySelectorAll(s));
    if (cards.length >= 3) break;
  }
  const out = [];
  const seen = new Set();
  for (const c of cards) {
    if (out.length >= limit) break;
    const a = c.matches && c.matches('a[href]') ? c : c.querySelector('a[href*="reuters.com"]');
    if (!a) continue;
    let url = a.href;
    if (url.startsWith('/')) url = 'https://www.reuters.com' + url;
    if (!/reuters\.com\/.+\//.test(url)) continue;
    if (seen.has(url)) continue;
    seen.add(url);
    const titleEl = c.querySelector('[data-testid*="Heading" i], h3, h4') || a;
    const title = (titleEl.textContent || '').trim();
    const dateEl = c.querySelector('time, [data-testid*="date" i]');
    const published = dateEl ? dateEl.textContent.trim() : '';
    out.push({title, url, published});
  }
  return out;
}
"""


class ReutersEngine(NewsBaseEngine):
    name = "reuters"
    HOME_URL = "https://www.reuters.com/"
    SEARCH_URL = "https://www.reuters.com/site-search/?query={query}"
    HOST_RE = re.compile(
        r"https?://(?:www\.)?reuters\.com/(?:[a-z\-]+/)+", re.IGNORECASE,
    )
    SITE_TITLE_SUFFIXES = (" | Reuters", " - Reuters", " — Reuters")

    def _parse_direct(self, limit: int) -> list[dict]:
        try:
            return self.page.evaluate(_PARSE_JS, max(limit, 5)) or []
        except Exception:
            return []
