"""Al Jazeera English search adapter.

Direct path: https://www.aljazeera.com/search/<query>
"""

from __future__ import annotations

import re
from ._news_base import NewsBaseEngine


_PARSE_JS = r"""
(limit) => {
  const cards = Array.from(document.querySelectorAll(
    'article, .gc, [class*="gc__"], li.search-result__list'
  ));
  const out = [];
  const seen = new Set();
  for (const c of cards) {
    if (out.length >= limit) break;
    const a = c.querySelector('a[href*="aljazeera.com/"]');
    if (!a) continue;
    let url = a.href.split('?')[0];
    if (url.startsWith('/')) url = 'https://www.aljazeera.com' + url;
    if (!/aljazeera\.com\/(news|features|opinions|sports|videos|programs|economy|culture|investigations|gallery)\//.test(url)) continue;
    if (seen.has(url)) continue;
    seen.add(url);
    const titleEl = c.querySelector('.gc__title, h3, h2, [class*="title" i]') || a;
    const title = (titleEl.textContent || '').trim();
    const dateEl = c.querySelector('.gc__date, time, [class*="date" i]');
    const published = dateEl ? dateEl.textContent.trim() : '';
    const sectionEl = c.querySelector('.gc__category, [class*="category" i], [class*="kicker" i]');
    const section = sectionEl ? sectionEl.textContent.trim() : '';
    const snipEl = c.querySelector('.gc__excerpt, p, [class*="excerpt" i]');
    const snippet = snipEl ? snipEl.textContent.trim() : '';
    out.push({title, url, snippet, section, published});
  }
  return out;
}
"""


class AlJazeeraEngine(NewsBaseEngine):
    name = "aljazeera"
    HOME_URL = "https://www.aljazeera.com/"
    SEARCH_URL = "https://www.aljazeera.com/search/{query}"
    HOST_RE = re.compile(
        r"https?://(?:www\.)?aljazeera\.com/"
        r"(?:news|features|opinions|sports|videos|programs|economy|"
        r"culture|investigations|gallery)/",
        re.IGNORECASE,
    )
    SITE_TITLE_SUFFIXES = (
        " | Al Jazeera", " - Al Jazeera",
        " | News | Al Jazeera",
    )

    def _parse_direct(self, limit: int) -> list[dict]:
        try:
            return self.page.evaluate(_PARSE_JS, max(limit, 5)) or []
        except Exception:
            return []
