"""BlackHatWorld forum search adapter (XenForo 2) with site: fallbacks.

BlackHatWorld runs XenForo 2. Anonymous users can usually browse threads
but the in-forum search form (`/search/`) often requires a logged-in
session (it returns the search page itself with a login prompt, or a
"You must be logged in to perform this action" notice). To make the
adapter useful out of the box we layer three modes:

1. **bhw_direct** – Hit `https://www.blackhatworld.com/search/?q=<query>`
   and parse XenForo's `.contentRow` / `li.block-row` results. Works only
   when search is open to guests (occasionally true for cached / SEO
   landing pages). Detects the login wall and the "must be logged in"
   notice and bails out so we move to the next mode.

2. **google_site** – Run `site:blackhatworld.com <query>` against Google
   (with the same consent / warm-up handling as `google.py`) and parse
   the organic results. This is the primary path most of the time.

3. **ddg_site** – Last-resort fallback via the HTML-only DuckDuckGo
   endpoint (`html.duckduckgo.com`) running the same `site:` query, used
   when Google returns a /sorry/ CAPTCHA. Free, no consent dialog, no
   login wall.

The adapter only switches modes when the previous mode either was blocked
or returned zero parseable results, so a healthy bhw_direct response will
short-circuit before we ever ask Google.

Diagnostics:
  * `engine.last_status` carries `mode`, `url`, `title`, `body_len`,
    `block_reason` and (for site searches) the matched selector, similar
    to bing.py / reddit.py / twitter.py.
  * `engine.selector_counts()` returns per-selector counts for whichever
    mode was attempted last so test scripts can show why parsing missed.
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

BHW_HOME = "https://www.blackhatworld.com"

# XenForo 2 search-result containers, in priority order.
BHW_RESULT_SELECTORS = [
    "li.block-row.block-row--separated",
    "ol.block-body > li.block-row",
    "li.block-row",
    ".contentRow",
]

# Phrases that indicate BHW is hiding search results behind login / a captcha.
BHW_BLOCK_PHRASES = [
    "you must be logged in",
    "you must log in or register",
    "log in or sign up",
    "please log in to use the search",
    "register now",
    "verify you are human",
    "checking your browser",
    "cf-browser-verification",
    "access denied",
    "attention required",
    "rate limit",
    "too many requests",
]

# ----- Google site: --------------------------------------------------------

GOOGLE_DOMAINS = [
    "https://www.google.com",
    "https://www.google.co.uk",
    "https://www.google.ca",
]

GOOGLE_RESULT_SELECTORS = [
    "div.g",
    ".tF2Cxc",
    "div[data-sokoban-container]",
    "div.MjjYud",
]

GOOGLE_CONSENT_BUTTON_SELECTORS = [
    "button#L2AGLb",
    "button[aria-label*='Accept all' i]",
    "button[aria-label*='Accept All' i]",
    "button[aria-label*='Akzeptieren' i]",
    "button[aria-label*='Accepter' i]",
    "button[aria-label*='Aceptar' i]",
    "form[action*='consent'] button",
    "div[role='dialog'] button",
]

GOOGLE_BLOCK_PHRASES = [
    "unusual traffic",
    "our systems have detected",
    "before you continue",
    "to continue, please type",
    "captcha",
    "i'm not a robot",
    "automated queries",
    "sending automated requests",
]

# ----- DuckDuckGo HTML fallback -------------------------------------------

DDG_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"


def _abs_bhw(href: str) -> str:
    if not href:
        return href
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return BHW_HOME + href
    return BHW_HOME + "/" + href


def _clean_google_redirect(href: str) -> str:
    """Strip Google's /url?q=... redirect wrapper if present."""
    if not href:
        return href
    if href.startswith("/url?"):
        try:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            return qs.get("q", [href])[0]
        except Exception:
            return href
    return href


def _clean_ddg_redirect(href: str) -> str:
    """Decode DuckDuckGo's /l/?uddg=<encoded> wrapper."""
    if not href:
        return href
    if "uddg=" in href:
        try:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            return qs.get("uddg", [href])[0]
        except Exception:
            return href
    return href


class BlackHatWorldEngine(BaseEngine):
    name = "blackhatworld"
    max_retries = 3

    def __init__(self, page):
        super().__init__(page)
        # Diagnostics for callers / tests.
        self.last_status: dict = {}
        # Set after each attempt so selector_counts() can pick the right list.
        self._last_mode: str = "bhw_direct"

    # ------------------------------------------------------------------ search

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # 1) Try the native XenForo search first.
        try:
            results = self._try_bhw_direct(query, limit)
        except Exception as e:
            log.warning("[bhw] bhw_direct raised: %s", e)
            results = []
        if results:
            self._last_mode = "bhw_direct"
            return results

        # 2) Google site: search.
        try:
            results = self._try_google_site(query, limit)
        except Exception as e:
            log.warning("[bhw] google_site raised: %s", e)
            results = []
        if results:
            self._last_mode = "google_site"
            return results

        # 3) DuckDuckGo site: last-resort.
        try:
            results = self._try_ddg_site(query, limit)
        except Exception as e:
            log.warning("[bhw] ddg_site raised: %s", e)
            results = []
        if results:
            self._last_mode = "ddg_site"
            return results

        return []

    # --------------------------------------------------------- bhw direct mode

    def _try_bhw_direct(self, query: str, limit: int) -> list[SearchResult]:
        # Warm-up on the homepage so Cloudflare cookies settle.
        if safe_goto(self.page, BHW_HOME + "/", timeout=25000, retries=1):
            human_delay(1.5, 3.0)
            self._human_hints()

        q = urllib.parse.quote(query)
        url = f"{BHW_HOME}/search/?q={q}&o=relevance"
        log.info("[bhw] direct search: %s", url)
        if not safe_goto(self.page, url, timeout=30000):
            self.last_status = {"mode": "bhw_direct", "error": "goto_failed"}
            return []

        human_delay(1.8, 3.5)
        self._human_hints()

        if self._is_bhw_blocked():
            return []

        results = self._extract_bhw(limit)
        self._last_mode = "bhw_direct"
        if results:
            self.last_status["mode"] = "bhw_direct"
            self.last_status["count"] = len(results)
        return results

    def _is_bhw_blocked(self) -> bool:
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
            "mode": "bhw_direct",
            "url": url,
            "title": title,
            "body_len": len(body),
        }

        # Login redirect - XenForo bounces unauthenticated search.
        if "/login/" in url or "login_required" in url:
            self.last_status["block_reason"] = "login_redirect"
            log.warning("[bhw] login redirect: %s", url)
            return True

        # Sample only the first ~3KB so a thread that legitimately mentions
        # "log in or sign up" further down the page doesn't trip detection.
        head = body[:3000]
        for phrase in BHW_BLOCK_PHRASES:
            if phrase in head or phrase in title:
                self.last_status["block_reason"] = phrase
                log.warning("[bhw] block phrase detected: %r", phrase)
                return True

        # If the page rendered but we don't see ANY block-row / contentRow,
        # XenForo probably served the empty "search form" page instead of
        # results. Treat as a non-fatal miss so we fall through to Google.
        try:
            has_results = bool(
                self.page.query_selector("li.block-row")
                or self.page.query_selector(".contentRow")
            )
        except Exception:
            has_results = False
        if not has_results:
            self.last_status["block_reason"] = "no_results_container"
            log.info("[bhw] no result container on page; falling through")
            return True

        return False

    def _extract_bhw(self, limit: int) -> list[SearchResult]:
        items = []
        used = None
        for sel in BHW_RESULT_SELECTORS:
            try:
                items = self.page.query_selector_all(sel)
            except Exception:
                items = []
            if items:
                used = sel
                break
        if not items:
            log.info("[bhw] direct: no result selector matched")
            return []

        log.info("[bhw] direct using selector %s (%d items)", used, len(items))
        self.last_status["selector"] = used

        results: list[SearchResult] = []
        for r in items[: limit * 2]:
            # Title + URL: XenForo renders `h3.contentRow-title a` (or
            # sometimes `h3.title a` on legacy templates).
            title_el = (
                r.query_selector("h3.contentRow-title a")
                or r.query_selector(".contentRow-title a")
                or r.query_selector("h3.title a")
                or r.query_selector("a.PreviewTooltip")
            )
            if not title_el:
                continue
            try:
                title = (title_el.inner_text() or "").strip()
                href = title_el.get_attribute("href") or ""
            except Exception:
                continue
            href = _abs_bhw(href)

            # Snippet: `.contentRow-snippet` is the standard preview blurb.
            snippet_text = ""
            try:
                snip_el = r.query_selector(
                    ".contentRow-snippet, .listInline.listInline--bullet"
                )
                if snip_el:
                    snippet_text = (snip_el.inner_text() or "").strip()
            except Exception:
                snippet_text = ""

            # Author handle from `.contentRow-minor` / `.username`.
            author = ""
            try:
                user_el = (
                    r.query_selector(".contentRow-minor .username")
                    or r.query_selector("a.username")
                    or r.query_selector(".username")
                )
                if user_el:
                    author = (user_el.inner_text() or "").strip()
            except Exception:
                author = ""

            snippet_parts: list[str] = []
            if author:
                snippet_parts.append(f"by {author}")
            if snippet_text:
                snippet_parts.append(snippet_text)
            snippet = " · ".join(snippet_parts)

            if title and href:
                results.append(
                    SearchResult(title=title, url=href, snippet=snippet)
                )
            if len(results) >= limit:
                break

        return results

    # ------------------------------------------------------- google site mode

    def _try_google_site(self, query: str, limit: int) -> list[SearchResult]:
        domain = random.choice(GOOGLE_DOMAINS)

        # Warm-up on Google so consent / cookies settle.
        if safe_goto(self.page, domain + "/", timeout=20000, retries=1):
            human_delay(1.5, 3.0)
            self._handle_google_consent()
            self._human_hints()

        site_query = f"site:blackhatworld.com {query}"
        q = urllib.parse.quote(site_query)
        url = f"{domain}/search?q={q}&hl=en&num={max(limit, 10)}"
        log.info("[bhw] google site search: %s", url)
        if not safe_goto(self.page, url, timeout=30000):
            self.last_status = {"mode": "google_site", "error": "goto_failed"}
            return []

        human_delay(2.5, 4.5)
        self._handle_google_consent()
        self._human_hints()

        if self._is_google_blocked():
            return []

        results = self._extract_google(limit)
        self._last_mode = "google_site"
        if results:
            self.last_status["mode"] = "google_site"
            self.last_status["count"] = len(results)
        return results

    def _handle_google_consent(self):
        # Buttons in the top-level frame.
        for sel in GOOGLE_CONSENT_BUTTON_SELECTORS:
            try:
                btn = self.page.query_selector(sel)
                if btn:
                    btn.click(timeout=3000)
                    log.info("[bhw] google consent clicked (%s)", sel)
                    human_delay(1, 2)
                    return
            except Exception:
                continue
        # Buttons inside consent.google.com iframes.
        try:
            for frame in self.page.frames:
                furl = (frame.url or "").lower()
                if "consent" not in furl:
                    continue
                for sel in GOOGLE_CONSENT_BUTTON_SELECTORS:
                    try:
                        btn = frame.query_selector(sel)
                        if btn:
                            btn.click(timeout=3000)
                            log.info(
                                "[bhw] google consent (frame %s) %s", furl, sel
                            )
                            human_delay(1, 2)
                            return
                    except Exception:
                        continue
        except Exception:
            pass

    def _is_google_blocked(self) -> bool:
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
            "mode": "google_site",
            "url": url,
            "title": title,
            "body_len": len(body),
        }

        if "/sorry/" in url or "sorry" in title:
            self.last_status["block_reason"] = "sorry"
            log.warning("[bhw] google sorry page: %r", title)
            return True
        for phrase in GOOGLE_BLOCK_PHRASES:
            if phrase in body[:3000] or phrase in title:
                self.last_status["block_reason"] = phrase
                log.warning("[bhw] google block phrase: %r", phrase)
                return True
        return False

    def _extract_google(self, limit: int) -> list[SearchResult]:
        items = []
        used = None
        for sel in GOOGLE_RESULT_SELECTORS:
            try:
                items = self.page.query_selector_all(sel)
            except Exception:
                items = []
            if items:
                used = sel
                break

        if not items:
            log.info("[bhw] google: no result selector matched, h3 fallback")
            return self._extract_google_h3(limit)

        log.info("[bhw] google using selector %s (%d items)", used, len(items))
        self.last_status["selector"] = used

        results: list[SearchResult] = []
        for r in items[: limit * 3]:
            title_el = r.query_selector("h3")
            link_el = (
                r.query_selector("a[href^='http']")
                or r.query_selector("a[href^='/url']")
            )
            snippet_el = r.query_selector(
                ".VwiC3b, [data-sncf], .lEBKkf, span.aCOpRe"
            )

            try:
                title = (title_el.inner_text() or "").strip() if title_el else ""
            except Exception:
                title = ""
            try:
                href = link_el.get_attribute("href") if link_el else ""
            except Exception:
                href = ""
            try:
                snippet = (
                    (snippet_el.inner_text() or "").strip() if snippet_el else ""
                )
            except Exception:
                snippet = ""

            href = _clean_google_redirect(href or "")

            # Restrict to BHW results — the site: filter usually does this for
            # us, but Google sometimes injects related sitelinks.
            if not href or "blackhatworld.com" not in href.lower():
                continue
            if not title:
                continue

            # Try to surface the author when Google's snippet starts with
            # "<Username> · <date> · <body>".
            author = ""
            m = re.match(r"^([A-Za-z0-9_\-\.]{2,30})\s*[·•·]", snippet)
            if m:
                author = m.group(1).strip()

            snippet_parts: list[str] = []
            if author:
                snippet_parts.append(f"by {author}")
            if snippet:
                snippet_parts.append(snippet)
            full_snippet = " · ".join(snippet_parts) if snippet_parts else snippet

            results.append(
                SearchResult(title=title, url=href, snippet=full_snippet)
            )
            if len(results) >= limit:
                break

        return results

    def _extract_google_h3(self, limit: int) -> list[SearchResult]:
        results: list[SearchResult] = []
        try:
            h3s = self.page.query_selector_all("#search h3, #rso h3")
        except Exception:
            h3s = []
        for h3 in h3s[: limit * 2]:
            try:
                title = (h3.inner_text() or "").strip()
            except Exception:
                continue
            try:
                href = self.page.evaluate(
                    "(el) => { let p = el; while(p) { if(p.tagName === 'A' && p.href) return p.href; p = p.parentElement; } return ''; }",
                    h3,
                )
            except Exception:
                href = ""
            href = _clean_google_redirect(href or "")
            if title and href and "blackhatworld.com" in href.lower():
                results.append(SearchResult(title=title, url=href))
            if len(results) >= limit:
                break
        return results

    # ------------------------------------------------------- ddg site mode

    def _try_ddg_site(self, query: str, limit: int) -> list[SearchResult]:
        site_query = f"site:blackhatworld.com {query}"
        q = urllib.parse.quote(site_query)
        url = f"{DDG_HTML_ENDPOINT}?q={q}"
        log.info("[bhw] ddg site search: %s", url)
        if not safe_goto(self.page, url, timeout=25000, retries=1):
            self.last_status = {"mode": "ddg_site", "error": "goto_failed"}
            return []

        human_delay(1.0, 2.0)
        self._human_hints()

        try:
            url_now = (self.page.url or "").lower()
            title_now = (self.page.title() or "").lower()
            body_now = self.page.inner_text("body").lower()
        except Exception:
            url_now = title_now = body_now = ""

        self.last_status = {
            "mode": "ddg_site",
            "url": url_now,
            "title": title_now,
            "body_len": len(body_now),
        }

        results: list[SearchResult] = []
        try:
            items = self.page.query_selector_all(".result")
        except Exception:
            items = []
        log.info("[bhw] ddg got %d .result items", len(items))
        self.last_status["selector"] = ".result"

        for r in items[: limit * 3]:
            title_el = r.query_selector(".result__a")
            snippet_el = r.query_selector(".result__snippet")
            try:
                title = (
                    (title_el.inner_text() or "").strip() if title_el else ""
                )
                href = (
                    (title_el.get_attribute("href") or "") if title_el else ""
                )
                snippet = (
                    (snippet_el.inner_text() or "").strip()
                    if snippet_el
                    else ""
                )
            except Exception:
                continue
            href = _clean_ddg_redirect(href)
            if not title or not href:
                continue
            if "blackhatworld.com" not in href.lower():
                continue
            results.append(SearchResult(title=title, url=href, snippet=snippet))
            if len(results) >= limit:
                break

        if results:
            self.last_status["count"] = len(results)
        self._last_mode = "ddg_site"
        return results

    # -------------------------------------------------------------- diagnostics

    def selector_counts(self) -> dict[str, int]:
        """Return per-selector counts for whichever mode was attempted last."""
        counts: dict[str, int] = {}
        sel_lists = {
            "bhw_direct": BHW_RESULT_SELECTORS,
            "google_site": GOOGLE_RESULT_SELECTORS,
            "ddg_site": [".result"],
        }
        for sel in sel_lists.get(self._last_mode, BHW_RESULT_SELECTORS):
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1

        # Always also report a few generic selectors useful for diagnostics
        # regardless of mode.
        for sel in (
            "h3.contentRow-title a",
            ".contentRow-snippet",
            "a.username",
            "#search h3",
            "#rso h3",
            ".result__a",
        ):
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        return counts

    # ------------------------------------------------------------------ helpers

    def _human_hints(self):
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
        time.sleep(random.uniform(0.3, 0.8))
