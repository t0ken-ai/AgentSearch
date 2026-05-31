"""Crunchbase search adapter.

Crunchbase's public SERP at ``crunchbase.com/textsearch`` is fully
client-side rendered, gates almost every result behind login, and
aggressively rate-limits unauthenticated browsers. So instead of
scraping their UI we drive an external web-search engine
(DuckDuckGo by default, with the same Brave / Bing fallback chain
``dev_docs`` uses) with ``site:crunchbase.com`` prepended.

This trades raw breadth for reliability — we get the public-facing
profile pages (organization / person / acquisition / funding-round)
that Google / DDG already index, which is plenty for "who is X" or
"is Y a real company" lookups. For deeper structured data (cap tables,
funding rounds, board members) you still need the paid Crunchbase API.

Returned ``SearchResult`` items carry:

* ``title``  — Crunchbase profile title ("OpenAI - Crunchbase Company Profile")
* ``url``    — direct link to the Crunchbase profile
* ``snippet`` — DDG's description (usually the first paragraph of the profile)
* dynamic attribute ``profile_type`` — one of ``organization`` /
  ``person`` / ``hub`` / ``acquisition`` / ``funding_round`` / ``other``,
  inferred from the URL path.

Diagnostics
-----------
``engine.last_status`` records ``query`` / ``ddg_query`` / ``backend``
/ ``raw_results`` / ``kept`` for the same debugging surface as
``dev_docs``.
"""

from __future__ import annotations

import logging
from typing import Optional

from .base import BaseEngine, SearchResult
from .duckduckgo import DuckDuckGoEngine

log = logging.getLogger(__name__)

# Crunchbase URL → profile-type heuristic
_PROFILE_PATHS = {
    "/organization/": "organization",
    "/person/":       "person",
    "/hub/":          "hub",
    "/acquisition/":  "acquisition",
    "/funding_round/":"funding_round",
    "/funding-round/":"funding_round",
    "/event/":        "event",
    "/ipo/":          "ipo",
}


class CrunchbaseEngine(BaseEngine):
    """Crunchbase profile search via DDG site filter.

    Optional kwargs:

    * ``profile_type`` (str) — restrict to one profile type
      (``organization`` / ``person`` / ``acquisition`` / ``funding_round``).
      Adds an ``inurl:<segment>`` modifier; off by default.
    """

    name = "crunchbase"
    max_retries = 1

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}
        self._ddg = DuckDuckGoEngine(page)
        self._brave = None
        self._bing = None
        self._page = page

    def _get_brave(self):
        if self._brave is None:
            from .brave import BraveEngine
            self._brave = BraveEngine(self._page)
        return self._brave

    def _get_bing(self):
        if self._bing is None:
            from .bing import BingEngine
            self._bing = BingEngine(self._page)
        return self._bing

    def search(  # type: ignore[override]
        self,
        query: str,
        limit: int = 10,
        *,
        profile_type: Optional[str] = None,
    ) -> list[SearchResult]:
        if not query or not query.strip():
            return []

        parts = ["site:crunchbase.com"]
        if profile_type:
            seg = _PROFILE_TYPE_TO_PATH.get(profile_type.lower(), profile_type)
            parts.append(f"inurl:{seg}")
        parts.append(query)
        ddg_query = " ".join(parts)
        log.info("[crunchbase] %s", ddg_query)

        # Pull more than asked so post-filter has headroom
        wanted = max(limit * 2, 20)

        # Try DDG → Brave → Bing
        rs = self._ddg.search(ddg_query, limit=wanted) or []
        backend = "ddg"
        if not rs:
            try:
                rs = self._get_brave().search(ddg_query, limit=wanted) or []
                if rs:
                    backend = "brave"
            except Exception as e:
                log.debug("brave fallback failed: %s", e)
        if not rs:
            try:
                rs = self._get_bing().search(ddg_query, limit=wanted) or []
                if rs:
                    backend = "bing"
            except Exception as e:
                log.debug("bing fallback failed: %s", e)

        kept: list[SearchResult] = []
        seen: set[str] = set()
        for r in rs:
            url = (r.url or "").lower()
            if "crunchbase.com" not in url:
                continue
            if "translate.goog" in url or "/cache/" in url:
                continue
            if r.url in seen:
                continue
            seen.add(r.url)
            r.__dict__["profile_type"] = _infer_profile_type(url)
            kept.append(r)
            if len(kept) >= limit:
                break

        self.last_status = {
            "query": query,
            "ddg_query": ddg_query,
            "backend": backend if kept else "all-failed",
            "raw_results": len(rs),
            "kept": len(kept),
        }
        return kept

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        return self.search(query, limit)


# Map profile-type alias → URL path segment for inurl filter
_PROFILE_TYPE_TO_PATH = {
    "organization":  "organization",
    "company":       "organization",
    "person":        "person",
    "hub":           "hub",
    "acquisition":   "acquisition",
    "funding_round": "funding_round",
    "funding-round": "funding_round",
    "ipo":           "ipo",
    "event":         "event",
}


def _infer_profile_type(url: str) -> str:
    u = url.lower()
    for path_segment, label in _PROFILE_PATHS.items():
        if path_segment in u:
            return label
    return "other"
