"""PubMed search adapter using NCBI E-utilities.

NCBI exposes a stable, public, programmatically-friendly API. There is no
anti-bot challenge, but we still issue HTTP requests via the browser context
(``page.evaluate(fetch ...)``) so the calls go through whatever TLS / proxy /
CA setup the user's stealth browser is already configured with.

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
The two requests we issue per ``search`` call (one esearch + one efetch)
sit comfortably under that limit.
"""

from __future__ import annotations

import json
import logging
from urllib.parse import urlencode
from xml.etree import ElementTree as ET

from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)


class PubMedEngine(BaseEngine):
    """Search PubMed via NCBI E-utilities."""

    name = "pubmed"

    ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
    EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
    ARTICLE_URL = "https://pubmed.ncbi.nlm.nih.gov/{pmid}/"

    USER_AGENT = (
        "AgentSearch/0.1 (PubMed adapter; "
        "https://github.com/AgentSearch)"
    )

    HTTP_TIMEOUT = 30
    SNIPPET_MAX = 400

    # ------------------------------------------------------------------ HTTP
    # We route the HTTP calls through the browser instead of urllib so that
    # the user's TLS chain (corporate MITM CAs, custom proxies, ...) handles
    # them — the same chain the rest of the suite already trusts. This also
    # means we don't pull in `requests`/`httpx` as runtime deps.
    _FETCH_JS = (
        "async (url) => {"
        "  const r = await fetch(url, {"
        "    credentials: 'omit',"
        "    headers: { 'Accept': '*/*' },"
        "  });"
        "  const text = await r.text();"
        "  return { ok: r.ok, status: r.status, text };"
        "}"
    )

    # NCBI is happy to serve fetch() requests from any origin; using the
    # eutils host as the base origin keeps us same-origin and avoids any
    # CORS preflight noise.
    _FETCH_BASE = "https://eutils.ncbi.nlm.nih.gov/"

    def _ensure_fetch_context(self) -> None:
        """Make sure the page is on an origin from which fetch() will work."""
        url = ""
        try:
            url = (self.page.url or "").lower()
        except Exception:
            url = ""
        if url.startswith("https://eutils.ncbi.nlm.nih.gov"):
            return
        try:
            self.page.goto(
                self._FETCH_BASE,
                wait_until="domcontentloaded",
                timeout=self.HTTP_TIMEOUT * 1000,
            )
        except Exception as e:
            log.warning("[pubmed] could not navigate to fetch base: %s", e)

    def _fetch(self, url: str) -> str:
        """GET ``url`` through the browser context and return the body as text."""
        self._ensure_fetch_context()
        result = self.page.evaluate(self._FETCH_JS, url)
        if not isinstance(result, dict):
            raise RuntimeError(f"unexpected fetch result type: {type(result).__name__}")
        if not result.get("ok"):
            raise RuntimeError(f"HTTP {result.get('status')} for {url}")
        return result.get("text") or ""

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
