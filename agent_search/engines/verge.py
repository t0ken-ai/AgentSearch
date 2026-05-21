"""The Verge search adapter (tech / consumer).

Direct path: https://www.theverge.com/search?q=<query>
"""

from __future__ import annotations

import re
from ._news_base import NewsBaseEngine


_PARSE_JS = r"""
(limit) => {
  const cards = Array.from(document.querySelectorAll(
    'div.duet--content-cards--content-card, article, [class*="content-card" i]'
  ));
  const out = [];
  const seen = new Set();
  for (const c of cards) {
    if (out.length >= limit) break;
    const a = c.querySelector('a[href*="theverge.com/"]');
    if (!a) continue;
    let url = a.href.split('?')[0];
    if (!/theverge\.com\/(?:20\d{2}|news|reviews|features|tech|science|policy|games)\//.test(url)) continue;
    if (seen.has(url)) continue;
    seen.add(url);
    const titleEl = c.querySelector('h2 a, h3 a, h4 a, [class*="headline" i] a, [data-analytics-link*="title" i]') || a;
    const title = (titleEl.textContent || '').trim();
    const authorEl = c.querySelector('[class*="author" i]');
    const author = authorEl ? authorEl.textContent.trim() : '';
    const dateEl = c.querySelector('time, [class*="time" i]');
    const published = dateEl ? dateEl.textContent.trim() : '';
    const snipEl = c.querySelector('[class*="dek" i], [class*="excerpt" i], p');
    const snippet = snipEl ? snipEl.textContent.trim() : '';
    out.push({title, url, snippet, section: author, published});
  }
  return out;
}
"""


class VergeEngine(NewsBaseEngine):
    name = "verge"
    HOME_URL = "https://www.theverge.com/"
    SEARCH_URL = "https://www.theverge.com/search?q={query}"
    HOST_RE = re.compile(
        r"https?://(?:www\.)?theverge\.com/"
        r"(?:20\d{2}|news|reviews|features|tech|science|policy|games)/",
        re.IGNORECASE,
    )
    SITE_TITLE_SUFFIXES = (" | The Verge", " - The Verge", " — The Verge")

    def _parse_direct(self, limit: int) -> list[dict]:
        try:
            return self.page.evaluate(_PARSE_JS, max(limit, 5)) or []
        except Exception:
            return []
