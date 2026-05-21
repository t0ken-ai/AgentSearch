"""CNN search adapter.

Direct path: https://edition.cnn.com/search?q=<query>
SPA-heavy; relies primarily on Google/Bing/DDG fallback chain.
"""

from __future__ import annotations

import re
from ._news_base import NewsBaseEngine


_PARSE_JS = r"""
(limit) => {
  const cards = Array.from(document.querySelectorAll(
    '.cnn-search__result, .container__item, [data-type*="result" i]'
  ));
  const out = [];
  const seen = new Set();
  for (const c of cards) {
    if (out.length >= limit) break;
    const a = c.querySelector('a[href*="cnn.com/"]');
    if (!a) continue;
    let url = a.href.split('?')[0];
    if (!/cnn\.com\/(20\d{2}|videos)\//.test(url)) continue;
    if (seen.has(url)) continue;
    seen.add(url);
    const titleEl = c.querySelector('.cnn-search__result-headline, h3, h2, .container__headline-text, [class*="headline" i]') || a;
    const title = (titleEl.textContent || '').trim();
    const dateEl = c.querySelector('.cnn-search__result-publish-date, .timestamp, time');
    const published = dateEl ? dateEl.textContent.trim() : '';
    const snipEl = c.querySelector('.cnn-search__result-body, [class*="body" i] p');
    const snippet = snipEl ? snipEl.textContent.trim() : '';
    out.push({title, url, snippet, published});
  }
  return out;
}
"""


class CNNEngine(NewsBaseEngine):
    name = "cnn"
    HOME_URL = "https://edition.cnn.com/"
    SEARCH_URL = "https://edition.cnn.com/search?q={query}"
    HOST_RE = re.compile(
        r"https?://(?:edition|www|us)\.cnn\.com/(?:20\d{2}|videos)/",
        re.IGNORECASE,
    )
    SITE_TITLE_SUFFIXES = (" | CNN", " - CNN", " — CNN")

    def _parse_direct(self, limit: int) -> list[dict]:
        try:
            return self.page.evaluate(_PARSE_JS, max(limit, 5)) or []
        except Exception:
            return []

    def _site_domain(self) -> str:
        return "cnn.com"
