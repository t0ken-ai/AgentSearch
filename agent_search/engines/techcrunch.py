"""TechCrunch search adapter (tech / startup news).

Direct path: https://techcrunch.com/?s=<query>
TechCrunch renders search results server-side; the direct extractor
typically returns 10+ rows on first try.
"""

from __future__ import annotations

import re
from ._news_base import NewsBaseEngine


_PARSE_JS = r"""
(limit) => {
  // TechCrunch SERP renders <article> with .post-block or .wp-block-tc23-post-picker
  const cards = Array.from(document.querySelectorAll(
    'article, .post-block, .wp-block-tc23-post-picker, .loop-card'
  ));
  const out = [];
  const seen = new Set();
  for (const c of cards) {
    if (out.length >= limit) break;
    const a = c.querySelector('a.post-block__title__link, h2 a, h3 a, a[href*="techcrunch.com/20"]');
    if (!a) continue;
    let url = a.href.split('?')[0];
    if (!/techcrunch\.com\/20\d{2}\//.test(url)) continue;
    if (seen.has(url)) continue;
    seen.add(url);
    const title = (a.textContent || '').trim();
    const authorEl = c.querySelector('.river-byline__authors a, [class*="author" i] a, .loop-card__author');
    const author = authorEl ? authorEl.textContent.trim() : '';
    const dateEl = c.querySelector('time, .river-byline__time, [class*="time" i]');
    const published = dateEl ? dateEl.textContent.trim() : '';
    const snipEl = c.querySelector('.post-block__content, .loop-card__excerpt, .excerpt');
    const snippet = snipEl ? snipEl.textContent.trim() : '';
    out.push({title, url, snippet, section: author, published});
  }
  return out;
}
"""


class TechCrunchEngine(NewsBaseEngine):
    name = "techcrunch"
    HOME_URL = "https://techcrunch.com/"
    SEARCH_URL = "https://techcrunch.com/?s={query}"
    HOST_RE = re.compile(
        r"https?://(?:www\.)?techcrunch\.com/20\d{2}/", re.IGNORECASE,
    )
    SITE_TITLE_SUFFIXES = (" | TechCrunch", " - TechCrunch", " — TechCrunch")

    def _parse_direct(self, limit: int) -> list[dict]:
        try:
            return self.page.evaluate(_PARSE_JS, max(limit, 5)) or []
        except Exception:
            return []
