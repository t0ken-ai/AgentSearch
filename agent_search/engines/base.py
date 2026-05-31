"""Base adapter for all engines."""

import logging
import time
import random
from dataclasses import dataclass

from ..core import safe_goto, human_delay
from ..stealth.enhance import apply_stealth, check_blocked

log = logging.getLogger(__name__)


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str = ""
    score: int | None = None
    # When the engine can extract / infer a publication date from the
    # SERP item, store an ISO-8601 string ('2026-05-31' or
    # '2026-05-31T12:34:56Z'). Empty when not available. Used by
    # downstream consumers (and the agent) to sort by recency.
    published_date: str = ""


class BaseEngine:
    """Base class for site adapters."""

    name: str = "base"
    max_retries: int = 3

    def __init__(self, page):
        self.page = page
        apply_stealth(page)

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Execute search with retry logic."""
        for attempt in range(self.max_retries):
            try:
                results = self._do_search(query, limit)
                blocked = check_blocked(self.page)
                if blocked:
                    log.warning("[%s] Blocked (attempt %d): %s", self.name, attempt + 1, blocked)
                    human_delay(3, 6)
                    continue
                if results:
                    return results
                log.warning("[%s] No results (attempt %d)", self.name, attempt + 1)
            except Exception as e:
                log.error("[%s] Error (attempt %d): %s", self.name, attempt + 1, e)
            human_delay(2, 4)
        return []

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        raise NotImplementedError
