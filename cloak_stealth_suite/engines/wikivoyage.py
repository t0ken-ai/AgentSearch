"""Wikivoyage search adapter using the official MediaWiki API.

Wikivoyage is a free, worldwide travel guide built on the same MediaWiki
software as Wikipedia, so the same ``action=query&list=search`` API works
without any anti-bot challenges.

Endpoint:
    https://en.wikivoyage.org/w/api.php?action=query&list=search&srsearch=<q>&format=json

Each ``search`` hit gives us:
    - ``title``  : human-readable destination/article title (e.g. "Tokyo")
    - ``pageid`` : numeric page id (used as a stable fallback)
    - ``snippet`` : HTML-marked snippet of the matching text

We map this to :class:`SearchResult` with a wiki-style URL:
    https://en.wikivoyage.org/wiki/<Title_With_Underscores>
"""

from __future__ import annotations

import json
import logging
from urllib.parse import quote, urlencode

from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)


class WikivoyageEngine(BaseEngine):
    """Search Wikivoyage via the MediaWiki action=query API."""

    name = "wikivoyage"
    API_BASE = "https://en.wikivoyage.org/w/api.php"
    SITE_BASE = "https://en.wikivoyage.org/wiki/"

    def _do_search(self, query: str, limit: int = 10) -> list[SearchResult]:
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": max(1, min(int(limit), 50)),
            "format": "json",
            "utf8": 1,
        }
        url = f"{self.API_BASE}?{urlencode(params)}"

        # Use the browser to fetch the JSON API — keeps everything in one
        # session. The MediaWiki API returns clean JSON, no anti-bot issues.
        self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        body = self.page.evaluate("() => document.body.innerText")

        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            log.error("[wikivoyage] non-JSON response (%s): %r", e, body[:200])
            return []

        results: list[SearchResult] = []
        search_hits = data.get("query", {}).get("search", []) or []
        for hit in search_hits:
            title = hit.get("title", "") or ""
            if not title:
                continue
            snippet_html = hit.get("snippet", "") or ""
            # Strip the search-match highlighting markup MediaWiki injects.
            snippet = (
                snippet_html
                .replace('<span class="searchmatch">', "")
                .replace("</span>", "")
            )
            page_url = self.SITE_BASE + quote(title.replace(" ", "_"))
            results.append(SearchResult(
                title=title,
                url=page_url,
                snippet=snippet,
            ))

        log.info("[wikivoyage] Found %d results for %r", len(results), query)
        return results

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Wikivoyage's MediaWiki API doesn't need stealth retries."""
        return self._do_search(query, limit)
