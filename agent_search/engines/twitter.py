"""Twitter/X search adapter with Nitter mirror fallback.

x.com search requires login for full results, so this adapter prefers public
Nitter mirrors (Twitter front-ends that render server-side HTML and don't
require authentication). It walks a priority list of mirrors and falls back
to x.com / twitter.com search as a last resort.

Strategy:
1. For each Nitter mirror in NITTER_INSTANCES:
     a. Visit `<base>/search?f=tweets&q=<query>`.
     b. Parse `.timeline-item` / `.tweet-link` / `.tweet-content` blocks.
     c. Skip mirrors that return zero matches, are gated, or 5xx.
2. If every Nitter mirror fails, try x.com/search (and twitter.com/search)
   and try to parse `article[data-testid="tweet"]` blocks. These usually
   require auth, so we accept that this branch may return [] often.
3. Detect typical block / rate-limit / "instance has been rate limited"
   messages on Nitter mirrors and skip them.

Diagnostics:
- `engine.last_status` carries the URL / title / body length / which mirror
  was used / which selector matched, similar to bing.py / reddit.py.
- `engine.selector_counts()` returns per-selector counts on the current page
  so test scripts can show why parsing missed.
"""

import logging
import random
import re
import time
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

# Public Nitter mirrors. Order matters: most-reliable first. xcancel.com is a
# Nitter fork that has stayed up the longest as of 2025/2026; the rest are
# kept as best-effort fallbacks. We try them in order until one returns
# parseable results.
NITTER_INSTANCES = [
    "https://xcancel.com",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://nitter.net",
    "https://nitter.tiekoetter.com",
    "https://nitter.salastil.com",
]

# x.com / twitter.com fallbacks (require login for most queries; tried last).
X_FALLBACKS = [
    "https://x.com/search?q={q}&src=typed_query&f=top",
    "https://twitter.com/search?q={q}&src=typed_query&f=top",
]

# Nitter result containers, in priority order.
NITTER_RESULT_SELECTORS = [
    ".timeline-item",
    "div.timeline-item",
    ".timeline > .timeline-item",
]

# x.com result containers (post-login UI; rarely reachable without auth).
X_RESULT_SELECTORS = [
    'article[data-testid="tweet"]',
    'article[role="article"]',
    "article",
]

# Phrases that indicate a Nitter mirror is dead / rate-limited / Cloudflared.
NITTER_BLOCK_PHRASES = [
    "instance has been rate limited",
    "instance is rate limited",
    "instance has been blocked",
    "tweets are temporarily unavailable",
    "tweets unavailable",
    "no results",
    "unable to retrieve",
    "error processing your request",
    "cf-error-details",
    "checking your browser",
    "verify you are human",
    "502 bad gateway",
    "503 service unavailable",
    "504 gateway timeout",
    "this account doesn't exist",
]

# Phrases that indicate x.com is forcing login / blocking.
X_BLOCK_PHRASES = [
    "log in to x",
    "sign in to x",
    "log in to twitter",
    "sign in to twitter",
    "something went wrong. try reloading",
    "rate limit exceeded",
    "verify you are human",
    "javascript is not available",
    "we've detected that javascript is disabled",
]


def _abs_url(href: str, base: str) -> str:
    """Make a URL absolute against `base` if it's a path."""
    if not href:
        return href
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return base.rstrip("/") + href
    return base.rstrip("/") + "/" + href


def _rewrite_nitter_link_to_x(href: str) -> str:
    """Rewrite a Nitter status URL to its x.com equivalent.

    Nitter exposes tweets at `/<user>/status/<id>` on the mirror's host. We
    want the `SearchResult.url` to point at the canonical x.com URL so the
    consumer can open / share it without depending on the mirror.
    """
    if not href:
        return href
    try:
        parsed = urllib.parse.urlparse(href)
    except Exception:
        return href
    if "/status/" not in parsed.path:
        return href
    # Drop `#m` fragment Nitter appends and rewrite host to x.com.
    return urllib.parse.urlunparse(
        ("https", "x.com", parsed.path, "", "", "")
    )


class TwitterEngine(BaseEngine):
    name = "twitter"
    max_retries = 3

    def __init__(self, page):
        super().__init__(page)
        # Diagnostics for callers / tests.
        self.last_status: dict = {}
        # Set after a successful _do_search so selector_counts() can pick the
        # right list. Defaults to the Nitter set since we try Nitter first.
        self._last_mode: str = "nitter"

    # ------------------------------------------------------------------ search

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        q = urllib.parse.quote(query)

        # Phase 0: if we're logged into x.com (auth_token cookie present in
        # the context), prefer x.com directly — logged-in search returns
        # richer fields (full reply chain, view counts, quote tweets) that
        # Nitter mirrors can't provide. This kicks in after the user has
        # run `agentsearch login twitter` and passes --profile twitter.
        if self._has_x_login():
            log.info("[twitter] x.com auth_token detected — preferring x.com")
            for tmpl in X_FALLBACKS:
                results = self._try_x(tmpl.format(q=q), limit)
                if results:
                    self._last_mode = "x_authed"
                    return results
            log.info("[twitter] authed x.com returned nothing — falling back to Nitter")

        # Phase 1: try every Nitter mirror in order.
        for base in NITTER_INSTANCES:
            results = self._try_nitter(base, q, limit)
            if results:
                self._last_mode = "nitter"
                return results

        # Phase 2: x.com / twitter.com fallback (anonymous, often empty).
        for tmpl in X_FALLBACKS:
            results = self._try_x(tmpl.format(q=q), limit)
            if results:
                self._last_mode = "x"
                return results

        return []

    # -------------------------------------------------------- login detection

    def _has_x_login(self) -> bool:
        """Check whether the current browser context carries an x.com login.

        Looks for the ``auth_token`` cookie scoped to x.com / twitter.com.
        Only present when the user has signed in (e.g., via
        ``agentsearch login twitter``) and the calling CLI has wired the
        same persistent profile through ``--profile twitter``.
        """
        try:
            ctx = self.page.context
            cookies = ctx.cookies(["https://x.com", "https://twitter.com"])
        except Exception:
            return False
        for c in cookies or []:
            name = c.get("name") if isinstance(c, dict) else None
            if name == "auth_token":
                return True
        return False

    # ------------------------------------------------------------------ nitter

    def _try_nitter(
        self, base: str, q_encoded: str, limit: int
    ) -> list[SearchResult]:
        """Hit a single Nitter mirror and try to parse results."""
        url = f"{base}/search?f=tweets&q={q_encoded}"
        log.info("[twitter] trying nitter mirror %s", url)
        if not safe_goto(self.page, url, timeout=25000, retries=1):
            self.last_status = {"mirror": base, "error": "goto_failed"}
            return []

        human_delay(1.5, 3.0)
        self._human_hints()

        if self._is_nitter_blocked(base):
            return []

        results = self._extract_nitter(base, limit)
        if results:
            self.last_status["mirror"] = base
            self.last_status["mode"] = "nitter"
            self.last_status["count"] = len(results)
        return results

    def _is_nitter_blocked(self, base: str) -> bool:
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
            "mirror": base,
            "url": url,
            "title": title,
            "body_len": len(body),
            "mode": "nitter",
        }

        # Body shorter than a few hundred chars on a Nitter search page almost
        # always means an empty / error template was rendered.
        if len(body) < 200:
            self.last_status["block_reason"] = "empty_body"
            log.warning(
                "[twitter] nitter mirror %s returned tiny body (%d chars)",
                base, len(body),
            )
            return True

        # Snip to first ~3KB so a tweet that happens to contain a phrase like
        # "rate limit" doesn't trip the detector once we're past the header.
        head = body[:3000]
        for phrase in NITTER_BLOCK_PHRASES:
            if phrase in head or phrase in title:
                # "no results" is genuinely a no-result response — skip the
                # mirror but don't treat it as blocked across the board.
                if phrase == "no results":
                    self.last_status["block_reason"] = "no_results"
                    log.info("[twitter] nitter mirror %s: no results", base)
                    return True
                self.last_status["block_reason"] = phrase
                log.warning(
                    "[twitter] nitter mirror %s blocked: %r", base, phrase
                )
                return True
        return False

    def _extract_nitter(self, base: str, limit: int) -> list[SearchResult]:
        items = []
        used = None
        for sel in NITTER_RESULT_SELECTORS:
            try:
                items = self.page.query_selector_all(sel)
            except Exception:
                items = []
            if items:
                used = sel
                break
        if not items:
            log.info("[twitter] nitter %s matched no result selectors", base)
            return []

        log.info(
            "[twitter] nitter %s using selector %s (%d items)",
            base, used, len(items),
        )

        results: list[SearchResult] = []
        for r in items[: limit * 3]:  # over-fetch; some are pinned / replies
            # Tweet text.
            try:
                text_el = r.query_selector(".tweet-content")
                text = (text_el.inner_text() or "").strip() if text_el else ""
            except Exception:
                text = ""

            # Tweet permalink: prefer `.tweet-link` (wraps the whole card),
            # then any anchor with /status/ in the href.
            href = ""
            try:
                link_el = r.query_selector(".tweet-link")
                if link_el:
                    href = link_el.get_attribute("href") or ""
                if not href:
                    a = r.query_selector('a[href*="/status/"]')
                    if a:
                        href = a.get_attribute("href") or ""
            except Exception:
                href = ""
            href = _abs_url(href, base)
            href = _rewrite_nitter_link_to_x(href)

            # Author handle (e.g. "@elonmusk").
            handle = ""
            try:
                user_el = (
                    r.query_selector(".username")
                    or r.query_selector(".tweet-header .username")
                )
                if user_el:
                    handle = (user_el.inner_text() or "").strip()
            except Exception:
                handle = ""

            # Display name.
            name = ""
            try:
                name_el = r.query_selector(".fullname")
                if name_el:
                    name = (name_el.inner_text() or "").strip()
            except Exception:
                name = ""

            # Engagement counters (replies / retweets / likes). Nitter exposes
            # them as `.tweet-stats .icon-container` blocks containing the
            # count text.
            score = self._parse_nitter_stats(r)

            if not text and not href:
                continue

            title_bits = [b for b in (name, handle) if b]
            title = " ".join(title_bits) if title_bits else (text[:80] if text else "tweet")
            if text and title and text[: len(title)] != title:
                # Keep title short and useful; if no name/handle, fall back to
                # the first line of the tweet.
                if len(title) > 100:
                    title = title[:100]
            elif not title_bits and text:
                title = text.splitlines()[0][:100]

            snippet_parts: list[str] = []
            if handle:
                snippet_parts.append(handle)
            if score is not None:
                snippet_parts.append(f"♥ {score}")
            if text:
                snippet_parts.append(text)
            snippet = " · ".join(snippet_parts)

            results.append(
                SearchResult(title=title, url=href, snippet=snippet, score=score)
            )
            if len(results) >= limit:
                break
        return results

    def _parse_nitter_stats(self, r) -> int | None:
        """Best-effort like-count parse from Nitter's `.tweet-stats` block."""
        try:
            stats = r.query_selector(".tweet-stats")
            if not stats:
                return None
            text = (stats.inner_text() or "").strip()
        except Exception:
            return None
        if not text:
            return None
        # Nitter shows up to four counters separated by whitespace. Take the
        # last numeric token (likes are rendered last in the default theme).
        tokens = re.findall(r"[\d.,]+\s*[KkMm]?", text)
        if not tokens:
            return None
        raw = tokens[-1].strip().lower().replace(",", "")
        mult = 1
        if raw.endswith("k"):
            mult = 1_000
            raw = raw[:-1]
        elif raw.endswith("m"):
            mult = 1_000_000
            raw = raw[:-1]
        try:
            return int(float(raw) * mult)
        except ValueError:
            return None

    # --------------------------------------------------------------- x.com

    def _try_x(self, url: str, limit: int) -> list[SearchResult]:
        log.info("[twitter] trying x.com fallback %s", url)
        if not safe_goto(self.page, url, timeout=30000, retries=1):
            self.last_status = {"mode": "x", "error": "goto_failed", "url": url}
            return []

        human_delay(2.5, 4.5)
        self._human_hints()

        if self._is_x_blocked():
            return []

        # Give the SPA a moment to hydrate articles.
        for _ in range(3):
            try:
                if self.page.query_selector('article[data-testid="tweet"]'):
                    break
            except Exception:
                pass
            human_delay(1.0, 2.0)

        return self._extract_x(limit)

    def _is_x_blocked(self) -> bool:
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
            "mode": "x",
        }

        # Login wall redirects.
        if "/i/flow/login" in url or "/login" in url:
            self.last_status["block_reason"] = "login_redirect"
            log.warning("[twitter] x.com login redirect: %s", url)
            return True

        for phrase in X_BLOCK_PHRASES:
            if phrase in body[:3000] or phrase in title:
                self.last_status["block_reason"] = phrase
                log.warning("[twitter] x.com block phrase: %r", phrase)
                return True
        return False

    def _extract_x(self, limit: int) -> list[SearchResult]:
        items = []
        used = None
        for sel in X_RESULT_SELECTORS:
            try:
                items = self.page.query_selector_all(sel)
            except Exception:
                items = []
            if items:
                used = sel
                break
        if not items:
            log.info("[twitter] x.com matched no article selectors")
            return []

        log.info("[twitter] x.com using selector %s (%d items)", used, len(items))

        results: list[SearchResult] = []
        for a in items[: limit * 2]:
            try:
                text_el = a.query_selector('[data-testid="tweetText"]')
                text = (text_el.inner_text() or "").strip() if text_el else ""
            except Exception:
                text = ""

            href = ""
            try:
                link_el = a.query_selector('a[href*="/status/"]')
                if link_el:
                    href = link_el.get_attribute("href") or ""
            except Exception:
                href = ""
            href = _abs_url(href, "https://x.com")

            handle = ""
            try:
                u_el = a.query_selector(
                    '[data-testid="User-Name"] a[href^="/"]'
                )
                if u_el:
                    handle = (u_el.inner_text() or "").strip()
            except Exception:
                handle = ""

            if not text and not href:
                continue

            title = handle or (text[:80] if text else "tweet")
            snippet_parts = []
            if handle:
                snippet_parts.append(handle)
            if text:
                snippet_parts.append(text)
            snippet = " · ".join(snippet_parts)

            results.append(SearchResult(title=title, url=href, snippet=snippet))
            if len(results) >= limit:
                break
        return results

    # -------------------------------------------------------------- diagnostics

    def selector_counts(self) -> dict[str, int]:
        """Return the number of elements each result selector matches.

        Picks Nitter selectors when the last successful (or attempted) mode
        was Nitter; otherwise falls back to x.com selectors. Always also
        returns generic counters (`article`, `.tweet-content`, etc.) so the
        test output is useful even when a mirror returns an unexpected layout.
        """
        counts: dict[str, int] = {}
        sel_lists = {
            "nitter": NITTER_RESULT_SELECTORS,
            "x": X_RESULT_SELECTORS,
        }
        for sel in sel_lists.get(self._last_mode, NITTER_RESULT_SELECTORS):
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        for sel in (
            ".tweet-content",
            ".tweet-link",
            ".username",
            'article[data-testid="tweet"]',
            '[data-testid="tweetText"]',
        ):
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        return counts

    # ------------------------------------------------------------------ helpers

    def _human_hints(self):
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
