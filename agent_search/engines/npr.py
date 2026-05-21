"""NPR (US public radio) search adapter.

Direct path: https://www.npr.org/search?query=<query>
"""

from __future__ import annotations

import re
from ._news_base import NewsBaseEngine


_PARSE_JS = r"""
(limit) => {
  const cards = Array.from(document.querySelectorAll(
    'article, .item, .item-info, [data-storylink-source]'
  ));
  const out = [];
  const seen = new Set();
  for (const c of cards) {
    if (out.length >= limit) break;
    const a = c.querySelector('a[href*="npr.org/"]');
    if (!a) continue;
    let url = a.href.split('?')[0];
    if (!/npr\.org\/(20\d{2}|sections|series|programs|podcasts|series)\//.test(url)) continue;
    if (seen.has(url)) continue;
    seen.add(url);
    const titleEl = c.querySelector('.title a, h2 a, h3 a, .item-title') || a;
    const title = (titleEl.textContent || '').trim();
    const dateEl = c.querySelector('time, .dateblock, [class*="date" i]');
    const published = dateEl ? dateEl.textContent.trim() : '';
    const sectionEl = c.querySelector('.slug a, [class*="slug" i]');
    const section = sectionEl ? sectionEl.textContent.trim() : '';
    const snipEl = c.querySelector('.teaser, p');
    const snippet = snipEl ? snipEl.textContent.trim() : '';
    out.push({title, url, snippet, section, published});
  }
  return out;
}
"""


class NPREngine(NewsBaseEngine):
    name = "npr"
    HOME_URL = "https://www.npr.org/"
    SEARCH_URL = "https://www.npr.org/search?query={query}"
    HOST_RE = re.compile(
        r"https?://(?:www\.)?npr\.org/(?:20\d{2}|sections|series|programs|podcasts)/",
        re.IGNORECASE,
    )
    SITE_TITLE_SUFFIXES = (" : NPR", " | NPR", " - NPR")

    def _parse_direct(self, limit: int) -> list[dict]:
        try:
            return self.page.evaluate(_PARSE_JS, max(limit, 5)) or []
        except Exception:
            return []
