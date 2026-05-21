"""NPM Registry search adapter.

Uses the npm Registry public search API:

    GET https://registry.npmjs.org/-/v1/search?text=<query>&size=<n>

Response shape (only the parts we use)::

    {
      "objects": [
        {
          "downloads": {"monthly": 415621297, "weekly": 105531731},
          "dependents": "103823",
          "updated":   "2026-05-20T08:37:03.749Z",
          "searchScore": 2289.4153,
          "package": {
            "name":        "express",
            "version":     "5.2.1",
            "description": "Fast, unopinionated, minimalist web framework",
            "keywords":    ["express", "framework", "..."],
            "license":     "MIT",
            "publisher":   {"username": "jonchurch", "email": "..."},
            "maintainers": [{"username": "...", "email": "..."}, ...],
            "links": {
              "homepage":   "https://expressjs.com/",
              "repository": "git+https://github.com/expressjs/express.git",
              "bugs":       "https://github.com/expressjs/express/issues",
              "npm":        "https://www.npmjs.com/package/express"
            },
            "date": "2025-12-01T20:49:43.268Z"
          },
          "score": {
            "final":  2289.4153,
            "detail": {"popularity": 1, "quality": 1, "maintenance": 1}
          },
          "flags": {"insecure": 0}
        },
        ...
      ],
      "total": 70425,
      "time":  "..."
    }

A single search call gives us everything the spec asks for — name, version,
description, license, weekly/monthly downloads, links, publisher, keywords,
and dependents — so we don't need any secondary requests.

The endpoint returns ``application/json`` with permissive CORS, and there's
no anti-bot challenge, so we use the same trick as ``archive_org`` /
``wikivoyage`` / ``devto``: navigate the page to the API URL and read
``document.body.innerText`` (Chromium renders the JSON as plain text).

Each :class:`SearchResult` carries the structured fields (``version``,
``description``, ``license``, ``downloads_weekly``, ``downloads_monthly``,
``keywords``, ``publisher``, ``dependents``) on attached attributes so
callers that want them don't have to reparse the snippet.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import quote_plus

from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)


class NpmSearchEngine(BaseEngine):
    """Search the npm registry via its public ``-/v1/search`` JSON endpoint."""

    name = "npm"

    SEARCH_URL = "https://registry.npmjs.org/-/v1/search?text={q}&size={n}"
    NPM_PACKAGE_URL = "https://www.npmjs.com/package/{name}"

    SNIPPET_MAX = 320
    MAX_PAGE_SIZE = 50  # registry caps `size` at 250; we ask for far less.

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _flatten_license(value: Any) -> str:
        """``license`` may be a string, a dict ``{type: ...}``, or absent."""
        if not value:
            return ""
        if isinstance(value, str):
            return value.strip()
        if isinstance(value, dict):
            v = value.get("type") or value.get("name")
            return str(v).strip() if v else ""
        if isinstance(value, list):  # rare: array of license dicts
            for item in value:
                got = NpmSearchEngine._flatten_license(item)
                if got:
                    return got
        return ""

    @staticmethod
    def _safe_int(value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _package_url(name: str, links: Any) -> str:
        """Prefer the canonical npmjs.com ``links.npm`` URL when present."""
        if isinstance(links, dict):
            v = links.get("npm")
            if v:
                return str(v).strip()
        # Scoped names like ``@types/node`` need to keep the ``@``/``/`` un-encoded
        # for the npmjs.com URL to actually resolve.
        return NpmSearchEngine.NPM_PACKAGE_URL.format(name=name)

    @staticmethod
    def _format_count(n: int) -> str:
        """``105531731`` -> ``105.5M``; ``12345`` -> ``12.3K``; ``42`` -> ``42``."""
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}K"
        return str(n)

    def _fetch_json(self, url: str) -> Any:
        """``page.goto`` the URL and parse ``document.body.innerText`` as JSON."""
        log.debug("[npm] GET %s", url)
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as e:
            log.warning("[npm] goto failed for %s: %s", url, e)
            return None

        body = self.page.evaluate("() => document.body.innerText")
        if not body:
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError as e:
            log.warning(
                "[npm] non-JSON response from %s: %s; body[:200]=%r",
                url, e, body[:200],
            )
            return None

    # ---------------------------------------------------------------- mapping
    def _build_result(self, obj: dict) -> SearchResult | None:
        pkg = obj.get("package") or {}
        if not isinstance(pkg, dict):
            return None

        name = (pkg.get("name") or "").strip()
        if not name:
            return None

        version = (pkg.get("version") or "").strip()
        description = (pkg.get("description") or "").strip()
        license_str = self._flatten_license(pkg.get("license"))

        keywords = pkg.get("keywords") or []
        if not isinstance(keywords, list):
            keywords = []
        keywords = [str(k).strip() for k in keywords if str(k).strip()]

        publisher = ""
        pub = pkg.get("publisher")
        if isinstance(pub, dict):
            publisher = (pub.get("username") or pub.get("name") or "").strip()

        url = self._package_url(name, pkg.get("links"))

        # Downloads can live either on the top-level ``obj`` (newer API shape)
        # or inside ``pkg`` — handle both.
        dl = obj.get("downloads")
        if not isinstance(dl, dict):
            dl = pkg.get("downloads") if isinstance(pkg.get("downloads"), dict) else {}
        weekly = self._safe_int(dl.get("weekly")) if isinstance(dl, dict) else 0
        monthly = self._safe_int(dl.get("monthly")) if isinstance(dl, dict) else 0

        dependents = self._safe_int(obj.get("dependents") or pkg.get("dependents") or 0)

        # Score: prefer weekly downloads when known, else searchScore.
        if weekly:
            final_score: int | None = weekly
        else:
            try:
                final_score = int(float(obj.get("searchScore") or 0))
            except (TypeError, ValueError):
                final_score = None
            if not final_score:
                final_score = None

        # Compose a compact, human-readable snippet.
        head_bits: list[str] = []
        if version:
            head_bits.append(f"v{version}")
        if license_str:
            head_bits.append(license_str)
        if weekly:
            head_bits.append(f"{self._format_count(weekly)}/wk")
        if dependents:
            head_bits.append(f"{self._format_count(dependents)} dependents")
        if publisher:
            head_bits.append(f"by {publisher}")
        head = " | ".join(head_bits)

        snippet = " — ".join(b for b in (head, description) if b)
        if len(snippet) > self.SNIPPET_MAX:
            snippet = snippet[: self.SNIPPET_MAX].rstrip() + "…"

        result = SearchResult(
            title=name,
            url=url,
            snippet=snippet,
            score=final_score,
        )
        # Attach structured fields so callers don't have to reparse the snippet.
        result.package_name = name                   # type: ignore[attr-defined]
        result.version = version                     # type: ignore[attr-defined]
        result.description = description             # type: ignore[attr-defined]
        result.license = license_str                 # type: ignore[attr-defined]
        result.downloads_weekly = weekly             # type: ignore[attr-defined]
        result.downloads_monthly = monthly           # type: ignore[attr-defined]
        result.dependents = dependents               # type: ignore[attr-defined]
        result.keywords = keywords                   # type: ignore[attr-defined]
        result.publisher = publisher                 # type: ignore[attr-defined]
        return result

    # ---------------------------------------------------------------- main
    def _do_search(self, query: str, limit: int = 10) -> list[SearchResult]:
        size = max(1, min(int(limit), self.MAX_PAGE_SIZE))
        url = self.SEARCH_URL.format(q=quote_plus(query), n=size)

        data = self._fetch_json(url)
        if not isinstance(data, dict):
            return []

        objects = data.get("objects") or []
        if not isinstance(objects, list):
            return []

        results: list[SearchResult] = []
        for obj in objects[:limit]:
            if not isinstance(obj, dict):
                continue
            r = self._build_result(obj)
            if r is not None:
                results.append(r)

        log.info(
            "[npm] %d results for %r (total=%s)",
            len(results), query, data.get("total"),
        )
        return results

    # The npm registry search endpoint is a clean public JSON API — no stealth
    # retries needed.
    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        return self._do_search(query, limit)
