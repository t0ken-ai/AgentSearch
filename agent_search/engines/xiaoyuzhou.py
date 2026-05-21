"""小宇宙FM (Xiaoyuzhou) podcast search adapter.

Strategy
--------
Xiaoyuzhou is a Chinese podcast platform whose web surface is essentially
a marketing landing page — every detail page (``/podcast/<id>`` and
``/episode/<id>``) returns a "找不到了" (not found) shell from
non-Chrome / non-app clients. The actual content is served by the
Chinese mobile app. Therefore there is no usable direct search.

We rely on external search engines for indexing. DuckDuckGo turns out
to have the broadest coverage for ``site:xiaoyuzhoufm.com``; Bing comes
second and Google is often rate-limited from this network. The chain is:

1. **Direct path** — try ``xiaoyuzhoufm.com/search/<q>`` for completeness.
   Always returns ``[]`` from non-app clients.
2. **DuckDuckGo site: fallback** — primary fallback.
3. **Bing site: fallback** — secondary.
4. **Google site: fallback** — tertiary (often /sorry/-walled).

Each :class:`SearchResult` carries:

* ``content_type`` — ``"podcast"`` (show) or ``"episode"``
* ``xyz_id``       — the 24-hex id from the URL
* ``podcast_name`` — show name parsed out of titles like
                     ``"<episode> | <show> | 小宇宙 - 听播客，上小宇宙"``
* ``source``       — ``"duckduckgo"`` / ``"bing"`` / ``"google"``
"""

from __future__ import annotations

import logging
import random
import re
import time
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult
from .duckduckgo import DuckDuckGoEngine
from .bing import BingEngine
from .google import GoogleEngine

log = logging.getLogger(__name__)

XYZ_HOME = "https://www.xiaoyuzhoufm.com"
XYZ_SEARCH = "https://www.xiaoyuzhoufm.com/search"

XYZ_HOST_RE = re.compile(
    r"https?://(?:www\.)?xiaoyuzhoufm\.com/", re.IGNORECASE
)
PODCAST_RE = re.compile(
    r"https?://(?:www\.)?xiaoyuzhoufm\.com/podcast/([0-9a-f]{20,32})", re.IGNORECASE
)
EPISODE_RE = re.compile(
    r"https?://(?:www\.)?xiaoyuzhoufm\.com/episode/([0-9a-f]{20,32})", re.IGNORECASE
)


class XiaoyuzhouEngine(BaseEngine):
    """Xiaoyuzhou podcast search via external search-engine fallbacks."""

    name = "xiaoyuzhou"
    max_retries = 1

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        return self._do_search(query, limit)

    # ------------------------------------------------------------------ search

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # Direct path is always empty in headless, but mark a status entry.
        self._probe_direct(query)

        # DuckDuckGo first — has the best coverage for site:xiaoyuzhoufm.com.
        log.info("[xiaoyuzhou] trying DuckDuckGo site:xiaoyuzhoufm.com")
        ddg = self._search_via(DuckDuckGoEngine, "duckduckgo", query, limit)
        if ddg:
            self.last_status["mode"] = "duckduckgo"
            return ddg

        log.info("[xiaoyuzhou] DDG empty; trying Bing")
        bing = self._search_via(BingEngine, "bing", query, limit)
        if bing:
            self.last_status["mode"] = "bing"
            return bing

        log.info("[xiaoyuzhou] Bing empty; trying Google")
        google = self._search_via(GoogleEngine, "google", query, limit)
        if google:
            self.last_status["mode"] = "google"
        return google

    # ----------------------------------------------------------- direct probe

    def _probe_direct(self, query: str):
        try:
            q = urllib.parse.quote(query)
            safe_goto(self.page, f"{XYZ_SEARCH}/{q}", timeout=15000, retries=0)
            human_delay(0.5, 1.0)
            try:
                body_len = len(self.page.inner_text("body") or "")
            except Exception:
                body_len = 0
            self.last_status = {
                "url": getattr(self.page, "url", ""),
                "body_len": body_len,
                "direct_count": 0,
            }
        except Exception as e:
            log.warning("[xiaoyuzhou] direct probe failed: %s", e)
            self.last_status = {"direct_error": str(e), "direct_count": 0}

    # --------------------------------------------------------- generic fallback

    def _search_via(self, engine_cls, source_label: str,
                    query: str, limit: int) -> list[SearchResult]:
        try:
            outer = engine_cls(self.page)
        except Exception as e:
            log.warning("[xiaoyuzhou] cannot construct %s: %s", engine_cls.__name__, e)
            return []

        query_attempts = [
            f'site:xiaoyuzhoufm.com "{query}"',
            f"site:xiaoyuzhoufm.com {query}",
            f"xiaoyuzhoufm.com {query} 播客",
        ]

        results: list[SearchResult] = []
        seen: set[str] = set()
        attempt_log: list[dict] = []

        for q in query_attempts:
            try:
                outer_results = outer.search(q, limit=max(limit * 3, 15))
            except Exception as e:
                log.warning("[xiaoyuzhou] %s raised on %r: %s",
                            source_label, q, e)
                outer_results = []

            attempt_log.append({"query": q, "organic": len(outer_results)})

            for r in outer_results:
                u = r.url or ""
                if not XYZ_HOST_RE.search(u):
                    continue
                content_type, xyz_id = self._classify_url(u)
                if not xyz_id:
                    # Skip pages like /careers, /agreement.
                    continue
                if xyz_id in seen:
                    continue
                seen.add(xyz_id)

                title, podcast_name = self._parse_title(r.title or "")
                if not title:
                    title = u
                snippet = (r.snippet or "")[:320]

                new_r = SearchResult(title=title[:200], url=u, snippet=snippet)
                new_r.xyz_id = xyz_id                # type: ignore[attr-defined]
                new_r.content_type = content_type    # type: ignore[attr-defined]
                new_r.podcast_name = podcast_name    # type: ignore[attr-defined]
                new_r.duration = ""                  # type: ignore[attr-defined]
                new_r.description = snippet          # type: ignore[attr-defined]
                new_r.source = source_label          # type: ignore[attr-defined]
                results.append(new_r)
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break

        self.last_status[f"{source_label}_attempts"] = attempt_log
        log.info("[xiaoyuzhou] %s fallback returned %d results",
                 source_label, len(results))
        return results

    @staticmethod
    def _classify_url(url: str) -> tuple[str, str]:
        m = EPISODE_RE.search(url)
        if m:
            return ("episode", m.group(1).lower())
        m = PODCAST_RE.search(url)
        if m:
            return ("podcast", m.group(1).lower())
        return ("page", "")

    @staticmethod
    def _parse_title(title: str) -> tuple[str, str]:
        """Strip Xiaoyuzhou tail and split episode | show | 小宇宙 ..."""
        if not title:
            return ("", "")
        t = title.strip()
        # Strip the universal site-name suffix.
        for sep in (
            " - xiaoyuzhoufm.com",
            " | 小宇宙 - 听播客，上小宇宙",
            " - 小宇宙 - 听播客，上小宇宙",
            " | 小宇宙",
            " - 小宇宙",
        ):
            if t.endswith(sep):
                t = t[: -len(sep)].strip()
        # If title still has trailing " - xiaoyuzhoufm.com" or similar.
        t = re.sub(r"\s*-\s*xiaoyuzhoufm\.com\s*$", "", t).strip()

        # Try to split on " | " — episode title is usually the longest left,
        # show name is the right.
        parts = [p.strip() for p in t.split("|") if p.strip()]
        if len(parts) >= 2:
            episode = parts[0]
            show = parts[1] if len(parts) >= 2 else ""
            # Drop trailing 小宇宙 annotations from the show name.
            show = re.sub(r"^小宇宙.*$", "", show).strip()
            if not show and len(parts) >= 3:
                show = parts[2]
            return (episode, show)
        return (t, "")
