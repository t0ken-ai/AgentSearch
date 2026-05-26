"""Instagram search adapter — multi-mode crawler with og-meta enrichment.

Modes (all reachable from ``InstagramEngine.search(query, ..., mode=...)``):

* ``"hashtag"`` (default): drive ``instagram.com/explore/tags/<tag>/``,
  optionally scroll for more cards. If the direct grid is empty / blocked,
  fall back through Google → DuckDuckGo → Bing ``site:instagram.com`` SERPs.
* ``"user"``: treat the query as an Instagram username, hit
  ``instagram.com/<username>/``, parse the ``og:*`` meta tags for
  follower/following/post counts + bio, and walk the recent-posts grid.
* ``"post"``: treat the query as a shortcode (``DTfS7SMEk8B``) or a full
  ``/p/<sc>/`` / ``/reel/<sc>/`` URL, fetch the post page, and parse the
  ``og:description`` for ``likes / comments / posted_at / username / caption``.
* ``"keyword"``: requires a logged-in profile (``--profile instagram``).
  Drives Instagram's internal ``/explore/search/keyword/?q=...`` flow which
  returns users, hashtags, and places.
* ``"auto"`` (legacy): hashtag mode (preserves the older behaviour).

All :class:`SearchResult`s carry the same attributes as before:
``user``, ``user_url``, ``shortcode``, ``post_type``, ``caption``, ``likes``,
``likes_text``, ``comments``, ``comments_text``, ``source``. Two new
optional attributes are populated when the engine has them:

* ``image_url`` – first ``og:image`` URL from the post detail page (only
  filled when ``enrich=True`` or in ``post`` / ``user`` mode).
* ``posted_at`` – ISO-ish date string parsed from ``og:description``.

The engine also exposes two public helpers for callers that already have a
post URL or username and just want enrichment without a search:

* :meth:`InstagramEngine.fetch_post` ``(url_or_shortcode)`` → dict
* :meth:`InstagramEngine.fetch_profile` ``(username)`` → dict
"""

from __future__ import annotations

import logging
import random
import re
import time
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult
from .google import GoogleEngine
from .duckduckgo import DuckDuckGoEngine

log = logging.getLogger(__name__)


# Anchors with this href shape are Instagram post / reel pages. Both ``/p/``
# (feed posts) and ``/reel/`` (reels / video posts) are accepted; the
# shortcode is Instagram's URL-safe base64 id.
POST_HREF_RE = re.compile(r"^/(p|reel)/([A-Za-z0-9_-]+)/?")
ABS_POST_URL_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?:[A-Za-z0-9_.]+/)?(p|reel)/([A-Za-z0-9_-]+)/?"
)
# Profile / user link, e.g. ``/some_user/`` (excluding reserved prefixes).
USER_HREF_RE = re.compile(
    r"^/((?!explore|p/|reel/|stories|accounts|direct|reels|about|developer|"
    r"legal|press|api|emails|web|graphql|locations|tags|challenge|igtv|"
    r"_n|_u|_i)[A-Za-z0-9_.]+)/?$"
)

# Heuristic: looks like a single Instagram handle (used to auto-pick mode).
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.]{2,30}$")
# A bare shortcode (the ``DTfS7SMEk8B`` part). IG shortcodes are URL-safe
# base64 of length ~11 and *always* mix cases / digits — we use that to
# avoid eating English words like "travel" as accidental shortcodes.
SHORTCODE_RE = re.compile(r"^(?=.*[A-Z])(?=.*[a-z0-9])[A-Za-z0-9_-]{10,15}$")

# ``og:description`` of a post page looks like
#   "3M likes, 5,787 comments - <user> on January 14, 2026: \"<caption>\""
# Sometimes likes are hidden ("<user> on ..." with no leading counts).
# We *don't* anchor with ``$`` because SERP snippets often truncate the
# tail with "..." or strip the closing quote. Caption is captured lazily
# up to the first closing ``"``; if the closing ``"`` is missing we fall
# back to "everything after the colon" via a second pattern below.
OG_POST_DESC_RE = re.compile(
    r"^\s*(?:(?P<likes>[\d.,KMBkmb]+)\s+likes?,\s*(?P<comments>[\d.,KMBkmb]+)\s+comments?\s*[-–—]\s*)?"
    r"(?P<user>[A-Za-z0-9_.]{2,30})\s+on\s+(?P<date>(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2},?\s+\d{4})"
    r"(?::\s*[\"“](?P<caption>.*?)(?:[\"”]\.?|$))?",
    re.DOTALL,
)
# ``og:description`` of a profile page looks like
#   "269M Followers, 195 Following, 32K Posts - See Instagram photos and videos from National Geographic (@natgeo)"
OG_PROFILE_DESC_RE = re.compile(
    r"^\s*(?P<followers>[\d.,KMBkmb]+)\s+Followers?,\s*(?P<following>[\d.,KMBkmb]+)\s+Following?,\s*(?P<posts>[\d.,KMBkmb]+)\s+Posts?\b",
    re.IGNORECASE,
)
# `og:url` of a post detail page is usually
#   "https://www.instagram.com/<user>/reel/<sc>/" or ".../p/<sc>/"
OG_POST_URL_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/(?P<user>[A-Za-z0-9_.]+)/(?P<path>p|reel)/(?P<sc>[A-Za-z0-9_-]+)/?"
)


# Selectors used to detect whether the hashtag / search page has hydrated.
RESULT_PRESENCE_SELECTORS = [
    'a[href*="/p/"]',
    'a[href*="/reel/"]',
    "main article a",
    "article a",
]

# Login modal close buttons. Instagram's login modal varies between
# layouts; these all close it without signing in.
LOGIN_MODAL_CLOSE_SELECTORS = [
    'button[aria-label="Close"]',
    'svg[aria-label="Close"]',
    'div[role="dialog"] button[aria-label="Close"]',
    'div[role="presentation"] button[aria-label="Close"]',
    "button:has-text('Not Now')",
    "button:has-text('Not now')",
]

# Cookie / "allow essential cookies" banner buttons (EU / UK).
COOKIE_BUTTON_SELECTORS = [
    "button:has-text('Allow all cookies')",
    "button:has-text('Allow essential')",
    "button:has-text('Decline optional')",
    "button:has-text('Accept All')",
    "button:has-text('Accept all')",
    "button:has-text('Only allow essential')",
    "button[aria-label*='Accept' i]",
    "button[aria-label*='Allow' i]",
]

# Phrases that indicate Instagram is gating us (login wall / 404 / ratelimit).
BLOCK_PHRASES = [
    "log in to instagram",
    "log into instagram",
    "sign up for instagram",
    "page not found",
    "sorry, this page",
    "please wait a few minutes",
    "try again later",
    "we restrict certain activity",
    "challenge_required",
    "checkpoint_required",
]


# ---------------------------------------------------------------- helpers

def _abs_url(href: str) -> str:
    """Make an Instagram href absolute against ``www.instagram.com``."""
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://www.instagram.com" + href
    return "https://www.instagram.com/" + href


def _parse_count(text: str) -> int | None:
    """Parse "12.3K" / "1.2M" / "543" / "1,234" / "1 234" into an int.

    Returns ``None`` when the text doesn't look like a count.
    """
    if not text:
        return None
    t = text.strip().lower().replace(",", "").replace(" ", "")
    m = re.fullmatch(r"(\d+\.?\d*)\s*([kmb]?)", t)
    if not m:
        return None
    try:
        n = float(m.group(1))
    except ValueError:
        return None
    mult = {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(
        m.group(2), 1
    )
    return int(n * mult)


def _hashtagify(query: str) -> str:
    """Turn a free-text query into the slug Instagram uses in its hashtag URL.

    Instagram's hashtag pages are at ``/explore/tags/<slug>/`` where the
    slug is the lowercased word with all non-alphanumeric characters
    removed (no spaces, no leading ``#``). Multi-word queries are
    concatenated into a single slug.
    """
    if not query:
        return ""
    q = query.strip().lstrip("#").lower()
    # Keep ASCII alnum, underscore, and any non-ASCII letters (CJK etc.).
    return re.sub(r"[^a-z0-9_\u00c0-\uffff]+", "", q)


def _looks_like_username(query: str) -> bool:
    """Heuristic: ``@user`` or a bare ``user`` (single token, no spaces)."""
    if not query:
        return False
    q = query.strip().lstrip("@")
    if " " in q or "#" in q:
        return False
    return bool(USERNAME_RE.match(q))


def _find_media_node(obj, shortcode: str | None = None):
    """Recursively walk a parsed JSON tree and return the first
    ``xdt_api__v1__media__shortcode__web_info.items[0]`` dict found.

    When ``shortcode`` is given, prefer items whose ``code`` matches; if
    nothing matches exactly, return the first item encountered (some
    related-grid payloads sit alongside the main post payload in the
    same script blob).
    """
    fallback = None
    stack = [obj]
    while stack:
        cur = stack.pop()
        if isinstance(cur, dict):
            wi = cur.get("xdt_api__v1__media__shortcode__web_info")
            if isinstance(wi, dict):
                items = wi.get("items")
                if isinstance(items, list) and items:
                    item = items[0]
                    if isinstance(item, dict):
                        if shortcode and item.get("code") == shortcode:
                            return item
                        if fallback is None:
                            fallback = item
            for v in cur.values():
                if isinstance(v, (dict, list)):
                    stack.append(v)
        elif isinstance(cur, list):
            for v in cur:
                if isinstance(v, (dict, list)):
                    stack.append(v)
    return fallback


def _extract_shortcode(query: str) -> tuple[str, str] | None:
    """If ``query`` is a shortcode or post/reel URL, return (path, shortcode).

    ``path`` is ``"p"`` or ``"reel"``. Returns ``None`` otherwise.
    """
    if not query:
        return None
    q = query.strip()
    m = ABS_POST_URL_RE.search(q)
    if m:
        return m.group(1), m.group(2)
    if SHORTCODE_RE.fullmatch(q):
        return "p", q
    return None


def _parse_og_post_description(text: str) -> dict:
    """Parse a post/reel ``og:description`` meta value.

    Returns a dict possibly containing keys: ``likes_text``, ``likes``,
    ``comments_text``, ``comments``, ``user``, ``posted_at``, ``caption``.
    """
    if not text:
        return {}
    m = OG_POST_DESC_RE.match(text.strip())
    if not m:
        return {}
    out: dict = {}
    if m.group("likes"):
        out["likes_text"] = m.group("likes")
        out["likes"] = _parse_count(m.group("likes"))
    if m.group("comments"):
        out["comments_text"] = m.group("comments")
        out["comments"] = _parse_count(m.group("comments"))
    if m.group("user"):
        out["user"] = m.group("user")
    if m.group("date"):
        out["posted_at"] = m.group("date").strip()
    if m.group("caption"):
        out["caption"] = m.group("caption").strip()
    return out


def _parse_og_profile_description(text: str) -> dict:
    """Parse a profile ``og:description`` meta value.

    Returns a dict possibly containing keys: ``followers_text``, ``followers``,
    ``following_text``, ``following``, ``posts_text``, ``posts``.
    """
    if not text:
        return {}
    m = OG_PROFILE_DESC_RE.search(text.strip())
    if not m:
        return {}
    out: dict = {}
    out["followers_text"] = m.group("followers")
    out["followers"] = _parse_count(m.group("followers"))
    out["following_text"] = m.group("following")
    out["following"] = _parse_count(m.group("following"))
    out["posts_text"] = m.group("posts")
    out["posts"] = _parse_count(m.group("posts"))
    return out


# ---------------------------------------------------------------- engine

class InstagramEngine(BaseEngine):
    """Search / scrape Instagram with hashtag, user, post, and keyword modes."""

    name = "instagram"
    max_retries = 2  # The fallback chain already adds resilience.

    TAG_URL = "https://www.instagram.com/explore/tags/{tag}/"
    USER_URL = "https://www.instagram.com/{user}/"
    POST_URL = "https://www.instagram.com/{path}/{sc}/"
    SEARCH_URL = "https://www.instagram.com/explore/search/keyword/?q={q}"
    HOMEPAGE_URL = "https://www.instagram.com/"

    # Reserved usernames that cannot be Instagram handles. Used to filter
    # out bogus matches when we slug-extract a username.
    RESERVED_USERS = frozenset({
        "explore", "p", "reel", "reels", "stories", "accounts", "direct",
        "about", "developer", "legal", "press", "api", "emails", "web",
        "graphql", "locations", "tags", "challenge", "igtv",
    })

    def __init__(self, page):
        super().__init__(page)
        # Diagnostics surface for callers / tests.
        self.last_status: dict = {}
        # Lazy: only checked when keyword mode is requested.
        self._authed: bool | None = None

    # --------------------------------------------------------- public API

    def search(  # type: ignore[override]
        self,
        query: str,
        limit: int = 10,
        *,
        mode: str = "auto",
        enrich: bool = False,
        max_scrolls: int = 0,
    ) -> list[SearchResult]:
        """Run an Instagram search.

        Parameters
        ----------
        query    : free text, hashtag, username, shortcode, or post URL.
        limit    : max number of results to return.
        mode     : ``"auto"`` (default), ``"hashtag"``, ``"user"``,
                   ``"post"``, or ``"keyword"``. ``"auto"`` keeps the
                   legacy behaviour: hashtag with Google fallback.
        enrich   : if True, after the listing path returns shortcodes,
                   visit each post detail page and fill ``likes`` /
                   ``comments`` / ``posted_at`` / ``image_url``. Adds
                   roughly +1.5s per result.
        max_scrolls : in hashtag / user mode, how many extra scrolls
                   (each ~12 cards) to perform after the first paint.
                   ``0`` disables scrolling (legacy behaviour).
        """
        self.last_status = {"mode_requested": mode}
        for attempt in range(self.max_retries):
            try:
                results = self._do_search_dispatch(
                    query, limit, mode=mode, enrich=enrich,
                    max_scrolls=max_scrolls,
                )
                if results:
                    return results
            except Exception as e:
                log.error(
                    "[%s] mode=%s error (attempt %d): %s",
                    self.name, mode, attempt + 1, e,
                )
            human_delay(2, 4)
        return []

    def fetch_post(self, url_or_shortcode: str) -> dict:
        """Fetch a single post / reel and return all parsed fields.

        Returns ``{}`` when the post can't be reached (login wall, 404, or
        challenge). Otherwise returns a dict with keys ``url`` ``shortcode``
        ``post_type`` ``user`` ``user_url`` ``caption`` ``likes`` ``likes_text``
        ``comments`` ``comments_text`` ``posted_at`` ``image_url`` ``title``.
        """
        sc = _extract_shortcode(url_or_shortcode)
        if not sc:
            return {}
        return self._fetch_post_meta(sc[0], sc[1])

    def fetch_profile(self, username: str) -> dict:
        """Fetch a profile's metadata + recent posts grid.

        Returns ``{}`` on failure. Otherwise returns a dict with keys
        ``user`` ``user_url`` ``display_name`` ``bio`` ``profile_pic_url``
        ``followers`` ``followers_text`` ``following`` ``following_text``
        ``posts`` ``posts_text`` ``recent``  (list of {shortcode, post_type, url, caption}).
        """
        return self._fetch_profile(username.lstrip("@"))

    # --------------------------------------------------------- dispatch

    def _do_search_dispatch(
        self,
        query: str,
        limit: int,
        *,
        mode: str,
        enrich: bool,
        max_scrolls: int,
    ) -> list[SearchResult]:
        """Single-attempt dispatcher (the retry loop sits in ``search``)."""
        m = (mode or "auto").lower()

        # post mode: query *is* the URL / shortcode.
        if m == "post":
            sc = _extract_shortcode(query)
            if not sc:
                self.last_status["error"] = "no_shortcode_in_query"
                return []
            data = self._fetch_post_meta(*sc)
            if not data:
                return []
            return [self._post_meta_to_result(data, source="instagram")]

        # user mode: query is a username.
        if m == "user":
            username = query.strip().lstrip("@")
            return self._search_user_profile(
                username, limit, enrich=enrich, max_scrolls=max_scrolls
            )

        # keyword mode: needs login.
        if m == "keyword":
            return self._search_logged_in_keyword(query, limit, enrich=enrich)

        # auto mode: re-route obvious shortcode / URL queries to post mode,
        # but keep the historic hashtag-first behaviour for regular text.
        if m == "auto":
            sc = _extract_shortcode(query)
            if sc:
                data = self._fetch_post_meta(*sc)
                if data:
                    return [self._post_meta_to_result(data, source="instagram")]
            # fall through to hashtag

        # default: hashtag with the multi-fallback chain.
        results = self._search_hashtag(
            query, limit, max_scrolls=max_scrolls
        )
        if not results:
            results = self._search_serp_fallbacks(query, limit)
        if enrich and results:
            self._enrich_results_inplace(results)
        return results

    # ----------------------------------------------------- hashtag (direct)

    def _search_hashtag(
        self,
        query: str,
        limit: int,
        *,
        max_scrolls: int = 0,
    ) -> list[SearchResult]:
        """Hit Instagram's hashtag page and try to parse the rendered DOM."""
        # Warm-up: visit homepage so basic cookies get set before we ask
        # for a hashtag page (otherwise the redirect to /accounts/login/
        # fires before the hashtag HTML even loads).
        if safe_goto(self.page, self.HOMEPAGE_URL, timeout=20000, retries=1):
            human_delay(1.0, 2.5)
            self._dismiss_overlays()
            self._human_hints()

        tag = _hashtagify(query)
        if not tag:
            self.last_status = {"phase": "direct", "error": "empty_tag"}
            return []

        url = self.TAG_URL.format(tag=urllib.parse.quote(tag))
        log.info("[instagram] navigating to %s", url)
        if not safe_goto(self.page, url, timeout=30000):
            self.last_status = {"phase": "direct", "error": "goto_failed"}
            return []

        human_delay(2.0, 3.5)
        self._dismiss_overlays()
        self._human_hints()

        # Hard-redirect to login? Bail to fallback.
        cur = (self.page.url or "").lower()
        if "instagram.com/accounts/login" in cur:
            log.warning("[instagram] redirected to login: %s", cur)
            self.last_status = {"phase": "direct", "block_reason": "login_redirect"}
            return []

        if self._is_blocked():
            return []

        if not self._wait_for_results(timeout_ms=8000):
            log.info(
                "[instagram] no result anchors after wait; "
                "trying extraction anyway"
            )

        self._dismiss_overlays()

        # Scroll for more cards if requested.
        if max_scrolls > 0:
            self._scroll_grid(max_scrolls)

        results = self._extract_grid(limit)

        # Force-scroll-if-empty: occasionally IG renders the page chrome
        # (title, "X reels on Instagram") but withholds the grid until
        # something nudges hydration. A single scroll usually unblocks it.
        if not results and max_scrolls == 0:
            log.info(
                "[instagram] grid empty after wait; nudging with one scroll"
            )
            self._scroll_grid(1)
            results = self._extract_grid(limit)

        log.info("[instagram] hashtag direct extracted: %d", len(results))
        if results:
            self.last_status["mode"] = "hashtag_direct"
        return results

    def _scroll_grid(self, max_scrolls: int) -> None:
        """Scroll the hashtag / user grid to trigger lazy-loading more cards."""
        for i in range(max_scrolls):
            try:
                self.page.evaluate(
                    "() => window.scrollBy(0, document.body.scrollHeight)"
                )
            except Exception as e:
                log.debug("[instagram] scroll %d failed: %s", i + 1, e)
                break
            time.sleep(random.uniform(1.4, 2.4))
            try:
                self.page.evaluate(
                    "() => window.scrollBy(0, -200)"  # tiny up-scroll to nudge hydration
                )
            except Exception:
                pass

    def _extract_grid(self, limit: int) -> list[SearchResult]:
        """Walk every Instagram post / reel anchor on the page."""
        try:
            raw: list[dict] = self.page.evaluate(_EXTRACT_JS) or []
        except Exception as e:
            log.warning("[instagram] extraction JS failed: %s", e)
            raw = []

        log.info("[instagram] grid raw extracted: %d", len(raw))

        results: list[SearchResult] = []
        seen: set[str] = set()
        for item in raw:
            shortcode = (item.get("shortcode") or "").strip()
            if not shortcode or shortcode in seen:
                continue
            href = (item.get("href") or "").strip()
            m = POST_HREF_RE.match(href)
            if not m:
                continue
            post_type = "reel" if m.group(1) == "reel" else "post"
            url = f"https://www.instagram.com/{m.group(1)}/{shortcode}/"
            seen.add(shortcode)

            user = (item.get("user") or "").strip().lstrip("@")
            user_url = (
                f"https://www.instagram.com/{user}/" if user else ""
            )
            caption = (item.get("caption") or "").strip()
            title = caption or (
                f"@{user} on Instagram" if user else f"Instagram {post_type}"
            )

            likes_text = (item.get("likes_text") or "").strip()
            likes = _parse_count(likes_text)
            comments_text = (item.get("comments_text") or "").strip()
            comments = _parse_count(comments_text)

            head_bits: list[str] = []
            if user:
                head_bits.append(f"@{user}")
            head_bits.append(post_type)
            if likes_text:
                head_bits.append(f"{likes_text} likes")
            if comments_text:
                head_bits.append(f"{comments_text} comments")
            snippet = " · ".join(head_bits)
            if caption and caption != title:
                snippet = snippet + " — " + caption

            r = SearchResult(title=title[:200], url=url, snippet=snippet[:400])
            self._stamp_post_attrs(
                r, user=user, user_url=user_url, shortcode=shortcode,
                post_type=post_type, caption=caption,
                likes=likes, likes_text=likes_text,
                comments=comments, comments_text=comments_text,
                source="instagram",
            )
            results.append(r)
            if len(results) >= limit:
                break

        return results

    # ----------------------------------------------------- user / profile

    def _search_user_profile(
        self,
        username: str,
        limit: int,
        *,
        enrich: bool,
        max_scrolls: int,
    ) -> list[SearchResult]:
        """Hit ``instagram.com/<user>/`` and return their recent posts."""
        if not username:
            self.last_status["error"] = "empty_username"
            return []
        # Warm-up so cookies land first.
        if safe_goto(self.page, self.HOMEPAGE_URL, timeout=20000, retries=1):
            human_delay(0.8, 1.6)
            self._dismiss_overlays()

        url = self.USER_URL.format(user=urllib.parse.quote(username))
        log.info("[instagram] navigating to user %s", url)
        if not safe_goto(self.page, url, timeout=30000):
            self.last_status = {"phase": "user", "error": "goto_failed"}
            return []
        human_delay(2.0, 3.5)
        self._dismiss_overlays()
        self._human_hints()

        # Recognise the IG "user not found" shape.
        cur = (self.page.url or "").lower()
        if "instagram.com/accounts/login" in cur:
            self.last_status = {"phase": "user", "block_reason": "login_redirect"}
            return []

        # Always parse og meta for profile-level info.
        og = self._read_og_meta()
        prof_meta = _parse_og_profile_description(og.get("og:description", ""))
        log.info(
            "[instagram] profile %s og parsed: %s", username, prof_meta
        )

        # Walk recent post grid (typically 12 cards anonymously).
        if max_scrolls > 0:
            self._scroll_grid(max_scrolls)
        results = self._extract_grid(limit)

        # Stamp the profile-level metadata on every result so callers can
        # see "@<user> | 269M followers | 4,800 posts" without an extra
        # round-trip.
        for r in results:
            r.user = username  # type: ignore[attr-defined]
            r.user_url = url  # type: ignore[attr-defined]
            r.followers = prof_meta.get("followers")  # type: ignore[attr-defined]
            r.followers_text = prof_meta.get("followers_text", "")  # type: ignore[attr-defined]
            r.profile_posts = prof_meta.get("posts")  # type: ignore[attr-defined]
            r.profile_posts_text = prof_meta.get("posts_text", "")  # type: ignore[attr-defined]
            r.source = "instagram_profile"  # type: ignore[attr-defined]

        # If grid was empty but og meta worked, return at least a single
        # synthetic result that carries the profile metadata so the agent
        # gets *something* useful (very common when IG gates the SPA).
        if not results and prof_meta:
            r = SearchResult(
                title=og.get("og:title", f"@{username} on Instagram")[:200],
                url=url,
                snippet=og.get("og:description", "")[:400],
            )
            self._stamp_post_attrs(
                r, user=username, user_url=url, shortcode="",
                post_type="profile", caption=og.get("description", ""),
                likes=None, likes_text="",
                comments=None, comments_text="",
                source="instagram_profile",
            )
            r.followers = prof_meta.get("followers")  # type: ignore[attr-defined]
            r.followers_text = prof_meta.get("followers_text", "")  # type: ignore[attr-defined]
            r.profile_posts = prof_meta.get("posts")  # type: ignore[attr-defined]
            r.profile_posts_text = prof_meta.get("posts_text", "")  # type: ignore[attr-defined]
            r.image_url = og.get("og:image", "")  # type: ignore[attr-defined]
            results = [r]

        self.last_status["mode"] = "user"
        if enrich and results:
            self._enrich_results_inplace(
                [r for r in results if getattr(r, "shortcode", "")]
            )
        return results

    def _fetch_profile(self, username: str) -> dict:
        """Public-shaped profile dump with og meta + recent posts."""
        if not username:
            return {}
        if safe_goto(self.page, self.HOMEPAGE_URL, timeout=20000, retries=1):
            human_delay(0.6, 1.2)
            self._dismiss_overlays()
        url = self.USER_URL.format(user=urllib.parse.quote(username))
        if not safe_goto(self.page, url, timeout=30000):
            return {}
        human_delay(1.5, 2.8)
        self._dismiss_overlays()
        cur = (self.page.url or "").lower()
        if "instagram.com/accounts/login" in cur:
            return {}
        og = self._read_og_meta()
        prof = _parse_og_profile_description(og.get("og:description", ""))
        # Bio: the full ``description`` meta is usually
        #   "<followers> Followers, ... - <DisplayName> (@user) on Instagram: \"<bio>\""
        # We extract the part after the colon.
        bio = ""
        desc = og.get("description", "") or ""
        idx = desc.find(": \"")
        if idx >= 0:
            bio = desc[idx + 3 :].strip().rstrip("\"")
        # display name from og:title => "<Display> (@user) • Instagram photos and videos"
        display = ""
        ot = og.get("og:title", "") or ""
        m_disp = re.match(r"^(.*?)\s*\(@", ot)
        if m_disp:
            display = m_disp.group(1).strip()

        # recent posts: walk the grid (no scroll for the public helper —
        # callers can pass max_scrolls via search() if they want more).
        try:
            raw: list[dict] = self.page.evaluate(_EXTRACT_JS) or []
        except Exception:
            raw = []
        recent: list[dict] = []
        seen: set[str] = set()
        for item in raw:
            sc = (item.get("shortcode") or "").strip()
            href = (item.get("href") or "").strip()
            mh = POST_HREF_RE.match(href)
            if not sc or sc in seen or not mh:
                continue
            seen.add(sc)
            recent.append({
                "shortcode": sc,
                "post_type": "reel" if mh.group(1) == "reel" else "post",
                "url": f"https://www.instagram.com/{mh.group(1)}/{sc}/",
                "caption": (item.get("caption") or "").strip(),
            })

        return {
            "user": username,
            "user_url": url,
            "display_name": display,
            "bio": bio,
            "profile_pic_url": og.get("og:image", ""),
            "followers_text": prof.get("followers_text", ""),
            "followers": prof.get("followers"),
            "following_text": prof.get("following_text", ""),
            "following": prof.get("following"),
            "posts_text": prof.get("posts_text", ""),
            "posts": prof.get("posts"),
            "recent": recent,
        }

    # ----------------------------------------------------- post detail

    def _fetch_post_meta(self, path: str, shortcode: str) -> dict:
        """Visit ``instagram.com/<path>/<sc>/`` and parse og meta + web_info JSON.

        Two data sources are merged with web_info preferred when present:

        1. ``<script type="application/json">`` blobs containing
           ``xdt_api__v1__media__shortcode__web_info`` — the same JSON node
           IG's GraphQL ``doc_id=8845758582119845`` returns. Yields exact
           like_count (not "3M"), full untruncated caption, image candidates
           at every resolution, video URLs, and sidecar children.
        2. ``og:description`` meta — fallback when the Relay payload is
           missing (e.g. transient block) and as a sanity check.
        """
        if path not in ("p", "reel"):
            return {}
        url = self.POST_URL.format(path=path, sc=shortcode)
        if not safe_goto(self.page, url, timeout=25000):
            return {}
        human_delay(1.2, 2.4)
        self._dismiss_overlays()

        cur = (self.page.url or "").lower()
        if "instagram.com/accounts/login" in cur:
            self.last_status["block_reason"] = "login_redirect"
            return {}

        # Path 1: try the embedded web_info JSON (best data).
        web_info = self._extract_web_info(shortcode)
        # Path 2: og meta (always run — used as fallback / sanity check).
        og = self._read_og_meta()
        og_post = _parse_og_post_description(
            og.get("og:description") or og.get("description") or ""
        )

        # If neither source yielded anything, treat as failed.
        if not web_info and not og.get("og:title") and not og.get("og:description"):
            return {}

        # Determine canonical post_type.
        post_type = "reel" if path == "reel" else "post"
        if web_info:
            mt = web_info.get("media_type")
            if mt == 8:
                post_type = "sidecar"
            elif mt == 2 or web_info.get("video_versions"):
                post_type = "reel" if path == "reel" else "video"

        # Username.
        user = ""
        if web_info:
            u = web_info.get("user") or {}
            user = (u.get("username") or "").lstrip("@")
        if not user:
            user = (og_post.get("user") or "").lstrip("@")
        if not user:
            og_url = og.get("og:url") or ""
            m_url = OG_POST_URL_RE.search(og_url)
            if m_url:
                user = m_url.group("user")

        # Caption (prefer untruncated web_info text).
        caption = ""
        if web_info:
            cap_obj = web_info.get("caption") or {}
            if isinstance(cap_obj, dict):
                caption = cap_obj.get("text") or ""
        if not caption:
            caption = og_post.get("caption") or ""
            if not caption:
                title = og.get("og:title") or ""
                if ": \"" in title:
                    try:
                        caption = title.split(": \"", 1)[1].rstrip("\"")
                    except Exception:
                        pass

        # Likes / comments — prefer exact web_info ints.
        likes = web_info.get("like_count") if web_info else None
        comments = web_info.get("comment_count") if web_info else None
        likes_text = ""
        comments_text = ""
        if likes is not None:
            likes_text = f"{likes:,}"
        else:
            likes = og_post.get("likes")
            likes_text = og_post.get("likes_text", "")
        if comments is not None:
            comments_text = f"{comments:,}"
        else:
            comments = og_post.get("comments")
            comments_text = og_post.get("comments_text", "")

        # Posted_at — web_info gives unix ts, og gives "January 14, 2026".
        posted_at = ""
        taken_at_unix = web_info.get("taken_at") if web_info else None
        if taken_at_unix:
            try:
                from datetime import datetime, timezone
                dt = datetime.fromtimestamp(int(taken_at_unix), tz=timezone.utc)
                posted_at = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
            except Exception:
                pass
        if not posted_at:
            posted_at = og_post.get("posted_at", "")

        # Media URLs: image (best of image_versions2) and video.
        image_url = ""
        image_urls: list[str] = []
        video_url = ""
        video_urls: list[str] = []
        sidecar: list[dict] = []
        if web_info:
            iv = (web_info.get("image_versions2") or {}).get("candidates") or []
            # Sort by resolution descending so [0] is the highest quality.
            iv_sorted = sorted(
                iv, key=lambda c: (c.get("width") or 0) * (c.get("height") or 0),
                reverse=True,
            )
            image_urls = [c.get("url", "") for c in iv_sorted if c.get("url")]
            if image_urls:
                image_url = image_urls[0]
            vv = web_info.get("video_versions") or []
            vv_sorted = sorted(
                vv, key=lambda v: (v.get("width") or 0) * (v.get("height") or 0),
                reverse=True,
            )
            video_urls = [v.get("url", "") for v in vv_sorted if v.get("url")]
            if video_urls:
                video_url = video_urls[0]
            for child in (web_info.get("carousel_media") or []):
                ch_iv = (child.get("image_versions2") or {}).get("candidates") or []
                ch_iv_sorted = sorted(
                    ch_iv,
                    key=lambda c: (c.get("width") or 0) * (c.get("height") or 0),
                    reverse=True,
                )
                ch_vv = child.get("video_versions") or []
                ch_vv_sorted = sorted(
                    ch_vv,
                    key=lambda v: (v.get("width") or 0) * (v.get("height") or 0),
                    reverse=True,
                )
                sidecar.append({
                    "code": child.get("code"),
                    "media_type": child.get("media_type"),
                    "is_video": bool(ch_vv),
                    "image_url": (ch_iv_sorted[0]["url"] if ch_iv_sorted else ""),
                    "video_url": (ch_vv_sorted[0]["url"] if ch_vv_sorted else ""),
                })
        if not image_url:
            image_url = og.get("og:image", "") or ""

        # Title fallback.
        title = og.get("og:title") or ""
        if not title:
            if user and caption:
                title = f"{user} on Instagram: \"{caption[:100]}\""
            elif user:
                title = f"@{user} on Instagram"
            else:
                title = f"Instagram {post_type}"

        return {
            "url": url,
            "shortcode": shortcode,
            "post_type": post_type,
            "user": user,
            "user_url": (
                f"https://www.instagram.com/{user}/" if user else ""
            ),
            "title": title,
            "caption": caption,
            "likes_text": likes_text,
            "likes": likes,
            "comments_text": comments_text,
            "comments": comments,
            "posted_at": posted_at,
            "image_url": image_url,
            "image_urls": image_urls,
            "video_url": video_url,
            "video_urls": video_urls,
            "sidecar": sidecar,
            "media_count": 1 + len(sidecar) if not sidecar else len(sidecar),
            "play_count": web_info.get("play_count") if web_info else None,
            "view_count": web_info.get("view_count") if web_info else None,
            "video_duration": web_info.get("video_duration") if web_info else None,
            "media_id": web_info.get("pk") if web_info else None,
        }

    def _extract_web_info(self, shortcode: str) -> dict | None:
        """Walk every ``<script type="application/json">`` blob looking for the
        ``xdt_api__v1__media__shortcode__web_info`` payload. Returns the
        first ``items[0]`` dict whose ``code`` matches ``shortcode``, or
        the first one found if the codes don't line up.
        """
        try:
            scripts: list[str] = self.page.evaluate(
                """
                () => Array.from(document.querySelectorAll('script[type="application/json"]'))
                    .map(s => s.textContent || '')
                    .filter(t => t && t.includes('xdt_api__v1__media__shortcode__web_info'))
                """
            ) or []
        except Exception as e:
            log.debug("[instagram] web_info script scan failed: %s", e)
            return None
        log.debug("[instagram] web_info candidate scripts: %d", len(scripts))
        import json as _json
        for txt in scripts:
            try:
                data = _json.loads(txt)
            except Exception:
                continue
            node = _find_media_node(data, shortcode)
            if node:
                return node
        return None

    def _post_meta_to_result(
        self, data: dict, *, source: str
    ) -> SearchResult:
        """Convert the dict from :meth:`_fetch_post_meta` into a SearchResult."""
        head = []
        user = data.get("user") or ""
        if user:
            head.append(f"@{user}")
        head.append(data.get("post_type") or "post")
        if data.get("likes_text"):
            head.append(f"{data['likes_text']} likes")
        if data.get("comments_text"):
            head.append(f"{data['comments_text']} comments")
        if data.get("posted_at"):
            head.append(data["posted_at"])
        # Surface the sidecar / video count when present.
        sidecar = data.get("sidecar") or []
        if sidecar:
            head.append(f"{len(sidecar)}x sidecar")
        if data.get("video_duration"):
            head.append(f"{data['video_duration']:.1f}s video")
        snippet = " · ".join(head)
        if data.get("caption"):
            snippet = snippet + " — " + data["caption"]

        r = SearchResult(
            title=(data.get("title") or data.get("caption") or "Instagram post")[:200],
            url=data.get("url") or "",
            snippet=snippet[:400],
        )
        self._stamp_post_attrs(
            r,
            user=user,
            user_url=data.get("user_url") or "",
            shortcode=data.get("shortcode") or "",
            post_type=data.get("post_type") or "post",
            caption=data.get("caption") or "",
            likes=data.get("likes"),
            likes_text=data.get("likes_text") or "",
            comments=data.get("comments"),
            comments_text=data.get("comments_text") or "",
            source=source,
        )
        # Media URLs and additional metadata
        r.posted_at = data.get("posted_at") or ""        # type: ignore[attr-defined]
        r.image_url = data.get("image_url") or ""        # type: ignore[attr-defined]
        r.image_urls = data.get("image_urls") or []      # type: ignore[attr-defined]
        r.video_url = data.get("video_url") or ""        # type: ignore[attr-defined]
        r.video_urls = data.get("video_urls") or []      # type: ignore[attr-defined]
        r.sidecar = sidecar                              # type: ignore[attr-defined]
        r.media_count = data.get("media_count")          # type: ignore[attr-defined]
        r.play_count = data.get("play_count")            # type: ignore[attr-defined]
        r.view_count = data.get("view_count")            # type: ignore[attr-defined]
        r.video_duration = data.get("video_duration")    # type: ignore[attr-defined]
        r.media_id = data.get("media_id")                # type: ignore[attr-defined]
        return r

    def _enrich_results_inplace(self, results: list[SearchResult]) -> None:
        """For each result without ``likes``, fetch its post page and fill in.

        Mutates the result list. Stops after a single failed fetch returns
        an empty dict and the next consecutive one too — assumes we got
        rate-limited if two in a row fail.
        """
        consecutive_fail = 0
        for r in results:
            if getattr(r, "likes", None) is not None:
                continue
            sc = getattr(r, "shortcode", "")
            pt = getattr(r, "post_type", "post")
            if not sc:
                continue
            data = self._fetch_post_meta("reel" if pt == "reel" else "p", sc)
            if not data:
                consecutive_fail += 1
                if consecutive_fail >= 2:
                    log.info(
                        "[instagram] enrichment: 2 consecutive fetches failed, "
                        "stopping early"
                    )
                    break
                continue
            consecutive_fail = 0
            # Fill blanks; never overwrite caller-provided fields.
            for fld in ("likes", "likes_text", "comments", "comments_text",
                        "posted_at", "image_url", "image_urls",
                        "video_url", "video_urls", "sidecar",
                        "media_count", "play_count", "view_count",
                        "video_duration", "media_id"):
                if not getattr(r, fld, None):
                    try:
                        setattr(r, fld, data.get(fld))
                    except Exception:
                        pass
            if not getattr(r, "user", "") and data.get("user"):
                r.user = data["user"]  # type: ignore[attr-defined]
                r.user_url = data.get("user_url") or ""  # type: ignore[attr-defined]
            if not getattr(r, "caption", "") and data.get("caption"):
                r.caption = data["caption"]  # type: ignore[attr-defined]

    # ----------------------------------------------------- keyword (logged in)

    def _search_logged_in_keyword(
        self, query: str, limit: int, *, enrich: bool = False,
    ) -> list[SearchResult]:
        """Drive Instagram's logged-in ``/explore/search/keyword/?q=...``.

        When the engine is running anonymously this just falls through to
        the hashtag path with a clear ``last_status`` note. When the page
        was launched with a persistent profile that already contains the
        ``sessionid`` cookie, the SPA renders Top / Accounts / Tags /
        Places sections — we walk every anchor that looks like
        ``/<user>/`` or ``/p/<sc>/`` and emit corresponding results.
        """
        # Warm-up.
        if safe_goto(self.page, self.HOMEPAGE_URL, timeout=20000, retries=1):
            human_delay(0.8, 1.6)
            self._dismiss_overlays()

        # Sniff auth state.
        if self._authed is None:
            self._authed = self._detect_auth()
        if not self._authed:
            self.last_status = {
                "phase": "keyword",
                "error": "not_authed",
                "hint": "run `agentsearch login instagram` first",
            }
            log.warning(
                "[instagram] keyword mode requested but not logged in — "
                "falling back to hashtag flow"
            )
            return self._search_hashtag(query, limit)

        url = self.SEARCH_URL.format(q=urllib.parse.quote(query))
        log.info("[instagram] keyword search %s", url)
        if not safe_goto(self.page, url, timeout=30000):
            self.last_status = {"phase": "keyword", "error": "goto_failed"}
            return []
        human_delay(2.0, 3.5)
        self._dismiss_overlays()
        if self._is_blocked():
            return []

        # Walk every anchor and collect users + hashtags + posts.
        try:
            anchors = self.page.evaluate(
                """
                () => Array.from(document.querySelectorAll('a[href]')).map(a => ({
                    href: a.getAttribute('href') || '',
                    text: (a.innerText || a.textContent || '').trim(),
                }))
                """
            ) or []
        except Exception as e:
            log.warning("[instagram] keyword anchor scan failed: %s", e)
            anchors = []

        results: list[SearchResult] = []
        seen_users: set[str] = set()
        seen_tags: set[str] = set()
        seen_posts: set[str] = set()

        for a in anchors:
            href = a.get("href") or ""
            text = a.get("text") or ""
            # Posts.
            mp = POST_HREF_RE.match(href)
            if mp:
                sc = mp.group(2)
                if sc in seen_posts:
                    continue
                seen_posts.add(sc)
                pt = "reel" if mp.group(1) == "reel" else "post"
                r = SearchResult(
                    title=text or f"Instagram {pt}",
                    url=f"https://www.instagram.com/{mp.group(1)}/{sc}/",
                    snippet=text,
                )
                self._stamp_post_attrs(
                    r, user="", user_url="", shortcode=sc, post_type=pt,
                    caption=text, likes=None, likes_text="",
                    comments=None, comments_text="",
                    source="instagram_keyword",
                )
                results.append(r)
                continue
            # Users.
            mu = USER_HREF_RE.match(href)
            if mu:
                user = mu.group(1)
                if user.lower() in self.RESERVED_USERS or user in seen_users:
                    continue
                seen_users.add(user)
                r = SearchResult(
                    title=text or f"@{user}",
                    url=f"https://www.instagram.com/{user}/",
                    snippet=text,
                )
                self._stamp_post_attrs(
                    r, user=user, user_url=f"https://www.instagram.com/{user}/",
                    shortcode="", post_type="user",
                    caption=text, likes=None, likes_text="",
                    comments=None, comments_text="",
                    source="instagram_keyword",
                )
                results.append(r)
                continue
            # Hashtags.
            if href.startswith("/explore/tags/"):
                tag = href.rstrip("/").split("/")[-1]
                if not tag or tag in seen_tags:
                    continue
                seen_tags.add(tag)
                r = SearchResult(
                    title=text or f"#{tag}",
                    url=f"https://www.instagram.com/explore/tags/{tag}/",
                    snippet=text,
                )
                self._stamp_post_attrs(
                    r, user="", user_url="",
                    shortcode="", post_type="hashtag",
                    caption=text, likes=None, likes_text="",
                    comments=None, comments_text="",
                    source="instagram_keyword",
                )
                results.append(r)

            if len(results) >= limit * 2:  # collect a bit extra; trim below
                break

        results = results[:limit]
        if enrich:
            self._enrich_results_inplace(
                [r for r in results if getattr(r, "shortcode", "")]
            )
        self.last_status["mode"] = "keyword"
        return results

    def _detect_auth(self) -> bool:
        """Best-effort: are we logged in to Instagram?"""
        # 1) cookie probe (works on persistent contexts).
        try:
            ctx = self.page.context  # type: ignore[attr-defined]
            cookies = ctx.cookies() or []
            for c in cookies:
                name = (c.get("name") or "").lower()
                domain = (c.get("domain") or "").lower()
                if "instagram" in domain and name in ("sessionid",):
                    return True
        except Exception:
            pass
        # 2) DOM probe: navbar Profile link.
        try:
            html = self.page.content() or ""
        except Exception:
            html = ""
        return bool(re.search(r'href="/[a-zA-Z0-9_.]+/"\s+aria-label="[^"]*[Pp]rofile', html))

    # ----------------------------------------------------- SERP fallbacks

    def _search_serp_fallbacks(
        self, query: str, limit: int
    ) -> list[SearchResult]:
        """Try Google → DDG ``site:instagram.com`` until we get hits.

        Each SERP path is exercised on the same browser page; safe_goto
        navigates away from any ``/sorry/`` interstitial. Bing is omitted —
        live testing shows it returns 0 results for ``site:instagram.com``
        queries (low IG index density).

        Each SERP-engine instance is forced to ``max_retries=1`` so the
        whole chain caps at ~30-45s instead of ~3min.
        """
        out: list[SearchResult] = []
        last_error: dict | None = None
        for cls in (GoogleEngine, DuckDuckGoEngine):
            engine_name = getattr(cls, "name", cls.__name__)
            log.info("[instagram] SERP fallback: %s", engine_name)
            try:
                got = self._search_via_serp(cls, query, limit)
            except Exception as e:
                log.warning("[instagram] SERP %s raised: %s", engine_name, e)
                continue
            if got:
                self.last_status["mode"] = f"serp_{engine_name}"
                return got
            try:
                last_error = {
                    "engine": engine_name,
                    "url": self.page.url,
                }
            except Exception:
                last_error = {"engine": engine_name}
        if last_error:
            self.last_status["serp_last"] = last_error
        return out

    def _search_via_serp(
        self, engine_cls, query: str, limit: int,
    ) -> list[SearchResult]:
        """Run a single SERP engine instance for ``site:instagram.com``.

        Uses an engine-specific query syntax: Google supports
        ``(inurl:/p/ OR inurl:/reel/)`` to bias toward post pages, but
        DuckDuckGo treats parenthesised operators as literals — we drop
        the ``inurl:`` clause for it and rely on the URL-shape filter
        instead.
        """
        try:
            serp = engine_cls(self.page)
            # Cap retries — we're already in a fallback chain.
            serp.max_retries = 1
        except Exception as e:
            log.warning(
                "[instagram] cannot construct %s: %s", engine_cls.__name__, e
            )
            return []

        engine_name = getattr(engine_cls, "name", engine_cls.__name__)
        tag = _hashtagify(query)
        terms = ["site:instagram.com"]
        # Google understands the ``inurl:`` operator inside grouped OR;
        # DDG/Bing treat the whole parenthesised expression as a phrase.
        if engine_name == "google":
            terms.append("(inurl:/p/ OR inurl:/reel/)")
        if tag:
            terms.append(f"#{tag}")
        else:
            terms.append(query)
        serp_query = " ".join(terms)

        try:
            organics = serp.search(serp_query, limit=max(limit * 3, 20))
        except Exception as e:
            log.warning(
                "[instagram] %s.search raised: %s", engine_cls.__name__, e
            )
            organics = []

        log.info(
            "[instagram] %s returned %d hits for %r",
            engine_cls.__name__, len(organics), serp_query,
        )

        # Strategy 1: filter SERP-engine's structured results.
        candidates: list[dict] = []
        for r in organics:
            entry = self._parse_serp_url(r.url or "", title=r.title or "",
                                         snippet=r.snippet or "")
            if entry:
                candidates.append(entry)

        # Strategy 2: scan all anchors on the page (catches result variants
        # the structured extractor missed — Bing's /ck/a links, Google's
        # carousel cards, etc.).
        try:
            anchors = self.page.evaluate(
                """
                () => Array.from(document.querySelectorAll('a[href]')).map(a => ({
                    href: a.href || '',
                    text: (a.innerText || a.textContent || '').trim()
                }))
                """
            ) or []
        except Exception as e:
            log.debug("[instagram] DOM anchor scan raised: %s", e)
            anchors = []

        for a in anchors:
            entry = self._parse_serp_url(
                a.get("href", ""), title=a.get("text", "") or "", snippet=""
            )
            if entry:
                candidates.append(entry)

        results: list[SearchResult] = []
        seen: set[str] = set()
        for c in candidates:
            shortcode = c["shortcode"]
            if shortcode in seen:
                continue
            seen.add(shortcode)

            post_type = c["post_type"]
            canonical_url = (
                f"https://www.instagram.com/{c['path']}/{shortcode}/"
            )
            snippet_in = c["snippet"]
            title_in = c["title"]

            # Bonus: SERP snippets often verbatim-quote the IG og:description,
            # which means we can parse likes/comments/posted_at out of them
            # without an extra round-trip. Try the title first (sometimes it
            # carries the full og:title), then the snippet body.
            og_parsed: dict = {}
            for s in (title_in, snippet_in):
                p = _parse_og_post_description(s or "")
                if p and (
                    p.get("likes") is not None
                    or p.get("comments") is not None
                    or p.get("posted_at")
                ):
                    og_parsed = p
                    break

            # Username extraction priority: og parse → text/snippet @-mention.
            user = og_parsed.get("user") or ""
            if not user:
                for source in (title_in, snippet_in):
                    m_user = re.search(r"@([A-Za-z0-9_.]{2,30})", source)
                    if m_user:
                        user = m_user.group(1)
                        break
            user_url = f"https://www.instagram.com/{user}/" if user else ""

            head_bits: list[str] = []
            if user:
                head_bits.append(f"@{user}")
            head_bits.append(post_type)
            if og_parsed.get("likes_text"):
                head_bits.append(f"{og_parsed['likes_text']} likes")
            if og_parsed.get("comments_text"):
                head_bits.append(f"{og_parsed['comments_text']} comments")
            if og_parsed.get("posted_at"):
                head_bits.append(og_parsed["posted_at"])
            merged_snippet = " · ".join(head_bits)
            caption = og_parsed.get("caption") or snippet_in
            if caption and caption not in merged_snippet:
                merged_snippet = merged_snippet + " — " + caption

            new_r = SearchResult(
                title=(title_in or (f"@{user} on Instagram" if user else f"Instagram {post_type}"))[:200],
                url=canonical_url,
                snippet=merged_snippet[:400],
            )
            self._stamp_post_attrs(
                new_r,
                user=user, user_url=user_url, shortcode=shortcode,
                post_type=post_type, caption=caption,
                likes=og_parsed.get("likes"),
                likes_text=og_parsed.get("likes_text", ""),
                comments=og_parsed.get("comments"),
                comments_text=og_parsed.get("comments_text", ""),
                source=getattr(engine_cls, "name", engine_cls.__name__),
            )
            new_r.posted_at = og_parsed.get("posted_at", "")  # type: ignore[attr-defined]
            new_r.image_url = ""  # type: ignore[attr-defined]
            results.append(new_r)
            if len(results) >= limit:
                break

        return results

    @staticmethod
    def _parse_serp_url(
        url: str, title: str = "", snippet: str = ""
    ) -> dict | None:
        """Match a SERP-result URL against ``ABS_POST_URL_RE``.

        Handles redirect wrappers used by Google (``/url?q=...``) and
        Bing (``/ck/a?...&u=<base64>...``).
        """
        if not url:
            return None
        # Google /url? wrapper.
        if "google.com/url" in url:
            try:
                qs = urllib.parse.urlparse(url).query
                target = urllib.parse.parse_qs(qs).get("q", [""])[0]
                if target:
                    url = target
            except Exception:
                pass
        # Bing /ck/a wrapper. We don't bother base64-decoding here — we
        # only care if the *href* itself contains the IG URL fragment,
        # which it sometimes does in plain text.
        m = ABS_POST_URL_RE.search(url)
        if not m:
            return None
        path = m.group(1)
        return {
            "path": path,
            "post_type": "reel" if path == "reel" else "post",
            "shortcode": m.group(2),
            "title": title,
            "snippet": snippet,
        }

    # ----------------------------------------------------- shared helpers

    def _stamp_post_attrs(
        self,
        r: SearchResult,
        *,
        user: str = "",
        user_url: str = "",
        shortcode: str = "",
        post_type: str = "post",
        caption: str = "",
        likes: int | None = None,
        likes_text: str = "",
        comments: int | None = None,
        comments_text: str = "",
        source: str = "instagram",
    ) -> None:
        """Attach the engine-specific attributes to a SearchResult."""
        r.user = user                       # type: ignore[attr-defined]
        r.user_url = user_url               # type: ignore[attr-defined]
        r.shortcode = shortcode             # type: ignore[attr-defined]
        r.post_type = post_type             # type: ignore[attr-defined]
        r.caption = caption                 # type: ignore[attr-defined]
        r.likes = likes                     # type: ignore[attr-defined]
        r.likes_text = likes_text           # type: ignore[attr-defined]
        r.comments = comments               # type: ignore[attr-defined]
        r.comments_text = comments_text     # type: ignore[attr-defined]
        r.source = source                   # type: ignore[attr-defined]

    def _read_og_meta(self) -> dict[str, str]:
        """Read every relevant ``<meta property=...>`` / ``<meta name=...>``."""
        try:
            return self.page.evaluate(
                """
                () => {
                    const out = {};
                    const metas = document.querySelectorAll('meta');
                    for (const m of metas) {
                        const k = m.getAttribute('property') || m.getAttribute('name') || '';
                        const v = m.getAttribute('content') || '';
                        if (k && v && !(k in out)) out[k] = v;
                    }
                    return out;
                }
                """
            ) or {}
        except Exception as e:
            log.debug("[instagram] og-meta read failed: %s", e)
            return {}

    def _dismiss_overlays(self) -> None:
        """Close login modals, cookie banners, and any other guest overlays."""
        for sel in COOKIE_BUTTON_SELECTORS:
            try:
                btn = self.page.query_selector(sel)
                if btn:
                    try:
                        btn.click(timeout=2000)
                    except Exception:
                        try:
                            self.page.evaluate("(el) => el.click()", btn)
                        except Exception:
                            continue
                    log.info("[instagram] dismissed cookie banner (%s)", sel)
                    human_delay(0.4, 0.9)
                    break
            except Exception:
                continue

        for sel in LOGIN_MODAL_CLOSE_SELECTORS:
            try:
                btn = self.page.query_selector(sel)
                if btn:
                    try:
                        btn.click(timeout=2000)
                    except Exception:
                        try:
                            self.page.evaluate("(el) => el.click()", btn)
                        except Exception:
                            continue
                    log.info("[instagram] closed login modal (%s)", sel)
                    human_delay(0.4, 0.9)
                    return
            except Exception:
                continue

        try:
            self.page.keyboard.press("Escape")
        except Exception:
            pass

    def _is_blocked(self) -> bool:
        """Detect login-wall / sorry-page / ratelimit interstitials."""
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

        self.last_status["url"] = url
        self.last_status["title"] = title
        self.last_status["body_len"] = len(body)

        if "instagram.com/accounts/login" in url:
            log.warning("[instagram] login redirect: %s", url)
            self.last_status["block_reason"] = "login_redirect"
            return True
        if "/challenge/" in url:
            log.warning("[instagram] challenge redirect: %s", url)
            self.last_status["block_reason"] = "challenge"
            return True

        try:
            has_post_anchor = bool(
                self.page.query_selector('a[href*="/p/"]')
                or self.page.query_selector('a[href*="/reel/"]')
            )
        except Exception:
            has_post_anchor = False
        if not has_post_anchor and (
            "log in to instagram" in body or "log into instagram" in body
        ):
            log.warning("[instagram] login wall (no post anchors)")
            self.last_status["block_reason"] = "login_wall"
            return True

        for phrase in BLOCK_PHRASES:
            if phrase in title:
                log.warning("[instagram] block phrase in title: %r", phrase)
                self.last_status["block_reason"] = phrase
                return True

        if "please wait a few minutes" in body and not has_post_anchor:
            log.warning("[instagram] ratelimited (please wait a few minutes)")
            self.last_status["block_reason"] = "ratelimit"
            return True

        return False

    def _human_hints(self) -> None:
        """Tiny mouse / scroll movement so lazy hydration commits."""
        try:
            self.page.mouse.move(
                random.randint(120, 500),
                random.randint(120, 400),
                steps=8,
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

    def _wait_for_results(self, timeout_ms: int = 8000) -> bool:
        """Wait for at least one post / reel anchor to attach."""
        deadline = time.time() + timeout_ms / 1000.0
        try:
            self.page.wait_for_function(
                """
                () => {
                  const anchors = document.querySelectorAll('a[href]');
                  for (const a of anchors) {
                    const href = a.getAttribute('href') || '';
                    if (/^\\/(p|reel)\\/[A-Za-z0-9_-]+/.test(href)) return true;
                  }
                  return false;
                }
                """,
                timeout=timeout_ms,
            )
            return True
        except Exception as e:
            log.debug("[instagram] wait_for_function timeout: %s", e)
        while time.time() < deadline:
            for sel in RESULT_PRESENCE_SELECTORS:
                try:
                    if self.page.query_selector(sel):
                        return True
                except Exception:
                    continue
            time.sleep(0.5)
        return False

    def selector_counts(self) -> dict[str, int]:
        """Per-selector match counts on the current page (for diagnostics)."""
        counts: dict[str, int] = {}
        for sel in (
            'a[href*="/p/"]',
            'a[href*="/reel/"]',
            "main article a",
            "article a",
            'div[role="dialog"]',
            'svg[aria-label="Like"]',
            'svg[aria-label="Comment"]',
        ):
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        return counts


# ---------------------------------------------------------------- JS
#
# Walks every anchor whose href matches ``/p/<sc>/`` or ``/reel/<sc>/``,
# then resolves the surrounding card/article to dig out the caption,
# author handle, and like / comment counts. We do this in JS rather than
# chained Python selectors to avoid dozens of CDP round-trips per result.
_EXTRACT_JS = r"""
() => {
  const POST_RE = /^\/(p|reel)\/([A-Za-z0-9_-]+)/;
  const USER_RE = /^\/([A-Za-z0-9_.]{2,30})\/?$/;
  const RESERVED = new Set([
    "explore", "p", "reel", "reels", "stories", "accounts", "direct",
    "about", "developer", "legal", "press", "api", "emails", "web",
    "graphql", "locations", "tags", "challenge", "igtv"
  ]);

  const text = (el) => (el ? (el.innerText || el.textContent || "").trim() : "");

  const findCard = (a) => {
    return (
      a.closest('article') ||
      a.closest('[role="link"]') ||
      a.closest('div[class*="Grid"]') ||
      a.parentElement
    );
  };

  const captionFromCard = (card, a) => {
    if (!card) return "";
    const img = card.querySelector('img[alt]');
    if (img) {
      const alt = (img.getAttribute('alt') || '').trim();
      if (alt && alt.length > 4) return alt;
    }
    const aria = (a.getAttribute('aria-label') || '').trim();
    if (aria && aria.length > 4) return aria;
    const at = text(a);
    if (at && at.length > 4) return at;
    const cap = card.querySelector('h1, span[dir="auto"], div[dir="auto"]');
    if (cap) {
      const t = text(cap);
      if (t && t.length > 4) return t;
    }
    return "";
  };

  const userFromCard = (card, postAnchor) => {
    if (!card) return "";
    const candidates = card.querySelectorAll('a[href]');
    for (const ca of candidates) {
      if (ca === postAnchor) continue;
      const href = ca.getAttribute('href') || '';
      const m = href.match(USER_RE);
      if (!m) continue;
      const user = m[1];
      if (RESERVED.has(user.toLowerCase())) continue;
      const t = text(ca);
      if (t && /^[@A-Za-z0-9_.]{2,32}$/.test(t)) {
        return t.replace(/^@/, '');
      }
      return user;
    }
    return "";
  };

  const countFromLabel = (card, label) => {
    if (!card) return "";
    const svgs = card.querySelectorAll(`svg[aria-label="${label}"]`);
    for (const svg of svgs) {
      let p = svg.parentElement;
      while (p && p !== card) {
        const t = text(p);
        if (t) {
          const m = t.match(/\d[\d.,KMBkmb]*/);
          if (m) return m[0];
        }
        p = p.parentElement;
      }
    }
    const allText = text(card);
    if (allText) {
      const re = new RegExp(
        `(\\d[\\d.,KMBkmb]*)\\s+${label.toLowerCase()}s?`, 'i'
      );
      const m = allText.match(re);
      if (m) return m[1];
    }
    return "";
  };

  const out = [];
  const seen = new Set();
  const anchors = document.querySelectorAll('a[href]');

  for (const a of anchors) {
    const href = a.getAttribute('href') || '';
    const m = href.match(POST_RE);
    if (!m) continue;
    const path = m[1];
    const shortcode = m[2];
    if (seen.has(shortcode)) continue;
    seen.add(shortcode);

    const card = findCard(a);
    const caption = captionFromCard(card, a);
    const user = userFromCard(card, a);
    const likes_text = countFromLabel(card, "Like");
    const comments_text = countFromLabel(card, "Comment");

    out.push({
      href: "/" + path + "/" + shortcode + "/",
      shortcode: shortcode,
      user: user,
      caption: caption,
      likes_text: likes_text,
      comments_text: comments_text,
    });
  }

  return out;
}
"""
