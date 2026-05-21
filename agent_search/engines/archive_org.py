"""Internet Archive search adapter using the public ``advancedsearch.php`` API.

The Internet Archive (https://archive.org) exposes a fully public, JSON-shaped
Solr endpoint at ``advancedsearch.php`` that needs no auth, no API key, and has
no anti-bot challenge. Every item on archive.org — book, audio recording,
movie, software image, web capture, etc. — is indexed there.

Endpoint::

    https://archive.org/advancedsearch.php
        ?q=<query>
        &fl[]=identifier&fl[]=title&fl[]=mediatype
        &fl[]=date&fl[]=publicdate
        &fl[]=description&fl[]=creator
        &output=json
        &rows=<n>
        &page=1

Response shape (only the parts we use)::

    {
      "response": {
        "numFound": 18043,
        "docs": [
          {
            "identifier": "...",       # stable item id
            "title":      "...",       # may be str or list[str]
            "mediatype":  "audio" | "movies" | "texts" | "image" | ...
            "date":       "2021-02-11T00:00:00Z",   # author/recording date
            "publicdate": "2021-02-24T19:38:28Z",   # date uploaded to IA
            "description": "...",      # may be str or list[str]
            "creator":    "..."        # may be str or list[str]
          },
          ...
        ]
      }
    }

We map each ``doc`` to a :class:`SearchResult`:

    title   -> ``doc.title``
    url     -> ``https://archive.org/details/<identifier>``
    snippet -> ``[<mediatype>] <date> | <creator> — <description>``  (trimmed)

Each :class:`SearchResult` also carries the structured fields (``date``,
``media_type``, ``description``) on attached attributes so callers that want
them don't have to reparse the snippet — see :func:`_attach_meta`.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlencode

from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)


class ArchiveOrgEngine(BaseEngine):
    """Search archive.org via the ``advancedsearch.php`` JSON endpoint."""

    name = "archive_org"

    API_BASE = "https://archive.org/advancedsearch.php"
    DETAILS_BASE = "https://archive.org/details/"

    # Fields we ask Solr to return. Keeping the list small keeps the
    # response compact and predictable.
    FIELDS = [
        "identifier",
        "title",
        "mediatype",
        "date",
        "publicdate",
        "description",
        "creator",
    ]

    SNIPPET_MAX = 400

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _flatten(value: Any) -> str:
        """Solr fields can come back as str, list[str] or None — normalize."""
        if value is None:
            return ""
        if isinstance(value, list):
            # Join multi-valued fields with " / " so they stay readable.
            return " / ".join(str(v).strip() for v in value if str(v).strip())
        return str(value).strip()

    @staticmethod
    def _short_date(date_str: str) -> str:
        """``2021-02-11T00:00:00Z`` -> ``2021-02-11``; pass through anything else."""
        if not date_str:
            return ""
        if "T" in date_str:
            return date_str.split("T", 1)[0]
        return date_str

    def _build_url(self, query: str, limit: int) -> str:
        # urlencode + doseq=True turns the repeated ``fl[]`` params into the
        # ``fl%5B%5D=identifier&fl%5B%5D=title&...`` form Solr expects.
        params: list[tuple[str, str]] = [("q", query)]
        for f in self.FIELDS:
            params.append(("fl[]", f))
        params.extend([
            ("output", "json"),
            ("rows", str(max(1, min(int(limit), 100)))),
            ("page", "1"),
            # No sort param: Solr defaults to relevance (score desc), which
            # matches the archive.org website's default. Sending an empty
            # ``sort[]=`` is rejected with ``UNSUPPORTED_VALUE``.
        ])
        return f"{self.API_BASE}?{urlencode(params, doseq=True)}"

    def _attach_meta(
        self,
        result: SearchResult,
        *,
        identifier: str,
        media_type: str,
        date: str,
        description: str,
        creator: str,
    ) -> SearchResult:
        """Stick the structured fields on the SearchResult as plain attrs.

        ``SearchResult`` is a frozen-ish dataclass with only ``title/url/
        snippet/score``; attaching extra attributes lets richer callers
        (e.g. an agent that wants to filter by ``media_type``) read them
        without us breaking the dataclass contract for everyone else.
        """
        result.identifier = identifier   # type: ignore[attr-defined]
        result.media_type = media_type   # type: ignore[attr-defined]
        result.date = date               # type: ignore[attr-defined]
        result.description = description # type: ignore[attr-defined]
        result.creator = creator         # type: ignore[attr-defined]
        return result

    # ------------------------------------------------------------------ main
    def _do_search(self, query: str, limit: int = 10) -> list[SearchResult]:
        url = self._build_url(query, limit)
        log.debug("[archive_org] GET %s", url)

        # The advancedsearch endpoint returns ``application/json``; loading
        # it directly via ``page.goto`` works the same as wikipedia /
        # wikivoyage — Chromium renders the body as plain text.
        self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
        body = self.page.evaluate("() => document.body.innerText")

        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            log.error("[archive_org] non-JSON response (%s): %r", e, body[:200])
            return []

        docs = (data.get("response") or {}).get("docs") or []
        results: list[SearchResult] = []
        for doc in docs:
            identifier = self._flatten(doc.get("identifier"))
            if not identifier:
                # Without an identifier we can't build a stable URL — skip.
                continue

            title = self._flatten(doc.get("title")) or identifier
            media_type = self._flatten(doc.get("mediatype"))
            # Prefer the human-meaningful "date" (recording / publication
            # date); fall back to "publicdate" (uploaded-to-IA date).
            date = self._short_date(
                self._flatten(doc.get("date"))
                or self._flatten(doc.get("publicdate"))
            )
            description = self._flatten(doc.get("description"))
            creator = self._flatten(doc.get("creator"))

            # Build a compact, human-readable snippet.
            head_bits: list[str] = []
            if media_type:
                head_bits.append(f"[{media_type}]")
            if date:
                head_bits.append(date)
            if creator:
                head_bits.append(creator)
            head = " | ".join(head_bits)

            snippet = " — ".join(b for b in (head, description) if b)
            if len(snippet) > self.SNIPPET_MAX:
                snippet = snippet[: self.SNIPPET_MAX].rstrip() + "…"

            result = SearchResult(
                title=title,
                url=f"{self.DETAILS_BASE}{identifier}",
                snippet=snippet,
            )
            results.append(self._attach_meta(
                result,
                identifier=identifier,
                media_type=media_type,
                date=date,
                description=description,
                creator=creator,
            ))

        log.info(
            "[archive_org] Found %d results for %r (numFound=%s)",
            len(results),
            query,
            (data.get("response") or {}).get("numFound"),
        )
        return results

    # The public IA Solr endpoint is a clean public API — no stealth retries.
    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        return self._do_search(query, limit)
