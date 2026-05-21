"""The Guardian (UK) search adapter.

Direct path: https://www.theguardian.com/search?q=<query>
Fallback chain: Google site:theguardian.com → Bing → DuckDuckGo
"""

from __future__ import annotations

import re
from ._news_base import NewsBaseEngine


_PARSE_JS = r"""
(limit) => {
  // Guardian SPA renders <li.fc-slice__item> or div.dcr-* cards.
  const sels = [
    'li.fc-slice__item',
    'div[data-link-name="article"]',
    'a[data-link-name="article"]',
    'a[href*="/202"]',
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
    const a = c.matches && c.matches('a[href]') ? c : c.querySelector('a[href*="theguardian.com"], a[href*="/202"]');
    if (!a) continue;
    let url = a.href;
    if (url.startsWith('/')) url = 'https://www.theguardian.com' + url;
    if (!/theguardian\.com\/.+\/202\d/.test(url)) continue;
    if (seen.has(url)) continue;
    seen.add(url);
    const titleEl = c.querySelector('h3, .fc-item__title, [class*="headline" i], span.js-headline-text') || a;
    const title = (titleEl.textContent || '').trim();
    const sectionEl = c.querySelector('.fc-item__kicker, [class*="kicker" i]');
    const section = sectionEl ? sectionEl.textContent.trim() : '';
    const dateEl = c.querySelector('time, .fc-timestamp__text');
    const published = dateEl ? dateEl.textContent.trim() : '';
    const snippetEl = c.querySelector('.fc-item__standfirst, [class*="standfirst" i], [class*="trail" i]');
    const snippet = snippetEl ? snippetEl.textContent.trim() : '';
    out.push({title, url, snippet, section, published});
  }
  return out;
}
"""


class GuardianEngine(NewsBaseEngine):
    name = "guardian"
    HOME_URL = "https://www.theguardian.com/"
    SEARCH_URL = "https://www.theguardian.com/search?q={query}"
    HOST_RE = re.compile(
        r"https?://(?:www\.)?theguardian\.com/(?:[a-z\-]+/)+202[0-9]/",
        re.IGNORECASE,
    )
    SITE_TITLE_SUFFIXES = (
        " | The Guardian", " - The Guardian",
        " | Theguardian.com", " — The Guardian",
    )

    def _parse_direct(self, limit: int) -> list[dict]:
        try:
            return self.page.evaluate(_PARSE_JS, max(limit, 5)) or []
        except Exception:
            return []
