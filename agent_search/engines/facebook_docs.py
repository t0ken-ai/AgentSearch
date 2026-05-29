"""Meta Developer Documentation search engine.

Searching ``developers.facebook.com`` directly is painful — the site
is a heavy React SPA, its built-in search is intermittently broken
through proxies, and it puts all results behind a hydration round
trip. The pragmatic shortcut is to drive a regular web-search engine
(DuckDuckGo by default; SearxNG / Brave / Bing as alternates) with
``site:developers.facebook.com`` prepended.

This engine wraps that pattern and adds:

* A focused ``product`` filter so callers can narrow to one of Meta's
  many sub-platforms (Marketing API, Graph API, WhatsApp Business,
  Instagram Graph, Messenger, Threads, Audience Network, App Events,
  Ad Library, Login, Webhooks, …) — each maps to an ``inurl:`` term
  that drastically tightens the result set.
* Optional ``api_version`` filter (``v21.0`` / ``latest`` / etc.) so
  reference pages from the wrong API version don't pollute results.
* Returns clean :class:`SearchResult` objects ready to feed into
  :func:`agent_search.extract.extract_page` for full-page Markdown.

Modes
-----
``mode="search"`` (default)
    Free-text search across all of ``developers.facebook.com``.

``mode="reference"``
    Limit to the API reference (``/docs/.../reference/``) — useful
    when you specifically want the field-by-field method docs and
    not narrative tutorials.

``mode="changelog"``
    Limit to release-notes / changelog pages so you can see what
    changed in a given API version.
"""

from __future__ import annotations

import logging
import urllib.parse
from typing import Optional, Sequence

from .base import BaseEngine, SearchResult
from .duckduckgo import DuckDuckGoEngine

log = logging.getLogger(__name__)


# Product → inurl term. The site puts everything under
# /docs/<product>/ or /documentation/<vertical>/<product>/, so a
# substring match against the URL path works reliably.
_PRODUCTS: dict[str, list[str]] = {
    "marketing-api":     ["marketing-api"],
    "graph-api":         ["graph-api"],
    "whatsapp-business": ["whatsapp", "whatsapp-business-platform",
                          "whatsapp-cloud-api"],
    "whatsapp":          ["whatsapp"],
    "instagram-graph":   ["instagram-api", "instagram-platform"],
    "instagram":         ["instagram"],
    "messenger":         ["messenger-platform"],
    "threads":           ["threads"],
    "audience-network":  ["audience-network"],
    "app-events":        ["app-events", "app-ads"],
    "ad-library":        ["ads-library", "ad-library"],
    "login":             ["facebook-login"],
    "webhooks":          ["webhooks"],
    "business-sdk":      ["business-sdk", "facebook-business-sdk"],
    "permissions":       ["permissions"],
    "marketing":         ["marketing-api", "ads"],   # umbrella alias
}


class FacebookDocsEngine(BaseEngine):
    """``developers.facebook.com`` documentation search."""

    name = "facebook_docs"
    max_retries = 1     # The wrapped engine retries internally.

    SITE = "developers.facebook.com"

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}
        # Reuse the DDG engine — it's the most reliable site-search
        # backend across regions (Bing/Brave throttle 'site:' more
        # aggressively, and Google goes through cloakbrowser-stealth
        # checks that fail under residential proxies).
        self._ddg = DuckDuckGoEngine(page)

    def search(  # type: ignore[override]
        self,
        query: str,
        limit: int = 10,
        *,
        mode: str = "search",
        product: Optional[str] = None,
        api_version: Optional[str] = None,
    ) -> list[SearchResult]:
        """Search Meta's developer documentation.

        :param query:       Natural-language or term-based query, e.g.
                            ``"create ad campaign"`` /
                            ``"ad_archive endpoint"`` /
                            ``"webhook signature verification"``.
        :param limit:       Max results to return (default 10).
        :param mode:        ``search`` / ``reference`` / ``changelog``.
        :param product:     Optional sub-platform filter
                            (see :data:`_PRODUCTS` for the list).
        :param api_version: Optional ``v21.0`` etc. — adds
                            ``"vXX.Y"`` to the query.
        """
        m = (mode or "search").lower()
        if m not in ("search", "reference", "changelog"):
            raise ValueError(
                f"unknown mode {m!r}; choose search / reference / changelog"
            )

        ddg_query = self._build_ddg_query(query, m, product, api_version)
        log.info("[fb_docs] %s", ddg_query)

        results = self._ddg.search(ddg_query, limit=max(limit * 2, 20)) or []

        # Hard-filter results to the official site — DDG sometimes
        # leaks adjacent domains (developers.facebook.com.translate.goog,
        # facebook.com, etc.) that we don't want.
        kept: list[SearchResult] = []
        seen_urls: set[str] = set()
        for r in results:
            url = (r.url or "").lower()
            if "developers.facebook.com" not in url:
                continue
            # Skip mirror / translate domains
            if "translate.goog" in url or "cache" in url:
                continue
            if r.url in seen_urls:
                continue
            seen_urls.add(r.url)
            # Tag every result for downstream consumers.
            r.__dict__.update({
                "doc_site": self.SITE,
                "doc_section": self._infer_section(r.url),
                "product": product or self._infer_product(r.url),
                "api_version": api_version or self._infer_version(r.url),
            })
            kept.append(r)
            if len(kept) >= limit:
                break

        self.last_status = {
            "mode": m, "product": product, "api_version": api_version,
            "ddg_query": ddg_query,
            "raw_results": len(results),
            "kept": len(kept),
        }
        return kept

    # ── helpers ─────────────────────────────────────────────────────

    def _build_ddg_query(self, query: str, mode: str,
                         product: Optional[str],
                         api_version: Optional[str]) -> str:
        """Compose the underlying DDG query string."""
        parts = [f"site:{self.SITE}"]
        if product:
            terms = _PRODUCTS.get(product.lower())
            if terms:
                # OR them with parentheses so DDG honours alternate paths.
                parts.append(
                    "(" + " OR ".join(f"inurl:{t}" for t in terms) + ")"
                )
            else:
                # Unknown product — pass through as a plain inurl filter.
                parts.append(f"inurl:{product}")
        if mode == "reference":
            parts.append("inurl:reference")
        elif mode == "changelog":
            parts.append(
                "(inurl:changelog OR inurl:release-notes OR inurl:graph-api/changelog)"
            )
        if api_version:
            parts.append(f'"{api_version}"')
        if query:
            parts.append(query)
        return " ".join(parts)

    @staticmethod
    def _infer_section(url: str) -> str:
        """Best-effort infer of section from the URL path.

        ``/docs/marketing-api/reference/adcreative/`` → ``reference``
        ``/docs/graph-api/changelog/``                → ``changelog``
        ``/docs/whatsapp/``                           → ``guide``
        """
        u = url.lower()
        if "/reference/" in u:
            return "reference"
        if "changelog" in u or "release-notes" in u:
            return "changelog"
        if "/get-started" in u or "/quickstart" in u:
            return "quickstart"
        if "/tutorials" in u or "/tutorial/" in u:
            return "tutorial"
        if "/use-cases" in u:
            return "use_case"
        if "/webhooks" in u:
            return "webhook"
        return "guide"

    @staticmethod
    def _infer_product(url: str) -> str:
        """Pick the first matching product slug from the URL path."""
        u = url.lower()
        for slug, terms in _PRODUCTS.items():
            for t in terms:
                if t in u:
                    return slug
        return ""

    @staticmethod
    def _infer_version(url: str) -> str:
        """Extract a Graph API version like ``v21.0`` from the URL."""
        import re
        m = re.search(r"/v(\d{1,2}\.\d{1,2})/", url)
        return f"v{m.group(1)}" if m else ""

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        return self.search(query, limit)
