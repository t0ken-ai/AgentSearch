"""Naver (네이버) search adapter — Korea's #1 search engine (~60% share).

Korean users overwhelmingly default to Naver over Google. The web SERP
at ``search.naver.com`` mixes Naver-native verticals (블로그 / 카페 /
지식iN) with general-web hits. We focus on the general-web list under
the "통합검색" tab.

Falls back to a Google ``site:`` over-ride when Naver returns 0
(rare — Naver throttles less aggressively than Google does).
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
  // 통합검색 mixes blog / news / web. Each web hit is in .total_area or
  // .api_subject_bx with a .title_area > a.link_tit.
  const cards = document.querySelectorAll(
    '.total_wrap, .api_subject_bx, .lst_total > li, ' +
    '.bx > .total_area, .group_news > .news_wrap'
  );
  const seen = new Set();
  for (const c of cards) {
    if (out.length >= limit) break;
    const a = c.querySelector(
      'a.link_tit, a.api_txt_lines, .title_area a, .news_tit, ' +
      '.api_link, .total_tit a, h3 a'
    );
    if (!a) continue;
    const href = a.getAttribute('href') || '';
    const title = (a.innerText || '').trim();
    if (!href || !title) continue;
    if (seen.has(href)) continue;
    seen.add(href);
    let snippet = '';
    const sn = c.querySelector(
      '.api_txt_lines.dsc_txt_wrap, .dsc_txt, .total_dsc, ' +
      '.api_txt_lines, .news_dsc, p'
    );
    if (sn) snippet = (sn.innerText || '').trim();
    out.push({title, url: href, snippet});
  }
  return out;
}
"""


class NaverEngine(BaseEngine):
    name = "naver"
    max_retries = 2

    def _do_search(self, query, limit):
        q = urllib.parse.quote(query)
        url = f"https://search.naver.com/search.naver?where=nexearch&query={q}"
        if not safe_goto(self.page, url, timeout=25000):
            return []
        human_delay(1.0, 2.0)
        try:
            self.page.evaluate("() => window.scrollBy(0, 1200)")
            human_delay(0.3, 0.7)
        except Exception:
            pass
        try:
            raw = self.page.evaluate(_PARSE_JS, limit) or []
        except Exception as e:
            log.debug("[naver] parse failed: %s", e)
            raw = []
        out: list[SearchResult] = []
        for r in raw:
            t = (r.get("title") or "").strip()
            u = (r.get("url") or "").strip()
            if not t or not u:
                continue
            sn = (r.get("snippet") or "").strip()
            if len(sn) > 320:
                sn = sn[:320].rstrip() + "…"
            out.append(SearchResult(title=t, url=u, snippet=sn))
            if len(out) >= limit:
                break
        return out
