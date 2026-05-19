"""Indeed search adapter.

Indeed exposes a public, login-free search at:

    https://www.indeed.com/jobs?q=<query>[&l=<location>]

The page is server-rendered with full job cards even for unauthenticated
users. Indeed runs an aggressive Cloudflare-style anti-bot layer (the
infamous "Are you a person?" / Hold-tight challenge) on hot IP ranges,
so we keep the scraper conservative and provide a Google-site fallback.

Card layout (Indeed JSERP, 2024–2025):

    <td class="resultContent">
      <div class="job_seen_beacon">
        <h2 class="jobTitle">
          <a class="jcs-JobTitle" data-jk="abc123" href="/rc/clk?jk=abc123&...">
            <span title="Senior Python Developer">Senior Python Developer</span>
          </a>
        </h2>
        <span data-testid="company-name">Acme Corp</span>
        <div data-testid="text-location">Remote</div>
        <div data-testid="attribute_snippet_testid">$120,000 - $150,000 a year</div>
        <div class="salary-snippet-container">$120k - $150k</div>
        <div data-testid="jobsnippet_footer">
          <ul><li>Build Python services...</li></ul>
        </div>
      </div>
    </td>

We do all parsing in one ``page.evaluate`` JS pass and canonicalize the
job URL to ``https://www.indeed.com/viewjob?jk=<jk>`` so callers always
get a stable, shareable link (the in-card ``/rc/clk`` redirector carries
session-tracking parameters that expire quickly).

Fallback strategies (in order)
------------------------------
1. **Primary**: scrape ``/jobs?q=<q>`` directly.
2. **Google site search**: ``google.com/search?q=site:indeed.com/viewjob+<q>``
   via :class:`GoogleEngine`. We get title + URL (and often a salary
   snippet from Google's preview), but lose the structured company /
   location / salary fields.

Returned ``SearchResult`` extension fields
------------------------------------------
* ``r.company``     — e.g. ``"Acme Corp"``
* ``r.location``    — e.g. ``"Remote"``
* ``r.salary``      — e.g. ``"$120,000 - $150,000 a year"`` (may be empty)
* ``r.snippet``     — short job description excerpt
"""

from __future__ import annotations

import logging
import random
import re
import time
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)


# Primary host. Indeed redirects regional traffic to country subdomains
# (uk.indeed.com, ca.indeed.com, ...) but the path & DOM are identical.
_PRIMARY_HOST = "https://www.indeed.com"

_JOBS_SEARCH_PATH = "/jobs"

# Selectors used by the primary scraper. Tried in order; whichever matches
# first wins. Indeed shuffles markup between A/B variants, so we list
# multiple shapes.
#
# We prefer ``div.cardOutline`` first because — as of 2024 — Indeed renders
# the job-description snippet ``[data-testid="belowJobSnippet"]`` as a
# *sibling* of ``div.job_seen_beacon`` (both nested inside cardOutline).
# Using cardOutline as the card root means the snippet lives inside the
# card subtree we walk in the JS parser. The other selectors are kept
# as fallbacks in case a future redesign drops cardOutline.
_RESULT_SELECTORS: tuple[str, ...] = (
    'div.cardOutline',
    '[data-testid="slider_item"]',
    'div.job_seen_beacon',
    'td.resultContent',
    'a.tapItem',
)

# Cookie / GDPR consent buttons (mostly EU traffic — Indeed uses TrustArc /
# OneTrust banners). Best-effort; the page parses fine even if we leave them.
_CONSENT_BUTTON_SELECTORS: tuple[str, ...] = (
    "#onetrust-accept-btn-handler",
    "button#onetrust-accept-btn-handler",
    "button[aria-label='Accept all']",
    "button[aria-label*='Accept' i]",
    "button:has-text('Accept all')",
    "button:has-text('Accept')",
)

# Modal-dismiss selectors for Indeed's "create alert" / job-recommendation
# popovers that occasionally float over results.
_MODAL_DISMISS_SELECTORS: tuple[str, ...] = (
    "button[aria-label='close']",
    "button[aria-label='Close']",
    "button[aria-label*='close' i]",
    "button.icl-CloseButton",
    "button.popover-x-button-close",
)

# Phrases / URL fragments that mean Indeed forced us through their
# anti-bot challenge.
_BLOCK_URL_FRAGMENTS: tuple[str, ...] = (
    "/blocked",
    "/incapsula_resource",
    "challenge.indeed.com",
)
_BLOCK_PHRASES: tuple[str, ...] = (
    "are you a person",
    "checking your browser before",
    "hold tight",
    "verifying you are human",
    "request blocked",
    "additional verification required",
)

# How long to wait for the first job card to render (ms).
_RESULT_WAIT_MS = 25_000

# Per-attempt navigation timeout (ms).
_NAV_TIMEOUT_MS = 40_000


# JS that walks every job card and pulls structured rows. The primary
# selectors are tried in order so we cope with both the desktop "card"
# layout and the lighter "tap item" mobile-style fallback.
_PARSE_JS = r"""
(rootSelector) => {
  const cardSelectors = [
    'div.cardOutline',
    '[data-testid="slider_item"]',
    'div.job_seen_beacon',
    'td.resultContent',
    'a.tapItem',
  ];

  let cards = [];
  let usedSel = '';

  let root = document;
  if (rootSelector) {
    const r = document.querySelector(rootSelector);
    if (r) root = r;
  }

  for (const sel of cardSelectors) {
    const found = root.querySelectorAll(sel);
    if (found && found.length) {
      cards = Array.from(found);
      usedSel = sel;
      break;
    }
  }

  const txt = (el) => el ? (el.textContent || '').replace(/\s+/g, ' ').trim() : '';

  const cleanIndeedUrl = (raw, jk) => {
    // Always prefer the canonical viewjob URL when we have a jk.
    if (jk) return 'https://www.indeed.com/viewjob?jk=' + encodeURIComponent(jk);
    if (!raw) return '';
    try {
      const u = new URL(raw, 'https://www.indeed.com');
      // Strip tracking params from /rc/clk redirector when no jk attribute.
      if (u.pathname === '/rc/clk' || u.pathname === '/pagead/clk') {
        const innerJk = u.searchParams.get('jk');
        if (innerJk) {
          return 'https://www.indeed.com/viewjob?jk=' + encodeURIComponent(innerJk);
        }
      }
      u.hash = '';
      return u.toString();
    } catch (e) {
      return raw;
    }
  };

  const out = [];
  for (const c of cards) {
    // 1) Title — h2.jobTitle wraps the anchor; the text we want is in
    //    a span (often with a `title` attribute carrying the same text).
    let title = txt(c.querySelector('h2.jobTitle span[title]'))
             || txt(c.querySelector('h2.jobTitle a span'))
             || txt(c.querySelector('h2.jobTitle a'))
             || txt(c.querySelector('h2.jobTitle'));
    // Some "tapItem" cards put the title on the anchor directly.
    if (!title) {
      const tapTitle = c.querySelector('span[id^="jobTitle"]');
      if (tapTitle) title = txt(tapTitle);
    }
    if (!title) {
      const t = c.querySelector('[data-testid="jobTitle"]');
      if (t) title = txt(t);
    }

    // 2) Company.
    let company = txt(c.querySelector('[data-testid="company-name"]'))
               || txt(c.querySelector('span.companyName'))
               || txt(c.querySelector('a[data-tn-element="companyName"]'));

    // 3) Location.
    let location = txt(c.querySelector('[data-testid="text-location"]'))
                || txt(c.querySelector('div.companyLocation'))
                || txt(c.querySelector('[data-testid="job-location"]'));

    // 4) Salary — Indeed encodes salary in two places. The dedicated
    //    salary-snippet container is the most reliable; the
    //    attribute_snippet_testid often holds salary too but can also
    //    hold things like "Full-time" or "Remote", so we filter for
    //    currency markers.
    let salary = txt(c.querySelector('div.salary-snippet-container'))
              || txt(c.querySelector('.metadata.salary-snippet-container'))
              || txt(c.querySelector('[data-testid="attribute_snippet_compensation"]'));
    if (!salary) {
      const candidates = c.querySelectorAll('[data-testid="attribute_snippet_testid"]');
      for (const cand of candidates) {
        const t = txt(cand);
        if (/[$£€¥₹]|per year|per hour|a year|an hour/i.test(t)) {
          salary = t;
          break;
        }
      }
    }

    // 5) Snippet (short job-description excerpt). Modern Indeed exposes
    //    this via [data-testid="belowJobSnippet"], which is a sibling of
    //    div.job_seen_beacon — only reachable when the card root is the
    //    wider div.cardOutline. We keep the older selectors as fallbacks.
    let snippet = txt(c.querySelector('[data-testid="belowJobSnippet"]'))
               || txt(c.querySelector('[data-testid="jobsnippet_footer"] ul'))
               || txt(c.querySelector('[data-testid="jobsnippet_footer"]'))
               || txt(c.querySelector('div.job-snippet'))
               || txt(c.querySelector('.job-snippet-container'));

    // 6) URL + jk.
    let jk = '';
    let href = '';
    const titleAnchor = c.querySelector('h2.jobTitle a')
                     || c.querySelector('a.jcs-JobTitle')
                     || c.querySelector('a[data-jk]')
                     || c.querySelector('a[href*="/viewjob?jk="]')
                     || c.querySelector('a[href*="/rc/clk"]')
                     || (c.tagName === 'A' ? c : null);
    if (titleAnchor) {
      jk = titleAnchor.getAttribute('data-jk') || '';
      href = titleAnchor.getAttribute('href') || '';
    }
    if (!jk) {
      // Some markup variants put data-jk on the parent card, not the anchor.
      jk = c.getAttribute && c.getAttribute('data-jk') || '';
    }
    const url = cleanIndeedUrl(href, jk);

    if (!title && !url) continue;

    out.push({
      title,
      company,
      location,
      salary,
      snippet,
      url,
      jk,
    });
  }

  return { usedSelector: usedSel, count: cards.length, items: out };
}
"""


class IndeedEngine(BaseEngine):
    """Search Indeed via the public ``/jobs`` page."""

    name = "indeed"
    max_retries = 2  # primary already has its own multi-strategy fallback

    def __init__(self, page):
        super().__init__(page)
        # Diagnostics for callers / tests.
        self.last_status: dict = {}
        # Track which strategy produced results so tests can report it.
        self.last_strategy: str = ""

    # ------------------------------------------------------------------ search
    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # Strategy 1: the main /jobs page.
        results = self._search_primary(query, limit)
        if results:
            self.last_strategy = "primary"
            return results

        # Strategy 2: Google `site:indeed.com/viewjob <q>` fallback.
        log.info("[indeed] primary returned 0, trying Google fallback")
        results = self._search_google_fallback(query, limit)
        if results:
            self.last_strategy = "google_fallback"
            return results

        return []

    # ------------------------------------------------------------ strategy 1
    def _search_primary(self, query: str, limit: int) -> list[SearchResult]:
        q = urllib.parse.quote(query)
        url = f"{_PRIMARY_HOST}{_JOBS_SEARCH_PATH}?q={q}"
        log.info("[indeed] (primary) navigating to %s", url)

        if not safe_goto(self.page, url, timeout=_NAV_TIMEOUT_MS):
            log.warning("[indeed] primary nav failed")
            return []

        human_delay(2.0, 3.5)
        self._handle_consent()
        self._dismiss_modal()
        self._human_hints()

        try:
            self.page.wait_for_selector(
                ", ".join(_RESULT_SELECTORS), timeout=_RESULT_WAIT_MS
            )
        except Exception as e:
            log.info("[indeed] primary card-selector wait failed: %s", e)

        if self._is_blocked():
            log.warning(
                "[indeed] primary blocked: %s",
                self.last_status.get("block_reason"),
            )
            return []

        return self._extract_results(limit, root_selector=None)

    # ------------------------------------------------------------ strategy 2
    def _search_google_fallback(self, query: str, limit: int) -> list[SearchResult]:
        """Last-ditch fallback: ask Google for ``site:indeed.com/viewjob <q>``."""
        try:
            from .google import GoogleEngine
        except Exception as e:
            log.error("[indeed] cannot import GoogleEngine: %s", e)
            return []

        google = GoogleEngine(self.page)
        site_query = f'site:indeed.com/viewjob "{query}"'
        google_results = google.search(site_query, limit=limit)

        out: list[SearchResult] = []
        seen_jks: set[str] = set()
        for g in google_results:
            url = g.url
            if "indeed.com" not in url or "/viewjob" not in url:
                continue

            # Canonicalize: strip tracking params, keep ?jk=...
            jk = ""
            try:
                parsed = urllib.parse.urlparse(url)
                qs = urllib.parse.parse_qs(parsed.query)
                jk_list = qs.get("jk", [])
                if jk_list:
                    jk = jk_list[0]
            except Exception:
                jk = ""
            if not jk:
                # Fallback: regex pull
                m = re.search(r"[?&]jk=([A-Za-z0-9]+)", url)
                if m:
                    jk = m.group(1)

            if jk:
                if jk in seen_jks:
                    continue
                seen_jks.add(jk)
                canonical_url = f"https://www.indeed.com/viewjob?jk={jk}"
            else:
                canonical_url = url

            title = (g.title or "").strip()
            # Google's title for an Indeed job posting tends to be:
            #   "Senior Python Developer - Acme Corp - Indeed.com"
            #   "Senior Python Developer | Acme Corp | Indeed.com"
            company = ""
            stripped = title
            for sep in [
                " - Indeed.com",
                " | Indeed.com",
                " - Indeed",
                " | Indeed",
            ]:
                if stripped.lower().endswith(sep.lower()):
                    stripped = stripped[: -len(sep)].strip()
                    break
            if " - " in stripped:
                parts = [p.strip() for p in stripped.rsplit(" - ", 1)]
                if len(parts) == 2 and parts[1]:
                    title_only, company = parts
                    title = title_only
            elif " | " in stripped:
                parts = [p.strip() for p in stripped.rsplit(" | ", 1)]
                if len(parts) == 2 and parts[1]:
                    title_only, company = parts
                    title = title_only
            else:
                title = stripped

            sr = SearchResult(title=title, url=canonical_url, snippet=g.snippet or "")
            sr.company = company
            sr.location = ""
            sr.salary = ""
            out.append(sr)
            if len(out) >= limit:
                break

        log.info("[indeed] google fallback returned %d results", len(out))
        return out

    # -------------------------------------------------------------- diagnostics
    def selector_counts(self) -> dict[str, int]:
        """Return how many elements each candidate selector matches."""
        counts: dict[str, int] = {}
        for sel in _RESULT_SELECTORS:
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        return counts

    # ----------------------------------------------------------------- helpers
    def _handle_consent(self) -> None:
        """Click any GDPR / cookie consent button if present."""
        for sel in _CONSENT_BUTTON_SELECTORS:
            try:
                btn = self.page.query_selector(sel)
                if btn:
                    btn.click(timeout=2500)
                    log.info("[indeed] clicked consent (%s)", sel)
                    human_delay(0.8, 1.5)
                    return
            except Exception:
                continue

    def _dismiss_modal(self) -> None:
        """Dismiss any "create alert" / suggestion popover."""
        for sel in _MODAL_DISMISS_SELECTORS:
            try:
                btn = self.page.query_selector(sel)
                if btn:
                    btn.click(timeout=2500)
                    log.info("[indeed] dismissed modal (%s)", sel)
                    human_delay(0.5, 1.2)
                    return
            except Exception:
                continue
        try:
            self.page.keyboard.press("Escape")
        except Exception:
            pass

    def _is_blocked(self) -> bool:
        """Detect Cloudflare / Imperva challenge or similar gate."""
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

        for frag in _BLOCK_URL_FRAGMENTS:
            if frag in url:
                self.last_status["block_reason"] = f"url:{frag}"
                return True

        # Title-only sentinel: Indeed's challenge page sets the title to
        # "Just a moment..." (Cloudflare) or "Additional Verification Required".
        for needle in ("just a moment", "additional verification"):
            if needle in title:
                self.last_status["block_reason"] = f"title:{needle}"
                return True

        for phrase in _BLOCK_PHRASES:
            if phrase in body:
                self.last_status["block_reason"] = f"phrase:{phrase}"
                return True

        return False

    def _human_hints(self) -> None:
        """Light human-like activity: mouse move + small scroll."""
        try:
            self.page.mouse.move(
                random.randint(120, 480),
                random.randint(120, 480),
                steps=10,
            )
        except Exception:
            pass
        try:
            self.page.evaluate(
                "() => window.scrollBy(0, Math.floor(Math.random()*500) + 200)"
            )
        except Exception:
            pass
        time.sleep(random.uniform(0.4, 1.0))

    # ---------------------------------------------------------------- extraction
    def _extract_results(
        self, limit: int, root_selector: str | None
    ) -> list[SearchResult]:
        try:
            payload = self.page.evaluate(_PARSE_JS, root_selector or "")
        except Exception as e:
            log.error("[indeed] page.evaluate failed: %s", e)
            return []

        if not payload:
            return []

        used = payload.get("usedSelector", "")
        raw_count = payload.get("count", 0)
        items = payload.get("items", []) or []
        log.info(
            "[indeed] selector=%s raw_cards=%d parsed=%d",
            used or "<none>", raw_count, len(items),
        )

        results: list[SearchResult] = []
        seen_keys: set[str] = set()
        for item in items:
            title = (item.get("title") or "").strip()
            url = (item.get("url") or "").strip()
            company = (item.get("company") or "").strip()
            location = (item.get("location") or "").strip()
            salary = (item.get("salary") or "").strip()
            snippet_text = (item.get("snippet") or "").strip()
            jk = (item.get("jk") or "").strip()

            if not title or not url:
                continue

            # Dedup by jk when present, else by URL.
            key = jk or url
            if key in seen_keys:
                continue
            seen_keys.add(key)

            # Compose a useful default snippet. Prefer the structured
            # job-snippet text; fall back to "company · location · salary".
            if snippet_text:
                snippet = snippet_text
            else:
                parts: list[str] = []
                if company:
                    parts.append(company)
                if location:
                    parts.append(location)
                if salary:
                    parts.append(salary)
                snippet = " · ".join(parts)

            sr = SearchResult(title=title, url=url, snippet=snippet)
            sr.company = company
            sr.location = location
            sr.salary = salary
            sr.jk = jk
            results.append(sr)

            if len(results) >= max(1, int(limit)):
                break

        log.info("[indeed] returned %d results", len(results))
        return results
