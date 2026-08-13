"""PubMed search adapter using NCBI E-utilities.

NCBI exposes a stable, public, programmatically-friendly API. There is no
anti-bot challenge, so requests go directly through a proxy-aware HTTP session
without consuming Chromium.

Two-call flow:
    1. ``esearch`` (``retmode=json``) — turn the user query into a list of PMIDs.
    2. ``efetch``  (``retmode=xml``)  — pull title, authors, and abstract
       for the returned PMIDs in a single batch request.

For each PubMed article we build a :class:`SearchResult` whose URL points to
the canonical PubMed landing page::

    https://pubmed.ncbi.nlm.nih.gov/<PMID>/

The snippet is composed of the first three authors and the abstract, trimmed
to ``SNIPPET_MAX`` characters.

NCBI usage policy (https://www.ncbi.nlm.nih.gov/books/NBK25497/):
    - <= 3 requests per second when no api_key is provided.
    - Identify your tool with a meaningful User-Agent.
The host-wide request gate spaces starts across concurrent CLI/MCP workers;
one search still batches all PMIDs into one follow-up ``efetch`` request.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

from .. import __version__
from ..rate_limit import wait_for_request_slot
from .base import HttpEngine, SearchResult

log = logging.getLogger(__name__)


class PubMedEngine(HttpEngine):
    """Search PubMed via NCBI E-utilities."""

    name = "pubmed"

    ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    ARTICLE_URL = "https://pubmed.ncbi.nlm.nih.gov/{pmid}/"

    USER_AGENT = (
        f"AgentSearch/{__version__} (PubMed adapter; "
        "https://github.com/AgentSearch)"
    )

    HTTP_TIMEOUT = 30
    SNIPPET_MAX = 400

    def _fetch(self, url: str) -> str:
        """GET one NCBI endpoint directly with the required tool identity."""
        # NCBI allows three requests/second without an API key. Reserving
        # starts across processes prevents parallel fan-out from exceeding it.
        wait_for_request_slot("eutils.ncbi.nlm.nih.gov", 0.34)
        return self.http_get(
            url,
            timeout=self.HTTP_TIMEOUT,
            headers={"Accept": "*/*", "User-Agent": self.USER_AGENT},
        ).text

    # --------------------------------------------------------------- esearch
    def _esearch(self, query: str, limit: int) -> list[str]:
        """Run esearch and return the matching PMID list (in relevance order)."""
        params = {
            "db": "pubmed",
            "term": query,
            "retmax": max(1, min(int(limit), 100)),
            "retmode": "json",
            "sort": "relevance",
        }
        url = f"{self.ESEARCH_URL}?{urlencode(params)}"

        try:
            body = self._fetch(url)
        except Exception as e:
            log.error("[pubmed] esearch request failed: %s", e)
            return []

        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            log.error("[pubmed] esearch returned non-JSON (%s): %r", e, body[:200])
            return []

        idlist = data.get("esearchresult", {}).get("idlist", []) or []
        return [str(pmid) for pmid in idlist]

    # ---------------------------------------------------------------- efetch
    def _efetch_articles(self, pmids: list[str]) -> dict[str, dict]:
        """Fetch article metadata for the given PMIDs and key by PMID string."""
        if not pmids:
            return {}

        params = {
            "db": "pubmed",
            "id": ",".join(pmids),
            "retmode": "xml",
            "rettype": "abstract",
        }
        url = f"{self.EFETCH_URL}?{urlencode(params)}"

        try:
            body = self._fetch(url)
        except Exception as e:
            log.error("[pubmed] efetch request failed: %s", e)
            return {}

        try:
            root = ET.fromstring(body)
        except ET.ParseError as e:
            log.error("[pubmed] efetch returned malformed XML (%s): %r", e, body[:200])
            return {}

        out: dict[str, dict] = {}
        for article in root.findall(".//PubmedArticle"):
            pmid_el = article.find(".//MedlineCitation/PMID")
            pmid = (pmid_el.text or "").strip() if pmid_el is not None else ""
            if not pmid:
                continue

            # Title — itertext to flatten <i>, <b>, <sup>, ... children.
            title_el = article.find(".//Article/ArticleTitle")
            title = (
                "".join(title_el.itertext()).strip() if title_el is not None else ""
            )

            # Abstract — may have multiple <AbstractText> sections (Background,
            # Methods, Results, Conclusion, ...). Concatenate them with labels.
            abstract_parts: list[str] = []
            for at in article.findall(".//Article/Abstract/AbstractText"):
                text = "".join(at.itertext()).strip()
                if not text:
                    continue
                label = at.attrib.get("Label")
                abstract_parts.append(f"{label}: {text}" if label else text)
            abstract = " ".join(abstract_parts)

            # Authors — prefer "ForeName LastName"; fall back to CollectiveName.
            authors: list[str] = []
            for au in article.findall(".//Article/AuthorList/Author"):
                last = (au.findtext("LastName") or "").strip()
                fore = (
                    au.findtext("ForeName")
                    or au.findtext("Initials")
                    or ""
                ).strip()
                collective = (au.findtext("CollectiveName") or "").strip()
                if last:
                    authors.append(f"{fore} {last}".strip())
                elif collective:
                    authors.append(collective)

            out[pmid] = {
                "title": title,
                "abstract": abstract,
                "authors": authors,
            }

        return out

    # ------------------------------------------------------------------ main
    def _do_search(self, query: str, limit: int = 10) -> list[SearchResult]:
        pmids = self._esearch(query, limit)
        if not pmids:
            log.info("[pubmed] esearch returned no PMIDs for %r", query)
            return []

        meta_by_pmid = self._efetch_articles(pmids)

        results: list[SearchResult] = []
        for pmid in pmids:
            meta = meta_by_pmid.get(pmid, {})
            title = meta.get("title") or f"PMID:{pmid}"
            abstract = meta.get("abstract") or ""
            authors = meta.get("authors") or []

            snippet_parts: list[str] = []
            if authors:
                preview = ", ".join(authors[:3])
                if len(authors) > 3:
                    preview += f", et al. ({len(authors)} authors)"
                snippet_parts.append(preview)
            if abstract:
                snippet_parts.append(abstract)
            snippet = " — ".join(snippet_parts)
            if len(snippet) > self.SNIPPET_MAX:
                snippet = snippet[: self.SNIPPET_MAX].rstrip() + "…"

            results.append(
                SearchResult(
                    title=title,
                    url=self.ARTICLE_URL.format(pmid=pmid),
                    snippet=snippet,
                )
            )

        log.info("[pubmed] Found %d results for %r", len(results), query)
        return results

    # NCBI E-utilities is a clean public API — no stealth retries needed.
    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        return self._do_search(query, limit)
