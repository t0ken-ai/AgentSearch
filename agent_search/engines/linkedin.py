"""LinkedIn search adapter — people, profile lookup.

LinkedIn is a structurally login-walled site since 2024 — anonymous
queries get a sign-up wall. The right path here is:

1. User runs ``agentsearch login linkedin`` once → headed CloakBrowser
   pops, user signs in, cookies persist to
   ``~/.cache/agentsearch/profiles/linkedin/``.
2. All subsequent ``--profile linkedin`` invocations carry the session
   silently.

This adapter detects the LinkedIn ``li_at`` auth cookie and:
  * **logged in**: hits ``/search/results/people/?keywords=<q>`` and parses
    full result cards (name, headline, location, current company, profile
    URL).
  * **anonymous**: tries the public directory at ``/pub/dir?keywords=<q>``
    which returns shallow public-profile cards (name + headline + url
    only). Many queries return [] here — that's expected, log a hint
    pointing the user at ``agentsearch login linkedin``.

DOM note: LinkedIn re-mangles its CSS class names regularly, so we
target stable structural anchors (``ul.reusable-search__entity-result-list
li``, ``div.entity-result``) and within-card aria-labels rather than
specific class names. When even those break, we fall back to scanning
``a[href*='/in/']`` patterns.
"""

import logging
import re
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

LI_LOGIN_URL = "https://www.linkedin.com/login"
LI_SEARCH_AUTHED = "https://www.linkedin.com/search/results/people/?keywords={q}"
LI_SEARCH_PUBLIC = "https://www.linkedin.com/pub/dir/?firstName=&lastName=&keywords={q}"

# Logged-in result containers, in priority order. Each card holds one
# person (name, headline, location, current company, profile URL).
AUTHED_CARD_SELECTORS = [
    "ul.reusable-search__entity-result-list li",
    "div.entity-result",
    "li.reusable-search__result-container",
    "div[data-chameleon-result-urn]",
]

# Public directory cards — much shallower, but reachable without login.
PUBLIC_CARD_SELECTORS = [
    "li.pserp-layout__profile-result-list-item",
    "div.profile-card",
    "a[href*='/in/']",  # last-ditch: every profile anchor on the page
]

# Phrases that mean "we hit the sign-in wall, can't see results".
WALL_PHRASES = [
    "sign in to continue",
    "you're at the limit",
    "join now to see",
    "sign up to see who",
    "we couldn't find",
]


class LinkedInEngine(BaseEngine):
    name = "linkedin"
    max_retries = 2  # LinkedIn responds quickly when it's going to fail

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}
        self._last_mode: str = "unknown"

    # ------------------------------------------------------------------ search

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        q = urllib.parse.quote(query)

        if self._has_login():
            log.info("[linkedin] li_at cookie present — using authed search")
            results = self._search_authed(q, limit)
            if results:
                self._last_mode = "authed"
                return results
            log.info("[linkedin] authed search empty — falling through to public")

        else:
            log.warning(
                "[linkedin] no li_at cookie — using shallow public directory. "
                "Run `agentsearch login linkedin` and then pass "
                "`--profile linkedin` for full results."
            )

        results = self._search_public(q, limit)
        self._last_mode = "public"
        return results

    # -------------------------------------------------------- login detection

    def _has_login(self) -> bool:
        """True when the browser context carries a LinkedIn ``li_at`` cookie."""
        try:
            ctx = self.page.context
            cookies = ctx.cookies(["https://www.linkedin.com"])
        except Exception:
            return False
        for c in cookies or []:
            if isinstance(c, dict) and c.get("name") == "li_at" and c.get("value"):
                return True
        return False

    # ----------------------------------------------------------------- authed

    def _search_authed(self, q_encoded: str, limit: int) -> list[SearchResult]:
        url = LI_SEARCH_AUTHED.format(q=q_encoded)
        log.info("[linkedin] authed url: %s", url)
        if not safe_goto(self.page, url, timeout=30000):
            return []
        human_delay(2.0, 3.5)

        # If LinkedIn redirected us to login, we lost the session.
        if "/login" in (self.page.url or "") or "/authwall" in (self.page.url or ""):
            log.warning("[linkedin] redirected to login — session expired")
            return []

        cards = []
        used = None
        for sel in AUTHED_CARD_SELECTORS:
            try:
                cards = self.page.query_selector_all(sel)
            except Exception:
                cards = []
            if cards:
                used = sel
                break
        if not cards:
            log.warning("[linkedin] no authed cards matched any selector")
            return []
        log.info("[linkedin] authed selector %s → %d cards", used, len(cards))

        results: list[SearchResult] = []
        for c in cards[: limit * 2]:
            r = self._parse_authed_card(c)
            if r and r.title:
                results.append(r)
            if len(results) >= limit:
                break
        return results

    def _parse_authed_card(self, card) -> SearchResult | None:
        try:
            text = (card.inner_text() or "").strip()
        except Exception:
            text = ""

        # Profile anchor — every card has at least one a[href*='/in/'].
        href = ""
        try:
            a = card.query_selector("a[href*='/in/']")
            if a:
                href = a.get_attribute("href") or ""
        except Exception:
            pass
        if href and href.startswith("/"):
            href = "https://www.linkedin.com" + href
        if href:
            href = href.split("?")[0]  # strip miniProfileUrn etc.

        # Name — first non-empty line, but LinkedIn renders "Name • status badge"
        # so split on bullet/middot to pick the actual name.
        name = ""
        for line in (text or "").split("\n"):
            line = line.strip()
            if not line:
                continue
            if any(p in line.lower() for p in ["status is", "view ", "follow"]):
                continue
            # Take the part before " · " or "•" (LinkedIn appends connection
            # degree as " · 1st" / "· 2nd").
            name = re.split(r"\s+[·•]\s+", line)[0].strip()
            if 2 < len(name) < 80:
                break
        if not name:
            return None

        # Headline / company / location are the next 2-3 non-empty lines.
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        headline = ""
        location = ""
        for ln in lines[1:5]:
            if ln == name or ln.lower().startswith("view "):
                continue
            if not headline:
                headline = ln
            elif not location and any(
                kw in ln for kw in [",", " Area", " - ", "Greater "]
            ):
                location = ln
                break

        # Current company — heuristic: split headline on " at " (e.g. "PM at Google").
        current_company = ""
        m = re.search(r"\bat\s+(.+)$", headline)
        if m:
            current_company = m.group(1).strip()

        snippet_bits = [b for b in [headline, location] if b]
        snippet = " · ".join(snippet_bits)

        result = SearchResult(title=name, url=href, snippet=snippet)
        result.__dict__.update({
            "headline": headline,
            "location": location,
            "current_company": current_company,
        })
        return result

    # ------------------------------------------------------------- anonymous

    def _search_public(self, q_encoded: str, limit: int) -> list[SearchResult]:
        url = LI_SEARCH_PUBLIC.format(q=q_encoded)
        log.info("[linkedin] public url: %s", url)
        if not safe_goto(self.page, url, timeout=30000):
            return []
        human_delay(1.5, 2.5)

        # Detect sign-in wall.
        try:
            body = (self.page.inner_text("body") or "").lower()[:3000]
        except Exception:
            body = ""
        if any(p in body for p in WALL_PHRASES):
            log.info("[linkedin] hit sign-in wall on public dir")
            return []

        cards = []
        used = None
        for sel in PUBLIC_CARD_SELECTORS:
            try:
                cards = self.page.query_selector_all(sel)
            except Exception:
                cards = []
            if cards:
                used = sel
                break
        if not cards:
            return []
        log.info("[linkedin] public selector %s → %d items", used, len(cards))

        seen_urls: set[str] = set()
        results: list[SearchResult] = []
        for c in cards[: limit * 3]:
            try:
                # If we matched anchors directly, c is the <a> itself.
                if c.evaluate("(el) => el.tagName") == "A":
                    href = c.get_attribute("href") or ""
                    name = (c.inner_text() or "").strip()
                else:
                    a = c.query_selector("a[href*='/in/']") or c.query_selector("a")
                    href = (a.get_attribute("href") or "") if a else ""
                    name = (a.inner_text() or "").strip() if a else (c.inner_text() or "").strip()
            except Exception:
                continue

            if href and href.startswith("/"):
                href = "https://www.linkedin.com" + href
            if not href or "/in/" not in href:
                continue
            href = href.split("?")[0]
            if href in seen_urls:
                continue
            seen_urls.add(href)

            # Name often has the headline bolted on; clean it.
            name = re.split(r"\s+[·•|]\s+", name)[0].strip()
            if not name or len(name) > 100:
                continue

            results.append(SearchResult(title=name, url=href, snippet=""))
            if len(results) >= limit:
                break
        return results
