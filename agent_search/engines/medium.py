"""Medium search adapter.

Medium's web search at ``https://medium.com/search?q=<q>`` is a heavily
JS-rendered Next.js application. The page renders article cards
asynchronously and aggressively gates suspected bots behind a
"Verifying you are human" Cloudflare-style interstitial. To stay
robust we layer two modes (mirroring ``github_search.py``):

1. **medium_direct** — Navigate to ``medium.com/search?q=<q>`` and
   scrape the rendered article cards. Selectors vary between layout
   revisions so we probe a list of known candidates and record which
   one matched on ``last_status['selector']``.

2. **ddg_site** — Last-resort fallback through the HTML-only
   DuckDuckGo endpoint with ``site:medium.com <query>``. We can pull
   title + URL + a coarse snippet, but no author / read-time / claps.

Each mode short-circuits on success. If a mode returns 0 parseable
results (or is rate-limited / blocked) the adapter falls through.

``SearchResult`` (see ``base.py``) carries ``title`` / ``url`` /
``snippet`` / ``score``. To preserve Medium-specific metadata:

* ``score`` holds the integer claps count when extractable, ``None``
  otherwise (Medium often hides claps for non-logged-in users).
* ``snippet`` is composed as
  ``"by <author> · <publication> · <read_time> · <member>"`` with
  missing parts dropped and the separator collapsing cleanly.
* Every returned ``SearchResult`` has the following attributes set
  dynamically (the dataclass has no ``__slots__`` so this is
  supported):

  - ``author``       (str) — author handle / display name.
  - ``author_url``   (str) — link to the author's profile (if found).
  - ``publication``  (str) — publication name (e.g. "Better Programming").
  - ``read_time``    (str) — "X min read" string as Medium renders it.
  - ``claps``        (int | None) — same as ``score``.
  - ``member_only``  (bool) — True iff the article is gated behind
    Medium's paywall ("Member-only story" badge present).
  - ``published_at`` (str) — date string as rendered ("May 12" /
    "May 12, 2024" / "1 day ago"); empty when not parseable.

Diagnostics
-----------

* ``engine.last_status`` — ``mode``, ``url``, ``title``, ``body_len``,
  optional ``selector`` / ``block_reason`` / ``count``.
* ``engine.selector_counts()`` — per-selector counts useful across
  both modes so test scripts can show why parsing missed.
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

MEDIUM_HOME = "https://medium.com"

# ---- medium_direct ---------------------------------------------------------

# Article container selectors for medium.com/search, in priority order.
# Medium has revised this markup multiple times across the Next.js app
# rewrites; we list the variants we've observed so the engine survives
# layout flips. The first entry that produces hits is recorded on
# ``last_status['selector']``.
DIRECT_RESULT_SELECTORS = [
    'article',                                  # current default
    'div[data-testid="storyPreview"]',          # observed Q1 2024
    'div[role="link"][data-testid]',            # SPA card variant
    'div.streamItem',                           # legacy list view
    'div.postArticle',                          # very old layout
]

# Phrases that indicate Medium / Cloudflare blocked us.
BLOCK_PHRASES = [
    "verify you are human",
    "verifying you are human",
    "checking your browser",
    "just a moment",
    "cf-browser-verification",
    "attention required",
    "access denied",
    "rate limit",
    "too many requests",
    "human verification",
    "enable javascript and cookies",
    "request unsuccessful",
    "are you a robot",
]

# ---- ddg_site --------------------------------------------------------------

DDG_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"

# Rough cap on how many DOM cards we'll inspect per page; Medium's
# search lazy-loads more on scroll, but we don't need a deep page.
MAX_CARDS_TO_SCAN = 80


# ----------------------------------------------------------------------------


def _abs_medium(href: str) -> str:
    """Normalize a relative Medium URL to an absolute one."""
    if not href:
        return href
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return MEDIUM_HOME + href
    return MEDIUM_HOME + "/" + href


def _strip_url_query(href: str) -> str:
    """Drop tracking params (``?source=...``) for stable de-duping."""
    if not href:
        return href
    return href.split("?", 1)[0].split("#", 1)[0]


def _parse_int(text: str) -> int | None:
    """Parse a claps / count string ('123', '1.2K', '4.5M', '1,234')."""
    if not text:
        return None
    t = text.strip().lower().replace(",", "")
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*([km])?", t)
    if not m:
        return None
    num = float(m.group(1))
    suffix = m.group(2)
    if suffix == "k":
        num *= 1_000
    elif suffix == "m":
        num *= 1_000_000
    try:
        return int(num)
    except (ValueError, OverflowError):
        return None


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


_READ_TIME_RE = re.compile(r"\b(\d+)\s*min\s*read\b", re.I)
_DATE_RE = re.compile(
    r"\b("
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}"
    r"(?:,\s*\d{4})?"
    r"|\d+\s+(?:second|minute|hour|day|week|month|year)s?\s+ago"
    r")\b",
    re.I,
)


def _extract_read_time(text: str) -> str:
    """Pull the ``X min read`` phrase out of free-form card text."""
    if not text:
        return ""
    m = _READ_TIME_RE.search(text)
    if not m:
        return ""
    return f"{m.group(1)} min read"


def _extract_published_at(text: str) -> str:
    """Pull a date / relative-time phrase out of free-form card text."""
    if not text:
        return ""
    m = _DATE_RE.search(text)
    if not m:
        return ""
    return m.group(1)


def _is_member_only(text: str) -> bool:
    """Heuristically decide if the card text indicates a paywalled story."""
    if not text:
        return False
    low = text.lower()
    # Medium's paywall badge text varies by locale / experiment but
    # always contains the literal "member-only" or "members only".
    return (
        "member-only" in low
        or "members only" in low
        or "member only story" in low
    )


def _looks_like_article_url(href: str) -> bool:
    """Heuristic: is this a Medium article URL (vs author / tag page)?"""
    if not href:
        return False
    # Drop query / hash before pattern checks.
    path = urllib.parse.urlparse(href).path or ""
    if not path:
        return False
    # Reject obvious non-article paths.
    bad_prefixes = (
        "/search", "/m/", "/_/", "/tag/", "/topics/", "/about", "/membership",
        "/p/", "/me/",
    )
    for bad in bad_prefixes:
        if path.startswith(bad):
            return False
    # Article URLs end in a "-<hashId>" slug (12 hex chars). Both
    # ``/<slug>-<id>`` and ``/@author/<slug>-<id>`` are valid.
    if re.search(r"-[0-9a-f]{6,}/?$", path):
        return True
    # Some URLs use a ``?source=...`` dance after the slug; the path
    # check above already strips query.
    return False


def _compose_snippet(
    author: str,
    publication: str,
    read_time: str,
    member_only: bool,
) -> str:
    """Render ``'by <author> · <pub> · <read> · Member-only'``;
    parts omitted when empty."""
    parts: list[str] = []
    if author:
        parts.append(f"by {author}")
    if publication:
        parts.append(publication)
    if read_time:
        parts.append(read_time)
    if member_only:
        parts.append("Member-only")
    return " · ".join(parts)


def _attach_extras(
    r: SearchResult,
    *,
    author: str,
    author_url: str,
    publication: str,
    read_time: str,
    claps: int | None,
    member_only: bool,
    published_at: str,
) -> SearchResult:
    r.author = author
    r.author_url = author_url
    r.publication = publication
    r.read_time = read_time
    r.claps = claps
    r.member_only = member_only
    r.published_at = published_at
    return r


# ----------------------------------------------------------------------------


class MediumSearchEngine(BaseEngine):
    """Search Medium for articles."""

    name = "medium"
    max_retries = 3

    _MODE_ORDER: tuple[str, ...] = ("medium_direct", "ddg_site")

    def __init__(self, page):
        super().__init__(page)
        # Diagnostics for callers / tests.
        self.last_status: dict = {}
        self._last_mode: str = self._MODE_ORDER[0]
        self._pages_fetched: int = 0

    # ------------------------------------------------------------------ search

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        for mode in self._MODE_ORDER:
            try:
                if mode == "medium_direct":
                    results = self._try_medium_direct(query, limit)
                elif mode == "ddg_site":
                    results = self._try_ddg_site(query, limit)
                else:  # pragma: no cover — _MODE_ORDER guards this.
                    results = []
            except Exception as e:
                log.warning("[medium] %s raised: %s", mode, e)
                results = []
            if results:
                self._last_mode = mode
                return results
        return []

    # ---------------------------------------------------- medium_direct mode

    def _try_medium_direct(
        self, query: str, limit: int
    ) -> list[SearchResult]:
        """Navigate to medium.com/search?q=... and scrape rendered cards."""
        # Warm up on the homepage so cookies settle before /search.
        if safe_goto(self.page, MEDIUM_HOME + "/", timeout=20000, retries=1):
            human_delay(1.0, 2.0)
            self._human_hints()

        q = urllib.parse.quote(query)
        url = f"{MEDIUM_HOME}/search?q={q}"
        log.info("[medium] direct search: %s", url)
        if not safe_goto(self.page, url, timeout=30000, retries=1):
            self.last_status = {
                "mode": "medium_direct",
                "error": "goto_failed",
            }
            return []

        self._pages_fetched = 1

        # Wait for at least one candidate selector to appear; tolerate
        # timeout (we'll fall through if nothing appears).
        for sel in DIRECT_RESULT_SELECTORS:
            try:
                self.page.wait_for_selector(sel, timeout=6000)
                break
            except Exception:
                continue

        human_delay(1.5, 3.0)
        self._human_hints()

        # Scroll once to encourage lazy-loaded cards to render. Medium
        # only renders ~10 cards initially; scroll triggers more.
        try:
            self.page.evaluate(
                "() => window.scrollBy(0, document.body.scrollHeight * 0.6)"
            )
        except Exception:
            pass
        human_delay(1.0, 2.0)

        if self._is_blocked("medium_direct"):
            return []

        items = []
        used = None
        for sel in DIRECT_RESULT_SELECTORS:
            try:
                items = self.page.query_selector_all(sel)
            except Exception:
                items = []
            if items:
                used = sel
                break
        if not items:
            self.last_status.setdefault("mode", "medium_direct")
            self.last_status["count"] = 0
            return []

        log.info("[medium] direct via %s (%d items)", used, len(items))
        self.last_status["selector"] = used

        results: list[SearchResult] = []
        seen: set[str] = set()
        for r in items[:MAX_CARDS_TO_SCAN]:
            sr = self._extract_direct_item(r)
            if sr is None:
                continue
            key = _strip_url_query(sr.url)
            if key in seen:
                continue
            seen.add(key)
            results.append(sr)
            if len(results) >= limit:
                break

        if results:
            self.last_status["mode"] = "medium_direct"
            self.last_status["count"] = len(results)
        return results

    def _extract_direct_item(self, r) -> SearchResult | None:
        """Pull a single SearchResult out of one DOM container."""
        # Aggregate text once for cheap regex-based extraction of read
        # time / date / paywall badge.
        try:
            full_text = (r.inner_text() or "").strip()
        except Exception:
            full_text = ""

        # Find the article link. Medium puts multiple <a> tags inside
        # each card (author link, publication link, headline link, tag
        # links). We pick the first <a> whose href looks like an
        # article URL.
        href = ""
        title = ""
        try:
            anchors = r.query_selector_all("a[href]")
        except Exception:
            anchors = []

        # Keep references so we can identify author / publication links
        # later. Pre-compute (href, text) for each anchor.
        anchor_info: list[tuple[str, str]] = []
        for a in anchors:
            try:
                h = a.get_attribute("href") or ""
                t = (a.inner_text() or "").strip()
            except Exception:
                continue
            if not h:
                continue
            h_abs = _abs_medium(h)
            anchor_info.append((h_abs, t))
            if not href and _looks_like_article_url(h_abs):
                href = h_abs
                # Prefer the headline text from inside the article link.
                # If the anchor wraps an h2/h3 the inner_text we just
                # captured is fine; otherwise look for the heading
                # element as a sibling within the card.
                title = t

        if not href:
            return None

        # Promote a real <h2>/<h3> headline text over the link's bare
        # inner text when the anchor only wrapped an image.
        if not title or len(title) < 4:
            try:
                h_el = (
                    r.query_selector("h2")
                    or r.query_selector("h3")
                    or r.query_selector('[data-testid="storyTitle"]')
                )
                if h_el:
                    h_text = (h_el.inner_text() or "").strip()
                    if h_text:
                        title = h_text
            except Exception:
                pass

        if not title:
            return None

        # Author: first /@<handle> link inside the card.
        author = ""
        author_url = ""
        for h_abs, t in anchor_info:
            path = urllib.parse.urlparse(h_abs).path or ""
            if path.startswith("/@"):
                # First @user link is the author. Skip if the text is
                # blank or matches a generic label like "Follow".
                if t and t.lower() not in {"follow", "following", "..."}:
                    author = t
                    author_url = h_abs
                    break
                if not author_url:
                    author_url = h_abs

        # Publication: a link of the form ``/<pub-slug>`` (no @, not an
        # article). Some cards have no publication; that's fine.
        publication = ""
        for h_abs, t in anchor_info:
            path = urllib.parse.urlparse(h_abs).path or ""
            # Skip the article link itself.
            if h_abs == href:
                continue
            if path.startswith("/@"):
                continue
            # Publication slugs are short paths without trailing
            # ``-<id>`` (which marks an article) and don't start with
            # known reserved namespaces.
            if not path or path == "/":
                continue
            # Reject article-shaped paths.
            if re.search(r"-[0-9a-f]{6,}/?$", path):
                continue
            # Reject reserved / utility paths.
            if path.startswith(
                (
                    "/search",
                    "/m/",
                    "/_/",
                    "/tag/",
                    "/topics/",
                    "/about",
                    "/membership",
                    "/p/",
                    "/me/",
                    "/help",
                )
            ):
                continue
            # Single-segment slug, e.g. ``/better-programming``.
            segs = [s for s in path.split("/") if s]
            if len(segs) == 1 and t:
                publication = t.strip()
                break

        read_time = _extract_read_time(full_text)
        published_at = _extract_published_at(full_text)
        member_only = _is_member_only(full_text)
        claps = self._extract_claps(r, full_text)

        sr = SearchResult(
            title=title,
            url=href,
            snippet=_compose_snippet(
                author, publication, read_time, member_only
            ),
            score=claps,
        )
        return _attach_extras(
            sr,
            author=author,
            author_url=author_url,
            publication=publication,
            read_time=read_time,
            claps=claps,
            member_only=member_only,
            published_at=published_at,
        )

    def _extract_claps(self, container, full_text: str) -> int | None:
        """Find the claps count on a story card.

        Medium renders claps as a button with an ``aria-label`` like
        ``"123 claps"`` or as a small numeric chip alongside a hands-up
        SVG icon. We try the aria route first, then fall back to the
        text-content heuristic.
        """
        try:
            aria_els = container.query_selector_all(
                'button[aria-label*="clap" i], '
                'span[aria-label*="clap" i], '
                'div[aria-label*="clap" i]'
            )
        except Exception:
            aria_els = []
        for el in aria_els:
            try:
                aria = el.get_attribute("aria-label") or ""
            except Exception:
                aria = ""
            n = _parse_int(aria)
            if n is not None:
                return n

        # Fallback: many layouts surface "<N> claps" near the bottom.
        m = re.search(r"(\d[\d,.]*\s*[KMkm]?)\s*claps?", full_text)
        if m:
            n = _parse_int(m.group(1))
            if n is not None:
                return n
        return None

    # -------------------------------------------------------- ddg_site mode

    def _try_ddg_site(self, query: str, limit: int) -> list[SearchResult]:
        site_query = f"site:medium.com {query}"
        q = urllib.parse.quote(site_query)
        url = f"{DDG_HTML_ENDPOINT}?q={q}"
        log.info("[medium] ddg site search: %s", url)
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
            "selector": ".result",
        }

        results: list[SearchResult] = []
        seen: set[str] = set()
        try:
            items = self.page.query_selector_all(".result")
        except Exception:
            items = []
        log.info("[medium] ddg got %d .result items", len(items))

        for r in items[: limit * 4]:
            title_el = r.query_selector(".result__a")
            snippet_el = r.query_selector(".result__snippet")
            try:
                title = (
                    (title_el.inner_text() or "").strip() if title_el else ""
                )
                href = (
                    (title_el.get_attribute("href") or "")
                    if title_el
                    else ""
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
            if "medium.com" not in href.lower():
                continue
            if not _looks_like_article_url(href):
                continue

            # Try to pull the author handle from the URL when it's a
            # ``/@author/article-slug-<id>`` link.
            author = ""
            author_url = ""
            path = urllib.parse.urlparse(href).path or ""
            m_auth = re.match(r"^/@([^/]+)/", path)
            if m_auth:
                author = "@" + m_auth.group(1)
                author_url = MEDIUM_HOME + "/@" + m_auth.group(1)

            read_time = _extract_read_time(snippet)
            published_at = _extract_published_at(snippet)
            member_only = _is_member_only(snippet)

            key = _strip_url_query(href)
            if key in seen:
                continue
            seen.add(key)

            sr = SearchResult(
                title=title,
                url=href,
                snippet=_compose_snippet(
                    author, "", read_time, member_only
                )
                or snippet,
                score=None,
            )
            _attach_extras(
                sr,
                author=author,
                author_url=author_url,
                publication="",
                read_time=read_time,
                claps=None,
                member_only=member_only,
                published_at=published_at,
            )
            results.append(sr)
            if len(results) >= limit:
                break

        if results:
            self.last_status["count"] = len(results)
        return results

    # -------------------------------------------------------- block detection

    def _is_blocked(self, mode: str) -> bool:
        """Detect Cloudflare / Medium interstitials and rate-limits."""
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
            "mode": mode,
            "url": url,
            "title": title,
            "body_len": len(body),
        }

        # Medium occasionally redirects unauth'd searches to the
        # sign-in / sign-up wall; treat that as a soft block so we
        # fall through to ddg_site.
        if "/m/signin" in url or "/m/signup" in url:
            self.last_status["block_reason"] = "auth_wall"
            log.warning("[medium] auth wall: %s", url)
            return True

        head = body[:3000]
        for phrase in BLOCK_PHRASES:
            if phrase in head or phrase in title:
                self.last_status["block_reason"] = phrase
                log.warning("[medium] block phrase detected: %r", phrase)
                return True
        return False

    # -------------------------------------------------------------- diagnostics

    def selector_counts(self) -> dict[str, int]:
        """Per-selector counts; safe to call regardless of last_mode."""
        counts: dict[str, int] = {}
        probe = [
            "article",
            'div[data-testid="storyPreview"]',
            'div[role="link"][data-testid]',
            "div.streamItem",
            "h2",
            "h3",
            'a[href*="/@"]',
            'button[aria-label*="clap" i]',
            ".result",
            ".result__a",
            ".result__snippet",
        ]
        for sel in probe:
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
