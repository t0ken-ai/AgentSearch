"""Semantic Scholar adapter — academic search across disciplines.

Semantic Scholar (semanticscholar.org, by AI2) is the most useful
academic search engine for non-CS / cross-discipline queries — it
indexes biology, medicine, history, etc. that arXiv doesn't cover,
and returns citation counts + influential-citation counts that arXiv
also doesn't.

URL: ``https://www.semanticscholar.org/search?q=<query>&sort=relevance``

Output fields:
  * title             ← paper title
  * url               ← semanticscholar.org paper page
  * snippet           ← TLDR / abstract excerpt
  * authors           ← comma-joined first 3 authors
  * year              ← publication year (int when parseable)
  * venue             ← journal / conference name
  * citation_count    ← total citations
  * paper_id          ← S2 paper identifier (parsed from URL path)
"""

import logging
import re
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

CARD_SELECTORS = [
    "[data-test-id='search-result']",
    "div.cl-paper-row",
    "div.search-result",
    "article[data-paper-id]",
]

YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
CITES_RE = re.compile(r"([\d,]+)\s*citation", re.IGNORECASE)
PAPER_ID_RE = re.compile(r"/paper/[^/]+/([a-f0-9]{40})")


class SemanticScholarEngine(BaseEngine):
    name = "semanticscholar"

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        q = urllib.parse.quote(query)
        url = f"https://www.semanticscholar.org/search?q={q}&sort=relevance"
        log.info("[s2] %s", url)
        if not safe_goto(self.page, url, timeout=30000):
            return []
        human_delay(2.0, 3.5)

        cards = []
        used = None
        for sel in CARD_SELECTORS:
            try:
                cards = self.page.query_selector_all(sel)
            except Exception:
                cards = []
            if cards:
                used = sel
                break
        if not cards:
            return []
        log.info("[s2] selector %s → %d cards", used, len(cards))

        results: list[SearchResult] = []
        for c in cards[: limit * 2]:
            r = self._parse_card(c)
            if r and r.title:
                results.append(r)
            if len(results) >= limit:
                break
        return results

    def _parse_card(self, card) -> SearchResult | None:
        try:
            text = (card.inner_text() or "").strip()
        except Exception:
            text = ""

        title = ""
        href = ""
        try:
            t_el = (
                card.query_selector("a[data-test-id='title-link']")
                or card.query_selector("h2 a")
                or card.query_selector("a.cl-paper-title")
                or card.query_selector("a[href*='/paper/']")
            )
            if t_el:
                title = (t_el.inner_text() or "").strip()
                href = t_el.get_attribute("href") or ""
        except Exception:
            pass
        if href.startswith("/"):
            href = "https://www.semanticscholar.org" + href

        paper_id = ""
        m = PAPER_ID_RE.search(href)
        if m:
            paper_id = m.group(1)

        snippet = ""
        try:
            sn = (
                card.query_selector("[data-test-id='paper-tldr']")
                or card.query_selector("[data-test-id='paper-abstract']")
                or card.query_selector("p.tldr")
                or card.query_selector("span.cl-paper-abstract")
            )
            if sn:
                snippet = (sn.inner_text() or "").strip()
        except Exception:
            pass

        # Authors — multiple <a class='author'> within a header row.
        authors_list: list[str] = []
        try:
            for el in card.query_selector_all("a.cl-paper-authors__author, span.author, [data-test-id='author-list'] a")[:5]:
                a_text = (el.inner_text() or "").strip()
                if a_text and a_text not in authors_list:
                    authors_list.append(a_text)
        except Exception:
            pass
        authors = ", ".join(authors_list[:3])

        year: int | None = None
        try:
            ym = YEAR_RE.search(text or "")
            if ym:
                year = int(ym.group(0))
        except Exception:
            pass

        venue = ""
        try:
            v = card.query_selector("[data-test-id='venue-name'], span.cl-paper-venue")
            if v:
                venue = (v.inner_text() or "").strip()
        except Exception:
            pass

        citation_count: int | None = None
        cm = CITES_RE.search(text or "")
        if cm:
            try:
                citation_count = int(cm.group(1).replace(",", ""))
            except ValueError:
                pass

        snippet_bits = []
        if authors:
            snippet_bits.append(authors)
        if year:
            snippet_bits.append(str(year))
        if venue:
            snippet_bits.append(venue)
        if citation_count is not None:
            snippet_bits.append(f"{citation_count} citations")
        meta = " · ".join(snippet_bits)
        full_snippet = f"{meta}\n{snippet}".strip() if meta else snippet

        if not title:
            return None
        result = SearchResult(title=title, url=href, snippet=full_snippet, score=citation_count)
        result.__dict__.update({
            "authors": authors,
            "year": year,
            "venue": venue,
            "citation_count": citation_count,
            "paper_id": paper_id,
        })
        return result
