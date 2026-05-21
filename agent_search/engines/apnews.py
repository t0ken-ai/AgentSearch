"""AP News (Associated Press) search adapter.

Direct path: https://apnews.com/search?q=<query>
"""

from __future__ import annotations

import re
from ._news_base import NewsBaseEngine


_PARSE_JS = r"""
(limit) => {
  const sels = [
    'div.PageList-items-item',
    'div.SearchResultsModule-results > div',
    'div[data-key*="card" i]',
    'a[href*="apnews.com/article/"]',
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
    const a = c.matches && c.matches('a[href]') ? c : c.querySelector('a[href*="apnews.com/article/"]');
    if (!a) continue;
    let url = a.href.split('?')[0];
    if (!/apnews\.com\/article\//.test(url)) continue;
    if (seen.has(url)) continue;
    seen.add(url);
    const titleEl = c.querySelector('h3, h2, .PagePromoContentIcons-text, [class*="Headline" i]') || a;
    const title = (titleEl.textContent || '').trim();
    const sectionEl = c.querySelector('[class*="Eyebrow" i], [class*="Tag" i]');
    const section = sectionEl ? sectionEl.textContent.trim() : '';
    const dateEl = c.querySelector('[class*="Timestamp" i], time, [data-source*="time" i]');
    const published = dateEl ? dateEl.textContent.trim() : '';
    out.push({title, url, section, published});
  }
  return out;
}
"""


class APNewsEngine(NewsBaseEngine):
    name = "apnews"
    HOME_URL = "https://apnews.com/"
    SEARCH_URL = "https://apnews.com/search?q={query}"
    HOST_RE = re.compile(
        r"https?://(?:www\.)?apnews\.com/article/", re.IGNORECASE,
    )
    SITE_TITLE_SUFFIXES = (" | AP News", " - AP News", " — AP News")

    def _parse_direct(self, limit: int) -> list[dict]:
        try:
            return self.page.evaluate(_PARSE_JS, max(limit, 5)) or []
        except Exception:
            return []
