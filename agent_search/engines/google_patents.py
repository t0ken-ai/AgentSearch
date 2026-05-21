"""Google Patents search adapter.

Search URL pattern::

    https://patents.google.com/?q=<query>

Google Patents renders results client-side as ``<search-result-item>`` custom
elements. Each item contains:

* a ``<state-modifier data-result="patent/<PATENT_ID>/<lang>">`` whose
  ``data-result`` attribute is the canonical handle for the patent;
* an ``<h3 id="htmlContent">`` with the title (with ``<em>`` query
  highlights);
* one or more ``<h4>`` elements with metadata (patent number, inventor,
  assignee — sometimes labelled with ``data-key``);
* a ``.metadata`` span carrying the priority / filing / publication dates;
* a ``.abstract`` span with a snippet of the abstract.

We extract via a single ``page.evaluate`` JS pass which is far more robust
than chaining many query_selectors from Python (the layout has changed in
the past, and JS lets us walk the elements once and pattern-match).

Anti-bot
--------
patents.google.com sometimes goes through ``consent.google.com`` for EU
clients, exactly like google.com search. We re-use the same consent
selectors as ``google.py`` and the same block-phrase detection.

Returned ``SearchResult`` extension fields
------------------------------------------
* ``r.patent_id``   — e.g. ``"US7654321B2"``
* ``r.assignee``    — e.g. ``"Google LLC"``
* ``r.inventor``    — best-effort, may be empty
* ``r.abstract``    — full abstract snippet from the result card
* ``r.filing_date`` — best-effort ISO date (``YYYY-MM-DD``) or year
* ``r.snippet``     — ``"<patent_id> · <assignee> · <filing_date> · <abstract...>"``
"""

from __future__ import annotations

import logging
import random
import time
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)


BASE_URL = "https://patents.google.com"

# Reuse google.com consent selectors — patents.google.com uses the same
# consent.google.com flow.
CONSENT_BUTTON_SELECTORS = [
    "button#L2AGLb",
    "button[aria-label*='Accept all' i]",
    "button[aria-label*='Accept All' i]",
    "button[aria-label*='Akzeptieren' i]",
    "button[aria-label*='Accepter' i]",
    "button[aria-label*='Aceptar' i]",
    "form[action*='consent'] button",
    "div[role='dialog'] button",
]

BLOCK_PHRASES = [
    "unusual traffic",
    "our systems have detected",
    "before you continue",
    "to continue, please type",
    "captcha",
    "i'm not a robot",
    "automated queries",
    "sending automated requests",
]

# How long we wait for the first <search-result-item> to render after
# navigation. The page is JS-driven so we cannot rely on domcontentloaded.
_RESULT_WAIT_MS = 25_000

# Per-attempt navigation timeout (ms).
_NAV_TIMEOUT_MS = 40_000


# JS that walks every <search-result-item> on the page and pulls structured
# rows out into a JSON-friendly list. Keeping the parsing in JS lets us
# iterate over elements without paying a Python<->browser round-trip per
# element, and is the most layout-robust place to do regex matching.
#
# Observed DOM layout (Google Patents, 2025):
#
#   <search-result-item id="patent/EP3838702B1">
#     <article>
#       <state-modifier data-result="patent/EP3838702B1/en">
#         <a><h3><raw-html><span id="htmlContent">TITLE</span></raw-html></h3></a>
#       </state-modifier>
#       <div class="abstract ...">                   ← outer container (NOT the abstract text)
#         <div class="flex">
#           <div class="layout ...">
#             <div class="figureViewButtonWrap">...</div>
#             <div class="flex">
#               <h4 class="metadata">                ← country codes + patent id + inventor + assignee
#                 <span class="active">EP</span>
#                 <span class="not_active">US</span>
#                 <span class="bullet-before"><span>EP3838702B1</span></span>
#                 <span><span class="bullet-before"></span>Vijaysai Patnaik</span>
#                 <span><span class="bullet-before"></span>Waymo Llc</span>
#               </h4>
#               <h4 class="dates">                   ← "Priority YYYY-MM-DD • Filed YYYY-MM-DD • ..."
#               <raw-html>BRIEF SUMMARY ...</raw-html>  ← the actual abstract snippet
_PARSE_JS = r"""
() => {
  const items = document.querySelectorAll('search-result-item');
  const out = [];
  const patentIdRe = /\b([A-Z]{2}\d{4,}[A-Z0-9]*)\b/;
  const txt = (el) => el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : '';

  for (const it of items) {
    // 1) Patent id: the search-result-item's own id attribute is the most
    //    reliable source (e.g. "patent/EP3838702B1").
    let patentId = '';
    const itemId = it.getAttribute('id') || '';
    let m = itemId.match(/^patent\/(.+)$/);
    if (m) patentId = m[1];

    // Fallback: <state-modifier data-result="patent/<ID>/<lang>">.
    if (!patentId) {
      const sm = it.querySelector('state-modifier[data-result]');
      if (sm) {
        const dr = sm.getAttribute('data-result') || '';
        const m2 = dr.match(/patent\/([^\/]+)/);
        if (m2) patentId = m2[1];
      }
    }

    // 2) Title — span#htmlContent inside state-modifier > a > h3 > raw-html.
    let title = '';
    const titleSpan =
      it.querySelector('state-modifier #htmlContent') ||
      it.querySelector('state-modifier h3') ||
      it.querySelector('h3 #htmlContent') ||
      it.querySelector('h3');
    title = txt(titleSpan);

    // 3) <h4 class="metadata"> — country codes + patent id + inventor + assignee.
    //    Direct child <span> elements; country-code spans carry the .active /
    //    .not_active class. The remainder is, in order:
    //       [patent id, inventor, assignee] (with optional bullet separators).
    let inventor = '';
    let assignee = '';
    let patentIdFromMeta = '';
    const metaH4 = it.querySelector('h4.metadata');
    if (metaH4) {
      const directSpans = Array.from(metaH4.children).filter(
        (c) => c.tagName === 'SPAN'
      );
      const nonCountrySpans = directSpans.filter(
        (s) => !s.classList.contains('active') && !s.classList.contains('not_active')
      );
      const values = [];
      for (const s of nonCountrySpans) {
        const t = txt(s);
        if (t) values.push(t);
      }
      if (values.length >= 1) patentIdFromMeta = values[0];
      if (values.length >= 2) inventor = values[1];
      if (values.length >= 3) assignee = values[2];
    }
    if (!patentId && patentIdFromMeta) {
      const idMatch = patentIdFromMeta.match(patentIdRe);
      patentId = idMatch ? idMatch[1] : patentIdFromMeta;
    }

    // 4) <h4 class="dates"> — "Priority YYYY-MM-DD • Filed YYYY-MM-DD • ..."
    let filingDate = '';
    let priorityDate = '';
    let publicationDate = '';
    const datesH4 = it.querySelector('h4.dates');
    if (datesH4) {
      const dt = txt(datesH4);
      const filed = dt.match(/Filed\s+(\d{4}-\d{2}-\d{2})/i);
      if (filed) filingDate = filed[1];
      const prio = dt.match(/Priority\s+(\d{4}-\d{2}-\d{2})/i);
      if (prio) priorityDate = prio[1];
      const pub = dt.match(/Published\s+(\d{4}-\d{2}-\d{2})/i);
      if (pub) publicationDate = pub[1];
      // If filing not present (some old patents only carry priority+pub),
      // fall back to priority, then to publication.
      if (!filingDate) filingDate = priorityDate || publicationDate;
    }

    // 5) Abstract — the <raw-html> element that is a sibling of the h4s
    //    (i.e. NOT the title's raw-html which lives inside <state-modifier>,
    //    and NOT the per-field raw-html elements that wrap inventor /
    //    assignee text inside <h4 class="metadata"> for query highlighting).
    let abstract = '';
    const allRawHtml = it.querySelectorAll('raw-html');
    for (const rh of allRawHtml) {
      if (rh.closest('state-modifier')) continue;  // skip title
      if (rh.closest('h4')) continue;              // skip inventor/assignee
      abstract = txt(rh);
      if (abstract) break;
    }

    // 6) Canonical Google Patents URL for the patent id.
    const url = patentId
      ? ('https://patents.google.com/patent/' + patentId + '/en')
      : '';

    out.push({
      title,
      patentId,
      url,
      assignee,
      inventor,
      abstract,
      filingDate,
      priorityDate,
      publicationDate,
    });
  }
  return out;
}
"""


class GooglePatentsEngine(BaseEngine):
    """Search Google Patents via the public HTML search page."""

    name = "google_patents"
    max_retries = 3

    def __init__(self, page):
        super().__init__(page)
        # Diagnostics for callers / tests.
        self.last_status: dict = {}

    # ------------------------------------------------------------------ search
    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # Warm up on the homepage so consent / cookies settle in before the
        # actual search query — same trick as the google.com adapter.
        if safe_goto(self.page, BASE_URL + "/", timeout=_NAV_TIMEOUT_MS, retries=1):
            human_delay(1.5, 3.0)
            self._handle_consent()
            self._human_hints()

        q = urllib.parse.quote(query)
        url = f"{BASE_URL}/?q={q}"
        log.info("[google_patents] navigating to %s", url)
        if not safe_goto(self.page, url, timeout=_NAV_TIMEOUT_MS):
            return []

        human_delay(2.0, 4.0)
        self._handle_consent()
        self._human_hints()

        # The result list is rendered after a JS fetch. Wait for at least
        # one <search-result-item> to appear — but don't fail outright if
        # it never does, so the diagnostic path can still report what we saw.
        try:
            self.page.wait_for_selector("search-result-item", timeout=_RESULT_WAIT_MS)
        except Exception as e:
            log.info("[google_patents] result selector wait failed: %s", e)

        if self._is_blocked():
            return []

        return self._extract_results(limit)

    # -------------------------------------------------------------- diagnostics
    def selector_counts(self) -> dict[str, int]:
        """Return how many elements the key result selectors match."""
        counts: dict[str, int] = {}
        for sel in (
            "search-result-item",
            "search-result-item h3",
            "search-result-item state-modifier[data-result]",
            "search-result-item .abstract",
        ):
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        return counts

    # ----------------------------------------------------------------- helpers
    def _handle_consent(self) -> None:
        """Click consent / cookie acceptance, including iframe variants."""
        for sel in CONSENT_BUTTON_SELECTORS:
            try:
                btn = self.page.query_selector(sel)
                if btn:
                    btn.click(timeout=3000)
                    log.info("[google_patents] clicked consent (%s)", sel)
                    human_delay(1, 2)
                    return
            except Exception:
                continue

        # consent.google.com sometimes lives inside an iframe — walk frames.
        try:
            for frame in self.page.frames:
                furl = (frame.url or "").lower()
                if "consent" not in furl:
                    continue
                for sel in CONSENT_BUTTON_SELECTORS:
                    try:
                        btn = frame.query_selector(sel)
                        if btn:
                            btn.click(timeout=3000)
                            log.info(
                                "[google_patents] clicked consent inside frame %s (%s)",
                                furl, sel,
                            )
                            human_delay(1, 2)
                            return
                    except Exception:
                        continue
        except Exception:
            pass

    def _is_blocked(self) -> bool:
        """Detect CAPTCHA / sorry / unusual-traffic interstitial."""
        try:
            url = (self.page.url or "").lower()
        except Exception:
            url = ""
        try:
            title = (self.page.title() or "").lower()
        except Exception:
            title = ""
        try:
            body = self.page.inner_text("body").lower()
        except Exception:
            body = ""

        self.last_status = {
            "url": url,
            "title": title,
            "body_len": len(body),
        }

        if "/sorry/" in url or "sorry" in title:
            log.warning("[google_patents] sorry page: title=%r url=%r", title, url)
            self.last_status["block_reason"] = "sorry"
            return True

        for phrase in BLOCK_PHRASES:
            if phrase in body:
                log.warning("[google_patents] block phrase detected: %r", phrase)
                self.last_status["block_reason"] = phrase
                return True

        return False

    def _human_hints(self) -> None:
        """Light human-like activity: mouse move + small scroll."""
        try:
            self.page.mouse.move(
                random.randint(100, 400),
                random.randint(100, 400),
                steps=10,
            )
        except Exception:
            pass
        try:
            self.page.evaluate(
                "() => window.scrollBy(0, Math.floor(Math.random()*400) + 100)"
            )
        except Exception:
            pass
        time.sleep(random.uniform(0.4, 1.0))

    # ---------------------------------------------------------------- extraction
    def _extract_results(self, limit: int) -> list[SearchResult]:
        try:
            raw = self.page.evaluate(_PARSE_JS) or []
        except Exception as e:
            log.error("[google_patents] page.evaluate failed: %s", e)
            return []

        results: list[SearchResult] = []
        for item in raw[: max(1, int(limit))]:
            title = (item.get("title") or "").strip()
            patent_id = (item.get("patentId") or "").strip()
            url = (item.get("url") or "").strip()
            assignee = (item.get("assignee") or "").strip()
            inventor = (item.get("inventor") or "").strip()
            abstract = (item.get("abstract") or "").strip()
            filing_date = (item.get("filingDate") or "").strip()
            priority_date = (item.get("priorityDate") or "").strip()
            publication_date = (item.get("publicationDate") or "").strip()

            # Need at least a patent_id or title+url to be useful.
            if not patent_id and not (title and url):
                continue
            if not url and patent_id:
                url = f"{BASE_URL}/patent/{patent_id}/en"
            if not title and patent_id:
                title = patent_id

            snippet_parts: list[str] = []
            if patent_id:
                snippet_parts.append(patent_id)
            if assignee:
                snippet_parts.append(assignee)
            if filing_date:
                snippet_parts.append(f"Filed {filing_date}")
            if abstract:
                trimmed = abstract if len(abstract) <= 220 else abstract[:220].rstrip() + "…"
                snippet_parts.append(trimmed)
            snippet = " · ".join(snippet_parts)

            sr = SearchResult(title=title, url=url, snippet=snippet)
            # Domain-specific extras — match the 1337x adapter's pattern of
            # attaching extra attributes to the SearchResult instance.
            sr.patent_id = patent_id
            sr.assignee = assignee
            sr.inventor = inventor
            sr.abstract = abstract
            sr.filing_date = filing_date
            sr.priority_date = priority_date
            sr.publication_date = publication_date
            results.append(sr)

        log.info("[google_patents] returned %d results", len(results))
        return results
