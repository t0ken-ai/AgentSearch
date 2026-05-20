"""arXiv academic preprints search adapter via the official Atom API.

API
---
``https://export.arxiv.org/api/query?search_query=all:<query>&start=0&max_results=<n>&sortBy=relevance``
returns Atom XML. Public, no auth, documented at
https://info.arxiv.org/help/api/user-manual.html — we honour the
recommended 3-second courtesy delay by setting ``max_retries = 1`` and
keeping our request rate at 1 search per call.

Same trick as ``pubmed.py``: rather than using ``urllib.request`` (which
hits the local TLS interception store), we navigate ``page`` to the
arxiv origin and use ``page.evaluate`` to ``fetch()`` the Atom feed —
that way TLS goes through the Chromium chain.

Each :class:`SearchResult` carries:

* ``arxiv_id`` — short id from the abstract URL (e.g. ``"2304.12345"``)
* ``authors``  — pipe-separated author list
* ``categories`` — primary category (e.g. ``"cs.AI"``)
* ``published`` — first-version submission date (YYYY-MM-DD)
* ``pdf_url``  — direct PDF URL
* ``abstract`` — full abstract text (also folded into ``snippet``)
"""

from __future__ import annotations

import logging
import re
import urllib.parse
import xml.etree.ElementTree as ET

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

ARXIV_HOME = "https://export.arxiv.org/"
ARXIV_API = "https://export.arxiv.org/api/query"

ATOM_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}

ABS_URL_RE = re.compile(r"https?://arxiv\.org/abs/([0-9.]+(?:v\d+)?)")
PDF_URL_RE = re.compile(r"https?://arxiv\.org/pdf/([0-9.]+(?:v\d+)?)")


class ArxivEngine(BaseEngine):
    """arXiv search via the official Atom API."""

    name = "arxiv"
    max_retries = 1

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # 1) Prime the page on arxiv origin so fetch() shares its TLS chain.
        if not safe_goto(self.page, ARXIV_HOME, timeout=20000, retries=1):
            log.warning("[arxiv] failed to reach origin")
            return []
        human_delay(0.4, 0.9)

        # 2) Build API URL.
        params = {
            "search_query": f"all:{query}",
            "start": "0",
            "max_results": str(max(limit, 5)),
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        api_url = f"{ARXIV_API}?{urllib.parse.urlencode(params)}"
        log.info("[arxiv] fetching %s", api_url)

        # 3) Fetch via page.evaluate so we use Chromium TLS.
        try:
            resp = self.page.evaluate(
                """async (url) => {
                  const r = await fetch(url, {credentials: 'omit'});
                  return {ok: r.ok, status: r.status, text: await r.text()};
                }""",
                api_url,
            )
        except Exception as e:
            log.warning("[arxiv] fetch raised: %s", e)
            return []

        if not (resp and resp.get("ok")):
            log.warning("[arxiv] HTTP %s", resp and resp.get("status"))
            self.last_status = {
                "url": api_url,
                "http_status": resp and resp.get("status"),
            }
            return []

        text = resp.get("text") or ""
        try:
            root = ET.fromstring(text)
        except ET.ParseError as e:
            log.warning("[arxiv] XML parse failed: %s", e)
            return []

        entries = root.findall("atom:entry", ATOM_NS)
        self.last_status = {
            "url": api_url,
            "entries": len(entries),
            "http_status": resp.get("status"),
        }

        results: list[SearchResult] = []
        for entry in entries:
            if len(results) >= limit:
                break
            title = self._text(entry.find("atom:title", ATOM_NS))
            summary = self._text(entry.find("atom:summary", ATOM_NS))
            published = self._text(entry.find("atom:published", ATOM_NS))[:10]

            # Abstract URL is the <id> of the entry.
            id_text = self._text(entry.find("atom:id", ATOM_NS))
            m = ABS_URL_RE.search(id_text)
            arxiv_id = m.group(1) if m else ""

            # PDF URL lives in <link rel="related" type="application/pdf">.
            pdf_url = ""
            html_url = ""
            for link in entry.findall("atom:link", ATOM_NS):
                rel = link.get("rel", "")
                href = link.get("href", "")
                title_a = link.get("title", "")
                if title_a == "pdf" or "/pdf/" in href:
                    pdf_url = href
                elif rel == "alternate":
                    html_url = href
            if not html_url and arxiv_id:
                html_url = f"https://arxiv.org/abs/{arxiv_id}"
            if not pdf_url and arxiv_id:
                pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"

            authors_list: list[str] = []
            for author in entry.findall("atom:author", ATOM_NS):
                nm = self._text(author.find("atom:name", ATOM_NS))
                if nm:
                    authors_list.append(nm)
            authors_str = " | ".join(authors_list)

            primary_cat_el = entry.find("arxiv:primary_category", ATOM_NS)
            primary_cat = primary_cat_el.get("term", "") if primary_cat_el is not None else ""

            head = []
            if authors_list:
                head.append(", ".join(authors_list[:3])
                            + (f" +{len(authors_list)-3}" if len(authors_list) > 3 else ""))
            if primary_cat:
                head.append(primary_cat)
            if published:
                head.append(published)
            head_text = " · ".join(head)
            snippet_parts = []
            if head_text:
                snippet_parts.append(head_text)
            if summary:
                snippet_parts.append(" ".join(summary.split())[:300])
            snippet = " — ".join(snippet_parts)[:400]

            r = SearchResult(title=title.strip(), url=html_url, snippet=snippet)
            r.arxiv_id = arxiv_id          # type: ignore[attr-defined]
            r.authors = authors_str        # type: ignore[attr-defined]
            r.categories = primary_cat     # type: ignore[attr-defined]
            r.published = published        # type: ignore[attr-defined]
            r.pdf_url = pdf_url            # type: ignore[attr-defined]
            r.abstract = " ".join(summary.split()) if summary else ""  # type: ignore[attr-defined]
            results.append(r)
        return results

    @staticmethod
    def _text(el) -> str:
        if el is None:
            return ""
        return (el.text or "").strip()
