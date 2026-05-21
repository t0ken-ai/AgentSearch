"""MDN Web Docs adapter — Mozilla's web reference.

MDN is the canonical reference every web-dev agent needs (HTML / CSS /
JS / Web APIs / accessibility). MDN exposes a public search at
``https://developer.mozilla.org/api/v1/search?q=<q>`` that returns
JSON, but the JSON shape changes occasionally and the public HTML
search at ``/<locale>/search?q=<q>`` is more stable.

We hit the HTML search and parse the document list. Each result has:
  * title         ← document name
  * url           ← absolute URL
  * snippet       ← short summary
  * locale        ← e.g. "en-US"
  * page_type     ← "Web/API", "Web/CSS", "Web/JavaScript", ...
"""

import logging
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

# Result containers in priority order. MDN's current SERP renders results
# as h2-wrapped anchors. Selectors are kept loose so we work whether MDN
# renders results inside <main>, an <ol>, or as raw <h2> nodes.
RESULT_SELECTORS = [
    "h2 a[href*='/docs/']",
    "main h2 a",
    "ol li a[href*='/docs/']",
    "li.result-document",
    "div.result",
]


class MDNEngine(BaseEngine):
    name = "mdn"

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        q = urllib.parse.quote(query)
        url = f"https://developer.mozilla.org/en-US/search?q={q}"
        log.info("[mdn] %s", url)
        if not safe_goto(self.page, url, timeout=30000):
            return []
        human_delay(1.0, 2.0)

        cards = []
        used = None
        for sel in RESULT_SELECTORS:
            try:
                cards = self.page.query_selector_all(sel)
            except Exception:
                cards = []
            if cards:
                used = sel
                break
        if not cards:
            return []
        log.info("[mdn] selector %s → %d items", used, len(cards))

        results: list[SearchResult] = []
        seen: set[str] = set()
        for c in cards[: limit * 2]:
            r = self._parse_card(c)
            if r and r.url and r.url not in seen:
                seen.add(r.url)
                results.append(r)
            if len(results) >= limit:
                break
        return results

    def _parse_card(self, card) -> SearchResult | None:
        try:
            tag = card.evaluate("(el) => el.tagName")
        except Exception:
            tag = ""

        # If we matched anchors directly, the card *is* the link.
        if tag == "A":
            try:
                href = card.get_attribute("href") or ""
                title = (card.inner_text() or "").strip()
            except Exception:
                return None
        else:
            try:
                a = card.query_selector("a[href*='/docs/']") or card.query_selector("a")
                if not a:
                    return None
                href = a.get_attribute("href") or ""
                title = (a.inner_text() or "").strip()
            except Exception:
                return None

        if href.startswith("/"):
            href = "https://developer.mozilla.org" + href
        if not href.startswith("http"):
            return None

        # Skip nav anchors, locale switchers, etc. — keep only real docs.
        if "/docs/" not in href:
            return None

        snippet = ""
        try:
            sn_el = card.query_selector(".result-excerpt, p, span.summary")
            if sn_el:
                snippet = (sn_el.inner_text() or "").strip()
        except Exception:
            pass

        # Path-based page_type derivation: /docs/Web/API, /docs/Web/CSS, ...
        page_type = ""
        try:
            path = urllib.parse.urlparse(href).path
            parts = [p for p in path.split("/") if p]
            if len(parts) >= 4 and parts[1] == "docs":
                page_type = "/".join(parts[2:4])
        except Exception:
            pass

        if not title:
            return None
        result = SearchResult(title=title, url=href, snippet=snippet)
        result.__dict__.update({"page_type": page_type, "locale": "en-US"})
        return result
