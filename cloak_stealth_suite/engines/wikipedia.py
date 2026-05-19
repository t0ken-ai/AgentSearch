"""Wikipedia search adapter using the official MediaWiki API."""

import json
import logging
from urllib.parse import quote, urlencode

from .base import BaseEngine, SearchResult
from ..core import safe_goto, human_delay

log = logging.getLogger(__name__)


class WikipediaEngine(BaseEngine):
    """Search Wikipedia via the MediaWiki action=query API."""

    name = "wikipedia"
    API_BASE = "https://en.wikipedia.org/w/api.php"

    def _do_search(self, query: str, limit: int = 10) -> list[SearchResult]:
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": limit,
            "format": "json",
            "utf8": 1,
        }
        url = f"{self.API_BASE}?{urlencode(params)}"

        # Use the browser to fetch the JSON API — keeps everything in one session
        # The MediaWiki API returns clean JSON, no anti-bot issues
        self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        body = self.page.evaluate("() => document.body.innerText")
        data = json.loads(body)

        results = []
        search_hits = data.get("query", {}).get("search", [])
        for hit in search_hits:
            title = hit.get("title", "")
            page_id = hit.get("pageid", "")
            snippet = hit.get("snippet", "").replace("<span class=\"searchmatch\">", "").replace("</span>", "")
            page_url = f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}"
            results.append(SearchResult(
                title=title,
                url=page_url,
                snippet=snippet,
            ))

        log.info("[wikipedia] Found %d results for '%s'", len(results), query)
        return results

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Wikipedia API doesn't need stealth retries."""
        return self._do_search(query, limit)
