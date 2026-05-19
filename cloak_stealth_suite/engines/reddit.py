"""Reddit search adapter targeting old.reddit.com.

Features:
1. Visits old.reddit.com/search?q=...&sort=relevance&t=all with a homepage
   warm-up so cookies / consent settle before the search request.
2. Parses .search-result-link entries and extracts:
     * title (a.search-title)
     * url (a.search-title href, normalized to absolute)
     * score (.search-score "N points", with k/m suffix support)
     * subreddit + body snippet rolled into the SearchResult.snippet field.
3. Falls back to .thing entries (data-score, .title a.title) when the
   search-result-link layout is unavailable.
4. Detects "you're doing that too much" / "too many requests" / Cloudflare
   interstitials and returns [] so the BaseEngine retry loop kicks in.
5. Best-effort dismissal of any login / signup banner. old.reddit.com rarely
   pops a hard login wall, but we cover the dismiss buttons that show up on
   redesigned redirects so we don't block on them.
"""

import logging
import random
import re
import time
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

OLD_REDDIT = "https://old.reddit.com"
# www.reddit.com triggers a tiny `js_challenge` flow on first hit and sets
# cookies that allow subsequent old.reddit.com requests to bypass the
# "you've been blocked by network security" gate. We use it as a warm-up only.
WARMUP_URL = "https://www.reddit.com/"

# Old-reddit search-result containers, in priority order.
RESULT_SELECTORS = [
    "div.search-result.search-result-link",
    ".search-result-link",
    "div.contents > div.search-result",
]

# Old-reddit "thing" listing fallback (used by some result variants).
THING_SELECTORS = [
    ".search-result-listing .thing.link",
    ".thing.link",
    ".search-result-group .thing",
]

# Phrases that indicate Reddit blocked us / rate-limited / Cloudflare gate.
BLOCK_PHRASES = [
    "you're doing that too much",
    "you are doing that too much",
    "try again in",
    "too many requests",
    "rate limit",
    "rate-limit",
    "verify you are human",
    "checking your browser",
    "access denied",
    "forbidden",
    "sorry, this content is unavailable",
    "blocked",
]

# Buttons to click to dismiss any login / signup interstitial that might
# show up if we get redirected to www.reddit.com.
LOGIN_DISMISS_SELECTORS = [
    "button[aria-label='Close']",
    "button[aria-label*='Close' i]",
    "button[aria-label*='close' i]",
    "[data-testid='close-button']",
    "shreddit-async-loader button[aria-label*='close' i]",
    ".close-button",
    "button.close-button",
]


def _parse_score(text: str) -> int | None:
    """Parse '1234 points' / '1.2k points' / '5' / '-3' into int.

    Handles 'k' / 'm' suffixes (e.g. '1.2k points' -> 1200).
    Returns None when the text doesn't look like a score.
    """
    if not text:
        return None
    t = text.strip().lower()

    # Form 1: "<num>[k|m] points"
    m = re.search(r"(-?\d[\d,]*\.?\d*\s*[km]?)\s*point", t)
    raw = m.group(1).strip() if m else None

    # Form 2: bare integer (e.g. data-score="123")
    if raw is None:
        m2 = re.fullmatch(r"-?\d[\d,]*", t)
        if m2:
            raw = m2.group(0)

    if raw is None:
        return None

    raw = raw.replace(",", "").strip()
    mult = 1
    if raw.endswith("k"):
        mult = 1_000
        raw = raw[:-1].strip()
    elif raw.endswith("m"):
        mult = 1_000_000
        raw = raw[:-1].strip()
    try:
        return int(float(raw) * mult)
    except ValueError:
        return None


class RedditEngine(BaseEngine):
    name = "reddit"
    max_retries = 4  # Reddit has aggressive rate limiting

    def __init__(self, page):
        super().__init__(page)
        # Diagnostics for callers / tests.
        self.last_status: dict = {}

    # ------------------------------------------------------------------ search

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # Warm-up: visit www.reddit.com so the js_challenge cookie lands.
        # Without this, old.reddit.com returns "you've been blocked by network
        # security" (an Akamai-style edge gate). With the cookie set, the same
        # /search URL returns the normal search-result-link layout.
        if safe_goto(self.page, WARMUP_URL, timeout=25000, retries=1):
            human_delay(3.0, 5.0)  # let the js_challenge issue + redirect settle
            self._dismiss_login()
            self._human_hints()

        q = urllib.parse.quote(query)
        url = f"{OLD_REDDIT}/search?q={q}&sort=relevance&t=all"
        log.info("[reddit] navigating to %s", url)
        if not safe_goto(self.page, url, timeout=30000):
            return []

        human_delay(2.0, 4.0)
        self._dismiss_login()
        self._human_hints()

        if self._is_blocked():
            return []

        results = self._extract_search_results(limit)
        if results:
            return results

        # Fallback: parse .thing entries.
        return self._extract_thing_results(limit)

    # -------------------------------------------------------------- diagnostics

    def selector_counts(self) -> dict[str, int]:
        """Return the number of elements each result selector matches."""
        counts: dict[str, int] = {}
        for sel in RESULT_SELECTORS + THING_SELECTORS:
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        for sel in ("a.search-title", ".search-score", ".thing"):
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

    def _dismiss_login(self):
        """Best-effort dismissal of login / sign-up banners and modals."""
        for sel in LOGIN_DISMISS_SELECTORS:
            try:
                btn = self.page.query_selector(sel)
                if btn:
                    btn.click(timeout=2000)
                    log.info("[reddit] dismissed login modal (%s)", sel)
                    human_delay(0.4, 0.9)
                    return
            except Exception:
                continue

    def _is_blocked(self) -> bool:
        """Detect rate-limit / Cloudflare / blocked interstitials."""
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

        for phrase in BLOCK_PHRASES:
            if phrase in title or phrase in body[:2000]:
                # Limit the body window so a long results page that happens to
                # contain "blocked" inside a comment doesn't trip the detector.
                log.warning("[reddit] block phrase detected: %r", phrase)
                self.last_status["block_reason"] = phrase
                return True
        return False

    # ---------------------------------------------------------------- extraction

    def _extract_search_results(self, limit: int) -> list[SearchResult]:
        """Primary extractor: .search-result-link entries."""
        items = []
        used = None
        for sel in RESULT_SELECTORS:
            try:
                items = self.page.query_selector_all(sel)
            except Exception:
                items = []
            if items:
                used = sel
                break
        if not items:
            return []

        log.info("[reddit] using selector %s (%d items)", used, len(items))
        results: list[SearchResult] = []
        for r in items[: limit * 2]:
            title_el = (
                r.query_selector("a.search-title")
                or r.query_selector("a.search-link")
            )
            if not title_el:
                continue

            try:
                title = (title_el.inner_text() or "").strip()
            except Exception:
                title = ""
            try:
                href = title_el.get_attribute("href") or ""
            except Exception:
                href = ""
            if href.startswith("/"):
                href = OLD_REDDIT + href

            # Score lives in .search-score (text like "1234 points").
            score = None
            try:
                score_el = r.query_selector(".search-score")
                if score_el:
                    score = _parse_score((score_el.inner_text() or "").strip())
            except Exception:
                pass
            if score is None:
                # Fallback: regex over the whole result block.
                try:
                    full_text = (r.inner_text() or "").strip()
                    m = re.search(
                        r"([\d.,]+\s*[km]?)\s*points?", full_text, re.I
                    )
                    if m:
                        score = _parse_score(m.group(0))
                except Exception:
                    pass

            subreddit = ""
            try:
                sr_el = r.query_selector(
                    ".search-subreddit-link, a.search-subreddit-link"
                )
                if sr_el:
                    subreddit = (sr_el.inner_text() or "").strip()
            except Exception:
                subreddit = ""

            body = ""
            try:
                body_el = r.query_selector(".search-result-body, .md")
                if body_el:
                    body = (body_el.inner_text() or "").strip()
            except Exception:
                body = ""

            snippet_parts: list[str] = []
            if subreddit:
                snippet_parts.append(subreddit)
            if score is not None:
                snippet_parts.append(f"{score} points")
            if body:
                snippet_parts.append(body)
            snippet = " · ".join(snippet_parts)

            if title and href:
                results.append(
                    SearchResult(
                        title=title, url=href, snippet=snippet, score=score
                    )
                )
            if len(results) >= limit:
                break
        return results

    def _extract_thing_results(self, limit: int) -> list[SearchResult]:
        """Fallback extractor: .thing entries with data-score."""
        items = []
        used = None
        for sel in THING_SELECTORS:
            try:
                items = self.page.query_selector_all(sel)
            except Exception:
                items = []
            if items:
                used = sel
                break
        if not items:
            return []

        log.info("[reddit] using thing selector %s (%d items)", used, len(items))
        results: list[SearchResult] = []
        for t in items[: limit * 2]:
            try:
                link = (
                    t.query_selector("a.search-title")
                    or t.query_selector("a.title")
                    or t.query_selector("p.title a")
                )
            except Exception:
                link = None
            if not link:
                continue

            try:
                title = (link.inner_text() or "").strip()
                href = link.get_attribute("href") or ""
            except Exception:
                continue
            if href.startswith("/"):
                href = OLD_REDDIT + href

            score = None
            try:
                ds = t.get_attribute("data-score")
                if ds:
                    score = _parse_score(ds)
            except Exception:
                pass
            if score is None:
                try:
                    score_el = t.query_selector(".score.unvoted, .score")
                    if score_el:
                        score = _parse_score(
                            (score_el.inner_text() or "").strip()
                        )
                except Exception:
                    pass

            subreddit = ""
            try:
                sr = t.get_attribute("data-subreddit-prefixed")
                if sr:
                    subreddit = sr
            except Exception:
                pass

            snippet_parts: list[str] = []
            if subreddit:
                snippet_parts.append(subreddit)
            if score is not None:
                snippet_parts.append(f"{score} points")
            snippet = " · ".join(snippet_parts)

            if title and href:
                results.append(
                    SearchResult(
                        title=title, url=href, snippet=snippet, score=score
                    )
                )
            if len(results) >= limit:
                break
        return results
