"""LinkedIn Jobs search adapter.

LinkedIn exposes a public, login-free search at:

    https://www.linkedin.com/jobs/search/?keywords=<q>[&location=<loc>]

The page is server-rendered with full job cards even for unauthenticated
users — they only nag you with a contextual sign-in modal (an overlay
``div.modal__overlay--visible`` carrying a "Sign in to LinkedIn" prompt
and a "Join now" CTA in the top nav). The job cards themselves are
already in the DOM behind that overlay, so dismissing the modal is
optional for parsing — we close it anyway so future scroll/click logic
can interact with the page normally.

Card layout (LinkedIn JSERP, 2025):

    <ul class="jobs-search__results-list">
      <li>
        <div class="base-card relative w-full ...">
          <a class="base-card__full-link" href="https://.../jobs/view/...">
            <span class="sr-only">Software Engineer at Notion</span>
          </a>
          <h3 class="base-search-card__title">Software Engineer</h3>
          <h4 class="base-search-card__subtitle">
            <a class="hidden-nested-link">Notion</a>
          </h4>
          <div class="base-search-card__metadata">
            <span class="job-search-card__location">San Francisco, CA</span>
            <time class="job-search-card__listdate"
                  datetime="2025-05-15">3 days ago</time>
            <!-- or .job-search-card__listdate--new -->
          </div>
        </div>
      </li>
      ...
    </ul>

We do all parsing in one ``page.evaluate`` JS pass to avoid a Python ↔
browser round-trip per element.

Fallback strategies (in order)
------------------------------
1. **Primary**: scrape the main ``/jobs/search/`` page.
2. **Guest API**: ``/jobs-guest/jobs/api/seeMoreJobPostings/search`` returns
   the same HTML fragment of ``<li>`` cards with no surrounding chrome —
   useful when the main page redirects us to ``/authwall`` or the modal
   blocks rendering on this Chromium build.
3. **Google site search**: ``google.com/search?q=site:linkedin.com/jobs/view+<q>``
   via :class:`GoogleEngine`. We get title + URL but lose
   company/location/date — best-effort last resort.

Returned ``SearchResult`` extension fields
------------------------------------------
* ``r.company``     — e.g. ``"Notion"``
* ``r.location``    — e.g. ``"San Francisco, CA"``
* ``r.date_posted`` — ISO date from ``<time datetime="...">`` if present,
                      else the human label ("3 days ago")
* ``r.snippet``     — ``"<company> · <location> · <date>"``
"""

from __future__ import annotations

import logging
import random
import time
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)


# Primary host. LinkedIn redirects regional traffic to country subdomains
# (uk.linkedin.com, in.linkedin.com, ...) but the path & DOM are identical.
_PRIMARY_HOST = "https://www.linkedin.com"

# Public-search routes.
_JOBS_SEARCH_PATH = "/jobs/search/"
_GUEST_API_PATH = "/jobs-guest/jobs/api/seeMoreJobPostings/search"

# Selectors used by the primary scraper.
_RESULT_SELECTORS: tuple[str, ...] = (
    "ul.jobs-search__results-list > li",
    "li.jobs-search-results__list-item",
    "div.base-card",
    "li div.base-search-card",
)

# Buttons / overlays that gate access. We try to dismiss them; failing
# that, we just parse around them since the cards are usually still in
# the DOM behind the overlay.
_MODAL_DISMISS_SELECTORS: tuple[str, ...] = (
    "button[data-tracking-control-name='public_jobs_contextual-sign-in-modal_modal_dismiss']",
    "button[data-tracking-control-name*='contextual-sign-in-modal_modal_dismiss']",
    "button.modal__dismiss",
    "icon.contextual-sign-in-modal__modal-dismiss-icon",
    "button[aria-label='Dismiss']",
    "button[aria-label*='Dismiss' i]",
)

# Cookie / GDPR consent buttons (linkedin.com/legal/cookie-consent).
_CONSENT_BUTTON_SELECTORS: tuple[str, ...] = (
    "button[action-type='ACCEPT']",
    "button[data-tracking-control-name='ga-cookie.consent.accept.v4']",
    "button[aria-label='Accept cookies']",
    "button[aria-label*='Accept' i]",
)

# Phrases / URL fragments that mean LinkedIn forced us through the auth wall
# or a Cloudflare-style block.
_BLOCK_URL_FRAGMENTS: tuple[str, ...] = (
    "/authwall",
    "/checkpoint/challenge",
    "/uas/login",
)
_BLOCK_PHRASES: tuple[str, ...] = (
    "let's do a quick security check",
    "sign in to continue",
    "this page isn't available",
    "join linkedin to continue",
)

# How long to wait for the first job card to render (ms).
_RESULT_WAIT_MS = 25_000

# Per-attempt navigation timeout (ms).
_NAV_TIMEOUT_MS = 40_000


# JS that walks every job card and pulls structured rows. The primary
# selectors are tried in order so we cope with both the React-driven
# `/jobs/search/` page and the simpler `seeMoreJobPostings` HTML fragment.
_PARSE_JS = r"""
(rootSelector) => {
  // Try a list of card selectors and use whichever matches first.
  const cardSelectors = [
    'ul.jobs-search__results-list > li',
    'li.jobs-search-results__list-item',
    'div.base-card',
    'li div.base-search-card',
  ];

  let cards = [];
  let usedSel = '';

  // Allow caller to scope the search if they pass a non-empty rootSelector.
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

  const cleanLinkedinUrl = (raw) => {
    if (!raw) return '';
    try {
      const u = new URL(raw, 'https://www.linkedin.com');
      // Strip tracking query params, keep the canonical /jobs/view/... path.
      u.search = '';
      u.hash = '';
      return u.toString();
    } catch (e) {
      return raw;
    }
  };

  const out = [];
  for (const c of cards) {
    // 1) Title — h3.base-search-card__title is the primary location;
    //    fall back to .sr-only text inside the full-link anchor (which
    //    LinkedIn fills with "Title at Company") and finally to a
    //    generic <h3> inside the card.
    let title = txt(c.querySelector('h3.base-search-card__title'))
             || txt(c.querySelector('.base-search-card__title'))
             || txt(c.querySelector('h3'));

    // 2) Company — .base-search-card__subtitle wraps an <a> with the name.
    let company = txt(c.querySelector('h4.base-search-card__subtitle a'))
               || txt(c.querySelector('.base-search-card__subtitle a'))
               || txt(c.querySelector('h4.base-search-card__subtitle'))
               || txt(c.querySelector('.base-search-card__subtitle'))
               || txt(c.querySelector('a.hidden-nested-link'));

    // 3) Location — .job-search-card__location.
    let location = txt(c.querySelector('.job-search-card__location'))
                || txt(c.querySelector('span.job-search-card__location'));

    // 4) URL — the .base-card__full-link anchor. Fall back to any
    //    /jobs/view/ link inside the card.
    let url = '';
    const fullLink = c.querySelector('a.base-card__full-link')
                  || c.querySelector('a[href*="/jobs/view/"]');
    if (fullLink) url = cleanLinkedinUrl(fullLink.getAttribute('href') || '');

    // 5) date_posted — prefer <time datetime="YYYY-MM-DD">.
    let datetimeAttr = '';
    let dateLabel = '';
    const t = c.querySelector('time');
    if (t) {
      datetimeAttr = t.getAttribute('datetime') || '';
      dateLabel = txt(t);
    }

    // 6) sr-only fallback for title — handles the rare card layout where
    //    h3 is missing but the anchor still contains "<Title> at <Company>".
    if (!title) {
      const sr = c.querySelector('a.base-card__full-link span.sr-only');
      if (sr) {
        const raw = txt(sr);
        const m = raw.match(/^(.*?)\s+at\s+(.+)$/i);
        if (m) {
          title = m[1].trim();
          if (!company) company = m[2].trim();
        } else {
          title = raw;
        }
      }
    }

    // Skip cards that have neither title nor URL — they're empty
    // placeholders the React app sometimes leaves behind.
    if (!title && !url) continue;

    out.push({
      title,
      company,
      location,
      url,
      datetimeAttr,
      dateLabel,
    });
  }

  return { usedSelector: usedSel, count: cards.length, items: out };
}
"""


class LinkedInJobsEngine(BaseEngine):
    """Search LinkedIn Jobs via the public guest-search page."""

    name = "linkedin_jobs"
    max_retries = 2  # primary already has its own multi-strategy fallback

    def __init__(self, page):
        super().__init__(page)
        # Diagnostics for callers / tests.
        self.last_status: dict = {}
        # Track which strategy produced results so tests can report it.
        self.last_strategy: str = ""

    # ------------------------------------------------------------------ search
    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # Strategy 1: main JSERP page (the URL the user requested in the spec).
        results = self._search_primary(query, limit)
        if results:
            self.last_strategy = "primary"
            return results

        # Strategy 2: guest API HTML fragment.
        log.info("[linkedin_jobs] primary returned 0, trying guest API")
        results = self._search_guest_api(query, limit)
        if results:
            self.last_strategy = "guest_api"
            return results

        # Strategy 3: Google `site:linkedin.com/jobs/view <q>` fallback.
        log.info("[linkedin_jobs] guest API returned 0, trying Google fallback")
        results = self._search_google_fallback(query, limit)
        if results:
            self.last_strategy = "google_fallback"
            return results

        return []

    # ------------------------------------------------------------ strategy 1
    def _search_primary(self, query: str, limit: int) -> list[SearchResult]:
        q = urllib.parse.quote(query)
        url = f"{_PRIMARY_HOST}{_JOBS_SEARCH_PATH}?keywords={q}"
        log.info("[linkedin_jobs] (primary) navigating to %s", url)

        if not safe_goto(self.page, url, timeout=_NAV_TIMEOUT_MS):
            log.warning("[linkedin_jobs] primary nav failed")
            return []

        human_delay(2.0, 3.5)
        self._handle_consent()
        self._dismiss_modal()
        self._human_hints()

        # Wait for at least one card to appear. Don't fail outright if the
        # selector never matches — fall through and let the parser report.
        try:
            self.page.wait_for_selector(
                ", ".join(_RESULT_SELECTORS), timeout=_RESULT_WAIT_MS
            )
        except Exception as e:
            log.info("[linkedin_jobs] primary card-selector wait failed: %s", e)

        if self._is_blocked():
            log.warning(
                "[linkedin_jobs] primary blocked: %s",
                self.last_status.get("block_reason"),
            )
            return []

        return self._extract_results(limit, root_selector=None)

    # ------------------------------------------------------------ strategy 2
    def _search_guest_api(self, query: str, limit: int) -> list[SearchResult]:
        """Hit the public ``seeMoreJobPostings`` endpoint, which returns a
        plain HTML fragment of ``<li>`` cards (no surrounding chrome /
        modal). We render it inside a Chromium ``data:`` URL so the same
        parser JS can reach it via document.querySelector.
        """
        q = urllib.parse.quote(query)
        api_url = (
            f"{_PRIMARY_HOST}{_GUEST_API_PATH}"
            f"?keywords={q}&start=0"
        )
        log.info("[linkedin_jobs] (guest_api) navigating to %s", api_url)

        if not safe_goto(self.page, api_url, timeout=_NAV_TIMEOUT_MS):
            log.warning("[linkedin_jobs] guest_api nav failed")
            return []

        human_delay(1.0, 2.0)

        if self._is_blocked():
            log.warning(
                "[linkedin_jobs] guest_api blocked: %s",
                self.last_status.get("block_reason"),
            )
            return []

        try:
            self.page.wait_for_selector(
                ", ".join(_RESULT_SELECTORS), timeout=_RESULT_WAIT_MS
            )
        except Exception as e:
            log.info("[linkedin_jobs] guest_api card-selector wait failed: %s", e)

        return self._extract_results(limit, root_selector=None)

    # ------------------------------------------------------------ strategy 3
    def _search_google_fallback(self, query: str, limit: int) -> list[SearchResult]:
        """Last-ditch fallback: ask Google for ``site:linkedin.com/jobs/view <q>``.

        Lightweight wrapper around :class:`GoogleEngine` so we don't have
        to re-implement consent / sorry / extraction. Loses company /
        location / date_posted (Google snippets don't carry them in a
        reliable shape), but keeps title + URL.
        """
        try:
            # Local import to avoid circular import at module load.
            from .google import GoogleEngine
        except Exception as e:
            log.error("[linkedin_jobs] cannot import GoogleEngine: %s", e)
            return []

        google = GoogleEngine(self.page)
        site_query = f'site:linkedin.com/jobs/view "{query}"'
        google_results = google.search(site_query, limit=limit)

        out: list[SearchResult] = []
        for g in google_results:
            url = g.url
            if "linkedin.com/jobs/view/" not in url:
                continue
            title = (g.title or "").strip()
            # Google's title for a LinkedIn jobs card looks like:
            #   "Software Engineer - Notion - LinkedIn"
            #   "Title at Company | LinkedIn"
            company = ""
            stripped = title
            for sep in [" - LinkedIn", " | LinkedIn"]:
                if stripped.endswith(sep):
                    stripped = stripped[: -len(sep)].strip()
                    break
            # Try " - " split (Google often uses this between title and company)
            if " - " in stripped:
                parts = [p.strip() for p in stripped.rsplit(" - ", 1)]
                if len(parts) == 2 and parts[1]:
                    title_only, company = parts
                    title = title_only
            elif " at " in stripped.lower():
                # "Title at Company"
                idx = stripped.lower().rfind(" at ")
                title = stripped[:idx].strip()
                company = stripped[idx + 4 :].strip()
            else:
                title = stripped

            sr = SearchResult(title=title, url=url, snippet=g.snippet or "")
            sr.company = company
            sr.location = ""
            sr.date_posted = ""
            out.append(sr)
            if len(out) >= limit:
                break

        log.info("[linkedin_jobs] google fallback returned %d results", len(out))
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
                    log.info("[linkedin_jobs] clicked consent (%s)", sel)
                    human_delay(0.8, 1.5)
                    return
            except Exception:
                continue

    def _dismiss_modal(self) -> None:
        """Dismiss the contextual sign-in modal that floats over results."""
        for sel in _MODAL_DISMISS_SELECTORS:
            try:
                btn = self.page.query_selector(sel)
                if btn:
                    btn.click(timeout=2500)
                    log.info("[linkedin_jobs] dismissed sign-in modal (%s)", sel)
                    human_delay(0.5, 1.2)
                    return
            except Exception:
                continue
        # Fallback: press Escape — works against many React modal libraries.
        try:
            self.page.keyboard.press("Escape")
        except Exception:
            pass

    def _is_blocked(self) -> bool:
        """Detect auth-wall / security-check redirect and similar gates."""
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
            log.error("[linkedin_jobs] page.evaluate failed: %s", e)
            return []

        if not payload:
            return []

        used = payload.get("usedSelector", "")
        raw_count = payload.get("count", 0)
        items = payload.get("items", []) or []
        log.info(
            "[linkedin_jobs] selector=%s raw_cards=%d parsed=%d",
            used or "<none>", raw_count, len(items),
        )

        results: list[SearchResult] = []
        seen_urls: set[str] = set()
        for item in items:
            title = (item.get("title") or "").strip()
            url = (item.get("url") or "").strip()
            company = (item.get("company") or "").strip()
            location = (item.get("location") or "").strip()
            datetime_attr = (item.get("datetimeAttr") or "").strip()
            date_label = (item.get("dateLabel") or "").strip()

            if not title or not url:
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)

            # Prefer ISO datetime when present, else fall back to the
            # human-readable label ("3 days ago").
            date_posted = datetime_attr or date_label

            snippet_parts: list[str] = []
            if company:
                snippet_parts.append(company)
            if location:
                snippet_parts.append(location)
            if date_posted:
                snippet_parts.append(date_posted)
            snippet = " · ".join(snippet_parts)

            sr = SearchResult(title=title, url=url, snippet=snippet)
            sr.company = company
            sr.location = location
            sr.date_posted = date_posted
            results.append(sr)

            if len(results) >= max(1, int(limit)):
                break

        log.info("[linkedin_jobs] returned %d results", len(results))
        return results
