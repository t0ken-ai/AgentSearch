"""DuckDuckGo Search adapter."""

import urllib.parse
from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult


class DuckDuckGoEngine(BaseEngine):
    name = "duckduckgo"

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        q = urllib.parse.quote(query)
        # Use HTML-only version (most stable, least anti-bot)
        url = f"https://html.duckduckgo.com/html/?q={q}"
        if not safe_goto(self.page, url):
            return []
        human_delay(1, 2)

        results = []
        items = self.page.query_selector_all(".result")
        for r in items[:limit]:
            title_el = r.query_selector(".result__a")
            snippet_el = r.query_selector(".result__snippet")

            title = title_el.inner_text().strip() if title_el else ""
            href = title_el.get_attribute("href") if title_el else ""
            snippet = snippet_el.inner_text().strip() if snippet_el else ""

            # Extract actual URL from DDG redirect
            if href and "uddg=" in href:
                actual = urllib.parse.parse_qs(
                    urllib.parse.urlparse(href).query
                ).get("uddg", [href])[0]
                href = actual

            if title:
                results.append(SearchResult(title=title, url=href, snippet=snippet))
        return results
