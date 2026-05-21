"""Shared base class for Western/European news adapters.

Most news sites (BBC, CNN, Reuters, Guardian, Al Jazeera, Verge, …) render
their search results client-side from React/Next/SvelteKit SPAs. Without
a logged-in session, harvesting them headlessly is brittle:

- Either the SPA hasn't hydrated yet (body collapses to a few hundred chars)
- Or the result schema changes between deploys.

Strategy
--------
Identical pattern to our Chinese walled-site adapters (douyin, weibo,
toutiao, xiaoyuzhou):

1. **Direct path** — visit the site's own search page and try a
   site-specific DOM extractor (overridden by subclass).
2. **Google site: fallback** — drive :class:`GoogleEngine` with
   ``site:<domain> "<query>"`` (and looser variants).
3. **Bing site: fallback** — when Google returns nothing or hits a
   ``/sorry/`` interstitial.
4. **DuckDuckGo fallback** — last resort, the most-tolerant of the three.

Every :class:`SearchResult` returned carries:

* ``article_id``  — best-effort id from URL (article slug or last path
                    segment)
* ``site``        — short site name (``"bbc"``, ``"cnn"`` …)
* ``published``   — date string (only when the direct extractor yields it)
* ``section``     — section / category (only on direct path)
* ``image_url``   — thumbnail (only on direct path when present)
* ``source``      — ``"<site>"`` (direct), ``"google"``, ``"bing"`` or
                    ``"duckduckgo"`` (fallback)
"""

from __future__ import annotations

import logging
import random
import re
import time
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult
from .google import GoogleEngine
from .bing import BingEngine
from .duckduckgo import DuckDuckGoEngine

log = logging.getLogger(__name__)


class NewsBaseEngine(BaseEngine):
    """Shared base for Western news-site adapters.

    Subclasses must set:
        name        — engine handle (matches CLI ``--engine`` value)
        HOME_URL    — site home (used for cookie warmup)
        SEARCH_URL  — search page URL with ``{query}`` placeholder OR
                      override ``_build_search_url(query)``
        HOST_RE     — compiled regex matching valid article URLs on this site

    Subclasses may override:
        _parse_direct(self, limit) -> list[dict]
            Site-specific DOM extractor returning rows with at least
            ``title`` and ``url`` keys. Optional: ``snippet``, ``published``,
            ``section``, ``image_url``.
        _classify_url(url) -> str
            Best-effort article-id extractor.

    Defaults work for sites where the direct path is brittle and we just
    rely on the search-engine fallback chain.
    """

    name: str = "news_base"
    max_retries: int = 1

    HOME_URL: str = ""
    SEARCH_URL: str = ""        # may contain `{query}` placeholder
    HOST_RE: re.Pattern = re.compile(r"^$")
    SITE_TITLE_SUFFIXES: tuple[str, ...] = ()
    DIRECT_NAV_TIMEOUT_MS: int = 30000
    DIRECT_RESULT_WAIT_MS: int = 12000

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        return self._do_search(query, limit)

    # ------------------------------------------------------------------ search

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # 1) Direct path.
        try:
            direct = self._search_direct(query, limit)
        except Exception as e:
            log.warning("[%s] direct path raised: %s", self.name, e)
            direct = []

        if direct:
            self.last_status["mode"] = self.name
            return direct

        # 2) Search-engine fallback chain.
        for engine_cls, label in (
            (GoogleEngine, "google"),
            (BingEngine, "bing"),
            (DuckDuckGoEngine, "duckduckgo"),
        ):
            log.info("[%s] direct empty; trying %s site:%s",
                     self.name, label, self._site_domain())
            try:
                fallback = self._search_via(engine_cls, label, query, limit)
            except Exception as e:
                log.warning("[%s] %s fallback raised: %s",
                            self.name, label, e)
                fallback = []
            if fallback:
                self.last_status["mode"] = label
                return fallback

        return []

    # ------------------------------------------------------------ direct path

    def _build_search_url(self, query: str) -> str:
        """Override this when sites use non-standard URL patterns."""
        encoded = urllib.parse.quote(query)
        if "{query}" in self.SEARCH_URL:
            return self.SEARCH_URL.replace("{query}", encoded)
        # Default: append ?q=<query> if no placeholder.
        sep = "&" if "?" in self.SEARCH_URL else "?"
        return f"{self.SEARCH_URL}{sep}q={encoded}"

    def _search_direct(self, query: str, limit: int) -> list[SearchResult]:
        # Cookie warmup on the homepage.
        if self.HOME_URL and safe_goto(self.page, self.HOME_URL,
                                       timeout=20000, retries=1):
            human_delay(0.4, 1.0)

        url = self._build_search_url(query)
        log.info("[%s] navigating to %s", self.name, url)
        if not safe_goto(self.page, url, timeout=self.DIRECT_NAV_TIMEOUT_MS):
            return []

        # Best-effort wait for SPA hydration.
        for _ in range(3):
            human_delay(0.8, 1.4)
            try:
                self.page.evaluate(
                    "(y) => window.scrollBy(0, y)",
                    random.randint(300, 600),
                )
            except Exception:
                pass

        try:
            rows = self._parse_direct(limit)
        except Exception as e:
            log.warning("[%s] _parse_direct raised: %s", self.name, e)
            rows = []

        try:
            body_len = len(self.page.inner_text("body") or "")
        except Exception:
            body_len = 0

        self.last_status = {
            "url": getattr(self.page, "url", ""),
            "body_len": body_len,
            "direct_count": len(rows),
        }

        results: list[SearchResult] = []
        seen: set[str] = set()
        for row in rows:
            url2 = (row.get("url") or "").strip()
            title = (row.get("title") or "").strip()
            if not url2 or not title:
                continue
            if not self.HOST_RE.search(url2):
                continue
            article_id = self._classify_url(url2)
            key = article_id or url2
            if key in seen:
                continue
            seen.add(key)

            snippet = (row.get("snippet") or "")[:320]
            head = []
            if row.get("section"):
                head.append(row["section"])
            if row.get("published"):
                head.append(row["published"])
            head_text = " · ".join(head)
            full_snippet = (
                " — ".join(p for p in (head_text, snippet) if p)[:320]
            )

            r = SearchResult(title=title[:200], url=url2, snippet=full_snippet)
            r.article_id = article_id           # type: ignore[attr-defined]
            r.site = self.name                  # type: ignore[attr-defined]
            r.published = row.get("published", "") or ""  # type: ignore[attr-defined]
            r.section = row.get("section", "") or ""      # type: ignore[attr-defined]
            r.image_url = row.get("image_url", "") or ""  # type: ignore[attr-defined]
            r.source = self.name                # type: ignore[attr-defined]
            results.append(r)
            if len(results) >= limit:
                break
        return results

    def _parse_direct(self, limit: int) -> list[dict]:
        """Override to provide a site-specific DOM extractor.

        Default implementation returns no rows — caller falls back to
        search-engine path.
        """
        return []

    # --------------------------------------------------------- generic fallback

    def _site_domain(self) -> str:
        """Return the bare domain used in `site:` filters."""
        if self.HOME_URL:
            host = urllib.parse.urlparse(self.HOME_URL).hostname or ""
            if host.startswith("www."):
                host = host[4:]
            return host
        return ""

    def _search_via(self, engine_cls, source_label: str,
                    query: str, limit: int) -> list[SearchResult]:
        try:
            outer = engine_cls(self.page)
        except Exception as e:
            log.warning("[%s] cannot construct %s: %s",
                        self.name, engine_cls.__name__, e)
            return []

        domain = self._site_domain()
        query_attempts = [
            f'site:{domain} "{query}"',
            f"site:{domain} {query}",
            f"{domain} {query}",
        ]

        results: list[SearchResult] = []
        seen: set[str] = set()
        attempt_log: list[dict] = []

        for q in query_attempts:
            try:
                outer_results = outer.search(q, limit=max(limit * 3, 15))
            except Exception as e:
                log.warning("[%s] %s raised on %r: %s",
                            self.name, source_label, q, e)
                outer_results = []

            attempt_log.append({"query": q, "organic": len(outer_results)})

            for r in outer_results:
                u = r.url or ""
                if not self.HOST_RE.search(u):
                    continue
                article_id = self._classify_url(u)
                key = article_id or u
                if key in seen:
                    continue
                seen.add(key)

                title = self._clean_title(r.title or "") or u
                snippet = (r.snippet or "")[:320]

                new_r = SearchResult(title=title[:200], url=u, snippet=snippet)
                new_r.article_id = article_id      # type: ignore[attr-defined]
                new_r.site = self.name             # type: ignore[attr-defined]
                new_r.published = ""               # type: ignore[attr-defined]
                new_r.section = ""                 # type: ignore[attr-defined]
                new_r.image_url = ""               # type: ignore[attr-defined]
                new_r.source = source_label        # type: ignore[attr-defined]
                results.append(new_r)
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break

        self.last_status[f"{source_label}_attempts"] = attempt_log
        log.info("[%s] %s fallback returned %d results",
                 self.name, source_label, len(results))
        return results

    @classmethod
    def _classify_url(cls, url: str) -> str:
        """Best-effort article id from URL.

        Default: take the last path segment (slug). Subclasses can override.
        """
        try:
            path = urllib.parse.urlparse(url).path.rstrip("/")
            if not path:
                return ""
            last = path.rsplit("/", 1)[-1]
            return last[:80]
        except Exception:
            return ""

    def _clean_title(self, title: str) -> str:
        if not title:
            return ""
        t = title.strip()
        for sep in self.SITE_TITLE_SUFFIXES:
            if t.endswith(sep):
                t = t[: -len(sep)].strip()
                break
        return t
