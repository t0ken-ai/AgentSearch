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
#
# IMPORTANT: these are matched ONLY against (a) the page URL, (b) the <title>,
# and (c) the inner-text of specific error-container selectors — never against
# the full page body. Reddit's search page echoes the user query back near the
# top of the body (e.g. "search results for: <query>"), so scanning the body
# would false-positive whenever the user's query happens to contain any of the
# words below (e.g. searching for "GitHub rate limit" used to break here).
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
    "you've been blocked by network security",
    "sorry, this content is unavailable",
]

# A subset of the above that is safe to match against the <title> alone — these
# strings only appear in titles when Reddit is genuinely returning a block /
# error page. ("forbidden" alone is too generic to live here; "rate limit"
# never appears in a normal Reddit search title.)
TITLE_BLOCK_PHRASES = [
    "you're doing that too much",
    "you are doing that too much",
    "too many requests",
    "rate limit",
    "rate-limit",
    "access denied",
    "blocked",
    "forbidden",
    "checking your browser",
]

# URL fragments that indicate we got bounced to a block / interstitial page.
BLOCK_URL_FRAGMENTS = [
    "/over18",
    "/blocked",
    "/quarantine",
    "challenge.cloudflare",
    "/login?dest=",  # forced-login redirect when search is rate-limited
]

# Specific containers that, on old.reddit.com, only ever exist on error pages.
# We scan their inner text against BLOCK_PHRASES; this is much narrower than
# scanning the entire <body>, which would echo the user's search query.
ERROR_CONTAINER_SELECTORS = [
    ".error-page",
    ".error",
    "#error",
    ".interstitial",
    ".message",
    "h1.error",
    "div.error",
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

    # ------------------------------------------------------------------ public API

    def search(  # type: ignore[override]
        self,
        query: str,
        limit: int = 10,
        *,
        mode: str = "search",
        comment_limit: int = 20,
    ) -> list[SearchResult]:
        """Run a Reddit query.

        Modes:
          * ``"search"`` (default): legacy ``old.reddit.com/search``
            keyword search via the BaseEngine retry loop.
          * ``"post"``: treat ``query`` as a post URL (any of
            ``reddit.com/r/<sub>/comments/<id>/<slug>``,
            ``reddit.com/comments/<id>``, ``redd.it/<id>``) or a bare
            base36 post id; fetch ``/comments/<id>.json`` (Reddit's
            official public JSON, no API key) and return one
            SearchResult for the post + ``comment_limit`` results for
            the top comments. Extracts media URLs (i.redd.it / v.redd.it
            fallback_url / gallery_data) the same way PRAW does.
        """
        self.last_status = {"mode_requested": mode}
        m = (mode or "search").lower()
        if m == "post":
            return self._mode_post(query, comment_limit)
        return super().search(query, limit)

    def fetch_post(self, url_or_id: str, *, comment_limit: int = 20) -> dict:
        """Fetch a single Reddit post + its top comments via ``.json``.

        ``url_or_id`` may be a full reddit URL, a ``redd.it/<id>`` short URL,
        or a bare base36 post id. Returns ``{}`` on failure. Otherwise:

          {
              "id": <base36>,
              "url": <permalink>,
              "subreddit": "r/<sub>",
              "title": ..., "author": ..., "score": int, "num_comments": int,
              "created_utc": float, "selftext": str, "is_self": bool,
              "is_video": bool, "is_gallery": bool, "over_18": bool,
              "link_url": str (external when not self),
              "image_urls": [str, ...],
              "video_url": str,           # mp4 for v.redd.it
              "gallery": [{"url": str, "media_id": str}, ...],
              "comments": [
                  {"id", "author", "score", "body", "created_utc", "depth",
                   "permalink", "replies": [...]}, ...
              ],
          }
        """
        post_id = self._normalise_post_id(url_or_id)
        if not post_id:
            return {}
        return self._fetch_post_json(post_id, comment_limit=comment_limit)

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
        """Detect rate-limit / Cloudflare / blocked interstitials.

        We deliberately avoid scanning the full <body> text because the search
        page echoes the user's query back into the body (e.g. "search results
        for: <query>"). That would cause every query containing words like
        "rate limit" or "blocked" to false-positive as a block.

        Instead we use three narrow, query-independent signals:
          1. The presence of any result container → definitely NOT blocked.
          2. The page URL → block / login-redirect / Cloudflare paths.
          3. The page title → short, doesn't echo the query.
          4. The inner-text of error-only containers (.error, .interstitial...).
        """
        # Signal 1: if any result container is present, we're on a normal
        # search results page (even if the result count is 0). Bail early.
        for sel in RESULT_SELECTORS + THING_SELECTORS:
            try:
                if self.page.query_selector_all(sel):
                    self.last_status = {"block_reason": None, "results_present": True}
                    return False
            except Exception:
                pass

        try:
            url = (self.page.url or "").lower()
        except Exception:
            url = ""
        try:
            title = (self.page.title() or "").lower()
        except Exception:
            title = ""

        self.last_status = {
            "url": url,
            "title": title,
        }

        # Signal 2: URL-based detection (most reliable).
        for frag in BLOCK_URL_FRAGMENTS:
            if frag in url:
                log.warning("[reddit] block URL fragment detected: %r", frag)
                self.last_status["block_reason"] = f"url:{frag}"
                return True

        # Signal 3: title-based detection (titles are short, don't echo query).
        for phrase in TITLE_BLOCK_PHRASES:
            if phrase in title:
                log.warning("[reddit] block title phrase detected: %r", phrase)
                self.last_status["block_reason"] = f"title:{phrase}"
                return True

        # Signal 4: scan ONLY the inner-text of known error containers, not
        # the whole body. These containers don't exist on a normal search page.
        for err_sel in ERROR_CONTAINER_SELECTORS:
            try:
                el = self.page.query_selector(err_sel)
                if not el:
                    continue
                err_text = (el.inner_text() or "").lower()
            except Exception:
                continue
            if not err_text:
                continue
            for phrase in BLOCK_PHRASES:
                if phrase in err_text:
                    log.warning(
                        "[reddit] block phrase %r detected in %s",
                        phrase,
                        err_sel,
                    )
                    self.last_status["block_reason"] = f"{err_sel}:{phrase}"
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


    # ============================================================
    # Post-detail mode (mode="post") — uses the .json public endpoint
    # ============================================================

    POST_JSON_URL = "https://www.reddit.com/comments/{post_id}.json?limit={limit}&raw_json=1"
    POST_JSON_URL_OLD = "https://old.reddit.com/comments/{post_id}.json?limit={limit}&raw_json=1"

    def _mode_post(
        self, query: str, comment_limit: int,
    ) -> list[SearchResult]:
        post_id = self._normalise_post_id(query)
        if not post_id:
            self.last_status["error"] = "invalid_post_id"
            return []
        data = self._fetch_post_json(post_id, comment_limit=comment_limit)
        if not data:
            return []

        out: list[SearchResult] = []

        # 1) The post itself, with media URLs surfaced.
        head = []
        if data.get("subreddit"):
            head.append(data["subreddit"])
        if data.get("author"):
            head.append(f"u/{data['author']}")
        if data.get("score") is not None:
            head.append(f"{data['score']} pts")
        if data.get("num_comments") is not None:
            head.append(f"{data['num_comments']} comments")
        if data.get("video_url"):
            head.append("video")
        elif data.get("image_urls"):
            head.append(f"{len(data['image_urls'])} image{'s' if len(data['image_urls']) > 1 else ''}")
        snippet = " · ".join(head)
        if data.get("selftext"):
            snippet = snippet + " — " + data["selftext"][:240]
        elif data.get("link_url") and not data.get("is_self"):
            snippet = snippet + " — " + data["link_url"][:200]

        post_result = SearchResult(
            title=(data.get("title") or "Reddit post")[:200],
            url=data.get("url") or "",
            snippet=snippet[:400],
            score=data.get("score"),
        )
        post_result.post_id = data.get("id")              # type: ignore[attr-defined]
        post_result.subreddit = data.get("subreddit", "") # type: ignore[attr-defined]
        post_result.author = data.get("author", "")       # type: ignore[attr-defined]
        post_result.score_num = data.get("score")         # type: ignore[attr-defined]
        post_result.num_comments = data.get("num_comments")  # type: ignore[attr-defined]
        post_result.created_utc = data.get("created_utc") # type: ignore[attr-defined]
        post_result.selftext = data.get("selftext", "")   # type: ignore[attr-defined]
        post_result.is_self = data.get("is_self", False)  # type: ignore[attr-defined]
        post_result.is_video = data.get("is_video", False)  # type: ignore[attr-defined]
        post_result.is_gallery = data.get("is_gallery", False)  # type: ignore[attr-defined]
        post_result.over_18 = data.get("over_18", False)  # type: ignore[attr-defined]
        post_result.link_url = data.get("link_url", "")   # type: ignore[attr-defined]
        post_result.image_urls = data.get("image_urls") or []  # type: ignore[attr-defined]
        post_result.video_url = data.get("video_url", "") # type: ignore[attr-defined]
        post_result.gallery = data.get("gallery") or []   # type: ignore[attr-defined]
        post_result.kind = "post"                         # type: ignore[attr-defined]
        out.append(post_result)

        # 2) Top comments as separate result entries (capped by comment_limit).
        for c in (data.get("comments") or [])[:comment_limit]:
            head = []
            if c.get("author"):
                head.append(f"u/{c['author']}")
            if c.get("score") is not None:
                head.append(f"{c['score']} pts")
            if c.get("depth") is not None:
                head.append(f"depth={c['depth']}")
            cmnt_snippet = " · ".join(head)
            body = (c.get("body") or "").strip()
            if body:
                cmnt_snippet = cmnt_snippet + " — " + body[:280]

            r = SearchResult(
                title=(body[:180] if body else "[deleted comment]"),
                url=c.get("permalink") or "",
                snippet=cmnt_snippet[:400],
                score=c.get("score"),
            )
            r.post_id = c.get("id")                       # type: ignore[attr-defined]
            r.subreddit = data.get("subreddit", "")       # type: ignore[attr-defined]
            r.author = c.get("author", "")                # type: ignore[attr-defined]
            r.score_num = c.get("score")                  # type: ignore[attr-defined]
            r.body = body                                 # type: ignore[attr-defined]
            r.created_utc = c.get("created_utc")          # type: ignore[attr-defined]
            r.depth = c.get("depth")                      # type: ignore[attr-defined]
            r.kind = "comment"                            # type: ignore[attr-defined]
            out.append(r)
        self.last_status["mode"] = "post"
        return out

    def _normalise_post_id(self, query: str) -> str:
        """Accept a full reddit URL, ``redd.it/<id>``, or bare ``<id>``."""
        if not query:
            return ""
        q = query.strip()
        # bare base36 id (5–8 chars typically)
        if re.fullmatch(r"[a-z0-9]{5,10}", q):
            return q
        # redd.it short URL
        m = re.match(r"https?://(?:www\.)?redd\.it/([a-z0-9]+)", q, re.I)
        if m:
            return m.group(1)
        # /comments/<id>/<slug> in any reddit host
        m = re.search(
            r"reddit\.com/(?:r/[^/]+/)?comments/([a-z0-9]+)",
            q, re.I,
        )
        if m:
            return m.group(1)
        return ""

    def _fetch_post_json(
        self, post_id: str, *, comment_limit: int,
    ) -> dict:
        """Hit ``/comments/<id>.json`` (no API key needed) and parse.

        Tries ``www.reddit.com`` first, falls back to ``old.reddit.com``
        if the canonical host throttles. Uses the same warm-up trick as
        the search path (visit www.reddit.com first so js_challenge
        cookies land) — but only when the engine has been live for less
        than a single request, since we don't want to redo the warm-up
        every call inside a hot loop.
        """
        # Best-effort warm-up: we do this on a single page so the cookie
        # carries over to the JSON request.
        if "reddit.com" not in (self.page.url or "").lower():
            try:
                safe_goto(self.page, WARMUP_URL, timeout=20000, retries=1)
                human_delay(0.6, 1.2)
            except Exception:
                pass

        url_primary = self.POST_JSON_URL.format(
            post_id=post_id, limit=max(comment_limit, 50)
        )
        url_fallback = self.POST_JSON_URL_OLD.format(
            post_id=post_id, limit=max(comment_limit, 50)
        )

        body_text = ""
        for url in (url_primary, url_fallback):
            try:
                resp = self.page.request.get(
                    url,
                    headers={
                        "Accept": "application/json",
                        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                                       "Chrome/142.0.0.0 Safari/537.36 "
                                       "AgentSearch/1.0",
                    },
                    timeout=20000,
                )
            except Exception as e:
                log.debug("[reddit] post JSON fetch failed (%s): %s", url, e)
                continue
            if resp.status == 200:
                body_text = resp.text() or ""
                break
            log.debug("[reddit] post JSON %s -> %s", url, resp.status)
        if not body_text:
            return {}

        import json as _json
        try:
            data = _json.loads(body_text)
        except _json.JSONDecodeError as e:
            log.debug("[reddit] post JSON parse failed: %s", e)
            return {}

        if not isinstance(data, list) or len(data) < 1:
            return {}

        # data[0] = post listing, data[1] = comments listing (when present).
        post_listing = data[0] or {}
        comments_listing = data[1] if len(data) > 1 else None

        try:
            post = (
                post_listing.get("data", {})
                .get("children", [{}])[0]
                .get("data", {})
            )
        except (AttributeError, IndexError):
            return {}
        if not post:
            return {}

        # Build the post record.
        permalink = post.get("permalink") or ""
        full_url = (
            f"https://www.reddit.com{permalink}" if permalink else
            f"https://www.reddit.com/comments/{post_id}"
        )

        out = {
            "id": post.get("id") or post_id,
            "url": full_url,
            "subreddit": post.get("subreddit_name_prefixed") or "",
            "title": post.get("title") or "",
            "author": post.get("author") or "",
            "score": post.get("score"),
            "num_comments": post.get("num_comments"),
            "created_utc": post.get("created_utc"),
            "selftext": post.get("selftext") or "",
            "is_self": bool(post.get("is_self")),
            "is_video": bool(post.get("is_video")),
            "is_gallery": bool(post.get("is_gallery")),
            "over_18": bool(post.get("over_18")),
            "link_url": post.get("url") or "",
            "image_urls": [],
            "video_url": "",
            "gallery": [],
        }

        # Media extraction — same fields PRAW exposes via Submission.
        out.update(self._extract_post_media(post))

        # Comments — flat-walk the tree, tagging depth.
        if comments_listing:
            comments = self._extract_comments(comments_listing, comment_limit)
            out["comments"] = comments
        else:
            out["comments"] = []

        return out

    @staticmethod
    def _extract_post_media(post: dict) -> dict:
        """Pull image_urls / video_url / gallery from a post dict.

        Reddit's media schema has 5 mutually-non-exclusive places media can
        live. We check each:
          1. ``post.url`` ends with .jpg/.png/.gif/.gifv (direct i.redd.it).
          2. ``post.preview.images[].source.url`` (preview images, html-encoded).
          3. ``post.media.reddit_video.fallback_url`` (v.redd.it mp4).
          4. ``post.secure_media.reddit_video.fallback_url`` (HTTPS mp4).
          5. ``post.is_gallery`` + ``post.gallery_data.items`` +
             ``post.media_metadata`` (multi-image gallery).
        """
        import html as _html
        image_urls: list[str] = []
        video_url = ""
        gallery: list[dict] = []

        link = post.get("url") or ""
        # Direct image link.
        if any(link.lower().endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp")):
            image_urls.append(link)
        # .gifv / Imgur etc — keep as link only.

        # Preview images (html-encoded URLs in JSON).
        try:
            for img in (post.get("preview") or {}).get("images") or []:
                src = (img.get("source") or {}).get("url")
                if src:
                    image_urls.append(_html.unescape(src))
        except Exception:
            pass

        # Reddit-hosted video.
        for key in ("media", "secure_media"):
            rv = (post.get(key) or {}).get("reddit_video") if post.get(key) else None
            if rv:
                fb = rv.get("fallback_url") or ""
                if fb and not video_url:
                    video_url = fb
                break

        # Galleries.
        if post.get("is_gallery"):
            try:
                items = (post.get("gallery_data") or {}).get("items") or []
                meta = post.get("media_metadata") or {}
                for it in items:
                    mid = it.get("media_id") or ""
                    md = meta.get(mid) or {}
                    s = (md.get("s") or {}).get("u") or (md.get("s") or {}).get("gif") or ""
                    if s:
                        s = _html.unescape(s)
                        gallery.append({"url": s, "media_id": mid})
                        image_urls.append(s)
            except Exception:
                pass

        # De-duplicate image_urls preserving order.
        seen: set = set()
        deduped: list[str] = []
        for u in image_urls:
            if u in seen:
                continue
            seen.add(u)
            deduped.append(u)

        return {
            "image_urls": deduped,
            "video_url": video_url,
            "gallery": gallery,
        }

    @staticmethod
    def _extract_comments(comments_listing: dict, max_total: int) -> list[dict]:
        """Flatten the comment tree into a list of dicts (depth-first).

        Stops once ``max_total`` comments have been collected to bound size.
        Skips ``more`` placeholder nodes (they require another XHR to expand
        — we keep things to a single round-trip).
        """
        out: list[dict] = []
        children = (comments_listing.get("data") or {}).get("children") or []

        def _walk(nodes, depth):
            if len(out) >= max_total:
                return
            for n in nodes or []:
                if len(out) >= max_total:
                    return
                if not isinstance(n, dict):
                    continue
                kind = n.get("kind")
                if kind == "more":
                    # Skip placeholder; would require a second XHR.
                    continue
                if kind != "t1":
                    continue
                d = n.get("data") or {}
                replies = d.get("replies") or {}
                inner = (
                    replies.get("data", {}).get("children")
                    if isinstance(replies, dict) else None
                )
                permalink = d.get("permalink") or ""
                full_link = (
                    f"https://www.reddit.com{permalink}" if permalink else ""
                )
                out.append({
                    "id": d.get("id"),
                    "author": d.get("author") or "",
                    "score": d.get("score"),
                    "body": d.get("body") or "",
                    "created_utc": d.get("created_utc"),
                    "depth": depth,
                    "permalink": full_link,
                })
                if inner:
                    _walk(inner, depth + 1)

        _walk(children, 0)
        return out
