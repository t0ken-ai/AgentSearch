"""dev.to search adapter using Forem's public API + internal search feed.

dev.to runs on Forem (https://forem.com), which exposes a few JSON endpoints
that we can hit without an API key, without auth, and without the anti-bot
challenges typical of larger SEs:

1. **Internal search feed** (full-text query, used by the dev.to search page):

       GET https://dev.to/search/feed_content
           ?per_page=<n>&page=0
           &search_fields=<query>
           &class_name=Article
           &sort_by=hotness_score&sort_direction=desc

   Response shape (only the parts we use)::

       {
         "result": [
           {
             "id": ...,
             "title": "...",
             "path": "/<username>/<slug>",
             "user": {"username": "...", "name": "..."},
             "tag_list": ["rust", "async"]   # or "rust, async"
             "public_reactions_count": 42,
             "reading_time": 5,
             "description": "..."
           },
           ...
         ]
       }

2. **Public articles API** (tag-based, no full-text — useful as a fallback):

       GET https://dev.to/api/articles?per_page=<n>&tag=<tag>

   Each article in the array has ``url`` (absolute), ``title``,
   ``user.username``/``user.name``, ``tag_list``, ``public_reactions_count``,
   ``reading_time_minutes``.

The engine tries (1) first because it handles arbitrary multi-word queries,
then falls back to (2) using the first alpha-numeric token of the query as a
tag. Both endpoints return ``application/json`` so we use the same trick as
``archive_org`` / ``wikivoyage``: navigate the page to the URL and read
``document.body.innerText`` (Chromium renders the JSON as plain text).

Each :class:`SearchResult` carries the structured fields (``author``,
``tags``, ``reactions_count``, ``reading_time``) on attached attributes so
callers that want them don't have to reparse the snippet.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any
from urllib.parse import quote_plus

from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)


class DevToEngine(BaseEngine):
    """Search dev.to via Forem's public JSON endpoints."""

    name = "devto"

    SITE_BASE = "https://dev.to"
    # Forem's internal search feed — used by the dev.to /search page.
    SEARCH_URL = (
        "https://dev.to/search/feed_content"
        "?per_page={n}&page=0&class_name=Article"
        "&sort_by=hotness_score&sort_direction=desc"
        "&search_fields={q}"
    )
    # Public Forem API — only filters by tag, no full-text search.
    API_URL = "https://dev.to/api/articles?per_page={n}&tag={tag}"

    SNIPPET_MAX = 320

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _normalize_tags(value: Any) -> list[str]:
        """``tag_list`` is either ``list[str]`` or a comma-separated string."""
        if not value:
            return []
        if isinstance(value, list):
            return [str(t).strip() for t in value if str(t).strip()]
        return [t.strip() for t in str(value).split(",") if t.strip()]

    @staticmethod
    def _absolute_url(path_or_url: str) -> str:
        if not path_or_url:
            return ""
        if path_or_url.startswith(("http://", "https://")):
            return path_or_url
        if not path_or_url.startswith("/"):
            path_or_url = "/" + path_or_url
        return "https://dev.to" + path_or_url

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    def _fetch_json(self, url: str) -> Any:
        """``page.goto`` the URL and parse ``document.body.innerText`` as JSON."""
        log.debug("[devto] GET %s", url)
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            log.warning("[devto] goto failed for %s: %s", url, e)
            return None

        body = self.page.evaluate("() => document.body.innerText")
        if not body:
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            log.warning(
                "[devto] non-JSON response from %s: %s; body[:200]=%r",
                url, e, body[:200],
            )
            return None

    def _extract_author(self, art: dict) -> str:
        """dev.to uses different shapes for author across endpoints."""
        user = art.get("user")
        if isinstance(user, dict):
            for key in ("name", "username"):
                v = user.get(key)
                if v:
                    return str(v).strip()
        # /api/articles flattens these to top-level fields.
        for key in ("user_name", "username"):
            v = art.get(key)
            if v:
                return str(v).strip()
        return ""

    def _build_result(self, art: dict) -> SearchResult | None:
        title = (art.get("title") or "").strip()
        if not title:
            return None

        # /api/articles already returns absolute ``url``; the search feed only
        # gives a relative ``path``.
        url = (art.get("url") or "").strip() or self._absolute_url(art.get("path") or "")
        if not url:
            return None

        author = self._extract_author(art)
        tags = self._normalize_tags(art.get("tag_list") or art.get("tags"))
        reactions = self._safe_int(
            art.get("public_reactions_count")
            or art.get("positive_reactions_count")
            or 0
        )
        reading_time = self._safe_int(
            art.get("reading_time_minutes")
            or art.get("reading_time")
            or 0
        )
        description = (art.get("description") or "").strip()

        # Compose a compact, human-readable snippet.
        head_bits: list[str] = []
        if author:
            head_bits.append(f"by {author}")
        if reading_time:
            head_bits.append(f"{reading_time} min read")
        if reactions:
            head_bits.append(f"♥ {reactions}")
        if tags:
            head_bits.append("#" + ", #".join(tags[:5]))
        head = " | ".join(head_bits)

        snippet = " — ".join(b for b in (head, description) if b)
        if len(snippet) > self.SNIPPET_MAX:
            snippet = snippet[: self.SNIPPET_MAX].rstrip() + "…"

        result = SearchResult(
            title=title,
            url=url,
            snippet=snippet,
            score=reactions or None,
        )
        # Attach structured fields so callers don't have to reparse the snippet.
        result.author = author                      # type: ignore[attr-defined]
        result.tags = tags                          # type: ignore[attr-defined]
        result.reactions_count = reactions          # type: ignore[attr-defined]
        result.reading_time = reading_time          # type: ignore[attr-defined]
        result.description = description            # type: ignore[attr-defined]
        return result

    def _collect(self, articles: list, limit: int) -> list[SearchResult]:
        out: list[SearchResult] = []
        for art in articles:
            if not isinstance(art, dict):
                continue
            r = self._build_result(art)
            if r is None:
                continue
            out.append(r)
            if len(out) >= limit:
                break
        return out

    # ------------------------------------------------------------------ search paths
    def _search_via_feed(self, query: str, limit: int) -> list[SearchResult]:
        """Full-text search via the internal ``/search/feed_content`` endpoint."""
        url = self.SEARCH_URL.format(
            n=max(1, min(int(limit), 30)),
            q=quote_plus(query),
        )
        data = self._fetch_json(url)
        if not isinstance(data, dict):
            return []
        articles = data.get("result") or []
        if not isinstance(articles, list):
            return []
        return self._collect(articles, limit)

    def _search_via_api(self, query: str, limit: int) -> list[SearchResult]:
        """Tag-based fallback via the public ``/api/articles`` endpoint.

        ``/api/articles`` doesn't support free-text search, so we derive a
        single-token tag from the query (first alpha-numeric word, lowercased)
        and ask for articles tagged with it. This is best-effort — useful for
        single-keyword queries like "python" or "rust".
        """
        m = re.search(r"[A-Za-z][A-Za-z0-9_+\-]*", query)
        if not m:
            return []
        tag = m.group(0).lower()
        url = self.API_URL.format(
            n=max(1, min(int(limit), 30)),
            tag=quote_plus(tag),
        )
        data = self._fetch_json(url)
        if not isinstance(data, list):
            return []
        return self._collect(data, limit)

    # ------------------------------------------------------------------ main
    def _do_search(self, query: str, limit: int = 10) -> list[SearchResult]:
        # 1) Full-text search (handles multi-word queries).
        results = self._search_via_feed(query, limit)
        if results:
            log.info("[devto] feed_content returned %d results for %r", len(results), query)
            return results

        # 2) Fall back to the public tag-based articles API.
        log.info(
            "[devto] feed_content empty; falling back to /api/articles for %r",
            query,
        )
        results = self._search_via_api(query, limit)
        log.info("[devto] /api/articles returned %d results for %r", len(results), query)
        return results

    # dev.to / Forem expose these endpoints publicly — no anti-bot retries.
    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        return self._do_search(query, limit)
