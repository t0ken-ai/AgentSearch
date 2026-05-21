"""Hacker News search adapter.

Hacker News (news.ycombinator.com) does not host its own search engine —
the site footer's "Search" link points at https://hn.algolia.com which
indexes every story / comment via Algolia. We layer three modes,
mirroring the ``stackoverflow.py`` strategy:

1. **hn_algolia_api** — Hit the public Algolia HN API directly:
   ``https://hn.algolia.com/api/v1/search?query=<q>&tags=story``
   (also supports ``search_by_date`` for chronological sort). Returns
   JSON with ``hits[]`` containing ``title``, ``url``, ``points``,
   ``num_comments``, ``author``, ``objectID``, ``created_at``. No
   auth, no rate limit visible in headers (Algolia front-end uses
   ~10k req/s caps); this is the cleanest, most stable path. We
   navigate the cloak browser to the API URL and parse the JSON body
   exactly the way ``stackoverflow.py`` parses the Stack Exchange API.

2. **hn_algolia_html** — Navigate to ``https://hn.algolia.com/?q=<q>``
   (the user-facing HN Search SPA). After JS executes, results render
   as ``.Story`` containers with ``.Story_title a`` (title + link),
   ``.Story_meta`` (points / comments / author / age). We wait for
   the SPA to populate, then extract from the DOM. Used when the API
   host returns non-JSON or is intercepted by a proxy.

3. **ddg_site** — Last-resort fallback via the HTML-only DuckDuckGo
   endpoint with ``site:news.ycombinator.com <query>``. We can pull
   title + URL + a coarse snippet but no points / comments.

Each mode short-circuits on success. If a mode is blocked or returns
0 parseable results, the adapter falls through to the next.

``SearchResult`` (see ``base.py``) carries ``title`` / ``url`` /
``snippet`` / ``score``. To preserve points + comments + author:

* ``score`` holds the points count (HN's primary metric, identical
  to votes minus flags). Negative / missing values become ``None``.
* ``snippet`` is composed as
  ``"by <author> · <N> comments · <age>"`` — any missing part is
  omitted, separator collapses cleanly.
* In addition, every returned ``SearchResult`` has the following
  attributes set dynamically (the dataclass has no ``__slots__`` so
  this is a supported extension):
    - ``points`` (int | None) — same as ``score``.
    - ``comments`` (int | None) — comment count.
    - ``author`` (str) — submitter username.
    - ``object_id`` (str) — Algolia objectID, equal to HN item id.
    - ``hn_url`` (str) — discussion permalink
      ``https://news.ycombinator.com/item?id=<id>``. ``url`` itself
      points to the story's external URL when present, falling back
      to ``hn_url`` for "Ask HN" / "Show HN" / dead links.
    - ``created_at`` (int | None) — ``created_at_i`` (unix seconds).

Diagnostics
-----------

* ``engine.last_status`` — ``mode``, ``url``, ``title``, ``body_len``,
  optional ``selector`` / ``block_reason`` / ``api_nb_hits`` /
  ``api_pages`` / ``count`` / ``pages_fetched``.
* ``engine.selector_counts()`` — per-selector counts useful across all
  three modes so test scripts can show why parsing missed.
"""

from __future__ import annotations

import html
import json
import logging
import random
import re
import time
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

HN_HOME = "https://news.ycombinator.com"
HN_ITEM_URL = HN_HOME + "/item?id="

# ---- hn_algolia_api --------------------------------------------------------

ALGOLIA_API_BASE = "https://hn.algolia.com/api/v1"
# /search uses Algolia "ranking by relevance" weighted by popularity.
# /search_by_date sorts strictly by created_at desc — relevance still
# applies, just secondary. We default to /search (relevance) and let
# callers override via the engine attribute if they want chronological.
ALGOLIA_SEARCH_PATH = "/search"

# ---- hn_algolia_html -------------------------------------------------------

ALGOLIA_HTML_BASE = "https://hn.algolia.com"
# SPA result containers, in priority order (the React app may rev
# its class names; we list both observed variants).
HTML_RESULT_SELECTORS = [
    ".Story",
    "article.Story",
    ".SearchResults .Story",
    "li.Story",
]

# ---- ddg_site --------------------------------------------------------------

DDG_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"

# Phrases that indicate Algolia / Cloudflare blocked us.
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
]

# Hard upper bounds on pagination.
MAX_API_PAGES = 10  # Algolia caps at 1000 hits anyway (page * hitsPerPage).


# ----------------------------------------------------------------------------


def _abs_hn(href: str) -> str:
    """Normalize a relative HN URL to an absolute one."""
    if not href:
        return href
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return HN_HOME + href
    return HN_HOME + "/" + href


def _parse_int(text: str) -> int | None:
    """Parse a points / comments string ('123', '1,234', '1.2k')."""
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


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    """Strip HTML tags + collapse whitespace.

    Algolia's ``_highlightResult.title.value`` wraps query terms in
    ``<em>`` tags. We use the un-highlighted ``title`` field directly
    when possible, but keep this helper as a safety net.
    """
    if not text:
        return text
    no_tags = _HTML_TAG_RE.sub("", text)
    return re.sub(r"\s+", " ", no_tags).strip()


def _compose_snippet(author: str, comments: int | None, age: str) -> str:
    """Return ``'by <author> · <N> comments · <age>'`` (parts omitted when empty)."""
    parts: list[str] = []
    if author:
        parts.append(f"by {author}")
    if comments is not None:
        parts.append(f"{comments} comments")
    if age:
        parts.append(age)
    return " · ".join(parts)


def _format_age(created_at_i: int | None) -> str:
    """Render a unix timestamp as ``'<n> <unit> ago'`` (best-effort)."""
    if not created_at_i:
        return ""
    try:
        delta = max(0, int(time.time()) - int(created_at_i))
    except (TypeError, ValueError):
        return ""
    if delta < 60:
        return f"{delta}s ago"
    if delta < 3600:
        return f"{delta // 60}m ago"
    if delta < 86400:
        return f"{delta // 3600}h ago"
    if delta < 86400 * 30:
        return f"{delta // 86400}d ago"
    if delta < 86400 * 365:
        return f"{delta // (86400 * 30)}mo ago"
    return f"{delta // (86400 * 365)}y ago"


def _attach_extras(
    r: SearchResult,
    *,
    points: int | None,
    comments: int | None,
    author: str,
    object_id: str,
    hn_url: str,
    created_at: int | None,
) -> SearchResult:
    """Attach HN-specific extension fields to a SearchResult instance.

    ``SearchResult`` is a regular dataclass (no ``__slots__``), so
    dynamic attributes are valid. Doing this in one helper keeps the
    extraction code in each mode tidy and ensures every code path
    populates the same set of fields.
    """
    r.points = points
    r.comments = comments
    r.author = author
    r.object_id = object_id
    r.hn_url = hn_url
    r.created_at = created_at
    return r


# ----------------------------------------------------------------------------


class HackerNewsEngine(BaseEngine):
    name = "hackernews"
    max_retries = 3

    def __init__(self, page):
        super().__init__(page)
        # Diagnostics for callers / tests.
        self.last_status: dict = {}
        # Set after each attempt so selector_counts() / tests can inspect it.
        self._last_mode: str = "hn_algolia_api"
        self._pages_fetched: int = 0
        # Allow callers to opt into chronological sort (sets API path
        # to /search_by_date). Default is /search (relevance).
        self.sort_by_date: bool = False

    # ------------------------------------------------------------------ search

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # 1) Algolia HN API — primary, structured JSON, no auth.
        try:
            results = self._try_algolia_api(query, limit)
        except Exception as e:
            log.warning("[hn] hn_algolia_api raised: %s", e)
            results = []
        if results:
            self._last_mode = "hn_algolia_api"
            return results

        # 2) hn.algolia.com SPA — same data, scraped from DOM.
        try:
            results = self._try_algolia_html(query, limit)
        except Exception as e:
            log.warning("[hn] hn_algolia_html raised: %s", e)
            results = []
        if results:
            self._last_mode = "hn_algolia_html"
            return results

        # 3) DuckDuckGo HTML site: last resort.
        try:
            results = self._try_ddg_site(query, limit)
        except Exception as e:
            log.warning("[hn] ddg_site raised: %s", e)
            results = []
        if results:
            self._last_mode = "ddg_site"
            return results

        return []

    # --------------------------------------------------- hn_algolia_api mode

    def _try_algolia_api(self, query: str, limit: int) -> list[SearchResult]:
        """Fetch from Algolia HN ``/search`` (or ``/search_by_date``).

        We navigate the browser to the API URL and parse the JSON body.
        Pagination is handled via ``&page=N`` until ``page+1 >= nbPages``
        or ``limit`` is satisfied.
        """
        results: list[SearchResult] = []
        seen_ids: set[str] = set()
        self._pages_fetched = 0
        page_size = max(min(limit, 100), 5)  # Algolia hitsPerPage: 1..1000.
        path = (
            "/search_by_date" if self.sort_by_date else ALGOLIA_SEARCH_PATH
        )

        for api_page in range(0, MAX_API_PAGES):  # Algolia is 0-indexed.
            params = {
                "query": query,
                # tags=story restricts hits to story posts (not comments,
                # polls, or job posts). The tag system is OR-able with
                # commas; "story" alone is what HN's own search uses.
                "tags": "story",
                "hitsPerPage": str(page_size),
                "page": str(api_page),
            }
            url = (
                f"{ALGOLIA_API_BASE}{path}?"
                + urllib.parse.urlencode(params)
            )
            log.info("[hn] api page %d: %s", api_page, url)
            if not safe_goto(self.page, url, timeout=25000, retries=1):
                self.last_status = {
                    "mode": "hn_algolia_api",
                    "error": "goto_failed",
                }
                return results

            self._pages_fetched = api_page + 1
            human_delay(0.3, 0.8)

            payload = self._read_json_body()
            if not payload:
                # Empty / non-JSON response (Cloudflare gate, captive
                # portal, …). Bail to next mode.
                self.last_status = {
                    "mode": "hn_algolia_api",
                    "url": url,
                    "error": "non_json_body",
                }
                return results

            self.last_status = {
                "mode": "hn_algolia_api",
                "url": url,
                "selector": "json",
                "api_nb_hits": payload.get("nbHits"),
                "api_pages": payload.get("nbPages"),
                "body_len": payload.get("_body_len", 0),
            }
            if "message" in payload and not payload.get("hits"):
                # Algolia returns ``{"message": "...", "status": 4xx}``
                # on errors (rate-limit, bad params).
                self.last_status["block_reason"] = str(
                    payload.get("message")
                )
                log.warning("[hn] api error: %s", self.last_status["block_reason"])
                return results

            hits = payload.get("hits") or []
            log.info("[hn] api page %d returned %d hits", api_page, len(hits))
            new_added = 0
            for it in hits:
                obj_id = str(it.get("objectID") or "")
                if not obj_id or obj_id in seen_ids:
                    continue

                # Algolia "story" hits use ``title``; very rarely a
                # /search hit can be a comment with ``story_title``.
                # We restrict to tags=story so the latter is unusual,
                # but cover it anyway.
                title = _strip_html(
                    html.unescape(
                        it.get("title")
                        or it.get("story_title")
                        or ""
                    )
                )
                if not title:
                    continue

                ext_url = it.get("url") or it.get("story_url") or ""
                hn_url = HN_ITEM_URL + obj_id
                # Prefer the external URL when present; fall back to
                # the HN discussion permalink for "Ask HN" / "Show HN"
                # / dead links.
                final_url = ext_url or hn_url

                points_raw = it.get("points")
                points = (
                    int(points_raw)
                    if isinstance(points_raw, (int, float))
                    else None
                )
                num_comments_raw = it.get("num_comments")
                num_comments = (
                    int(num_comments_raw)
                    if isinstance(num_comments_raw, (int, float))
                    else None
                )
                author = (it.get("author") or "").strip()
                created_i_raw = it.get("created_at_i")
                created_i = (
                    int(created_i_raw)
                    if isinstance(created_i_raw, (int, float))
                    else None
                )
                age = _format_age(created_i)

                seen_ids.add(obj_id)
                r = SearchResult(
                    title=title,
                    url=final_url,
                    snippet=_compose_snippet(author, num_comments, age),
                    score=points,
                )
                _attach_extras(
                    r,
                    points=points,
                    comments=num_comments,
                    author=author,
                    object_id=obj_id,
                    hn_url=hn_url,
                    created_at=created_i,
                )
                results.append(r)
                new_added += 1
                if len(results) >= limit:
                    break

            if len(results) >= limit:
                break
            nb_pages = payload.get("nbPages")
            if nb_pages is not None and api_page + 1 >= nb_pages:
                log.info(
                    "[hn] api: page+1=%d >= nbPages=%d, stopping",
                    api_page + 1,
                    nb_pages,
                )
                break
            if new_added == 0:
                log.info("[hn] api: no new hits on page %d, stopping", api_page)
                break

        self._last_mode = "hn_algolia_api"
        if results:
            self.last_status["mode"] = "hn_algolia_api"
            self.last_status["count"] = len(results)
            self.last_status["pages_fetched"] = self._pages_fetched
        return results

    def _read_json_body(self) -> dict | None:
        """Pull JSON from the current page body, tolerating Chrome's pretty-print.

        Chrome wraps JSON responses in a viewer ``<pre>`` element; both
        ``page.inner_text('body')`` and the underlying ``<pre>`` text give
        the raw JSON document, so we try ``<pre>`` first and fall back to
        the body text.
        """
        raw = ""
        try:
            pre = self.page.query_selector("pre")
            if pre:
                raw = (pre.inner_text() or "").strip()
        except Exception:
            raw = ""
        if not raw:
            try:
                raw = (self.page.inner_text("body") or "").strip()
            except Exception:
                raw = ""
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            log.warning(
                "[hn] api JSON decode failed: %s; head=%r",
                e,
                raw[:200],
            )
            return None
        if not isinstance(data, dict):
            return None
        data["_body_len"] = len(raw)
        return data

    # -------------------------------------------------- hn_algolia_html mode

    def _try_algolia_html(self, query: str, limit: int) -> list[SearchResult]:
        """Scrape hn.algolia.com's React SPA after it renders results."""
        q = urllib.parse.quote(query)
        url = f"{ALGOLIA_HTML_BASE}/?q={q}&sort=byPopularity&type=story"
        log.info("[hn] algolia html search: %s", url)
        if not safe_goto(self.page, url, timeout=25000, retries=1):
            self.last_status = {
                "mode": "hn_algolia_html",
                "error": "goto_failed",
            }
            return []

        # SPA needs a moment to fetch + render results. Wait for at least
        # one .Story container; tolerate timeout (we'll fall through if
        # nothing appears).
        try:
            self.page.wait_for_selector(".Story", timeout=12000)
        except Exception:
            pass

        human_delay(1.2, 2.5)
        self._human_hints()

        if self._is_blocked("hn_algolia_html"):
            return []

        items = []
        used = None
        for sel in HTML_RESULT_SELECTORS:
            try:
                items = self.page.query_selector_all(sel)
            except Exception:
                items = []
            if items:
                used = sel
                break
        if not items:
            self.last_status.setdefault("mode", "hn_algolia_html")
            self.last_status["count"] = 0
            return []

        log.info(
            "[hn] algolia html via %s (%d items)", used, len(items)
        )
        self.last_status["selector"] = used

        results: list[SearchResult] = []
        seen_ids: set[str] = set()
        for r in items[: limit * 3]:
            link_el = (
                r.query_selector(".Story_title a")
                or r.query_selector("a.Story_title")
                or r.query_selector("h2 a")
                or r.query_selector("a")
            )
            if not link_el:
                continue
            try:
                title = (link_el.inner_text() or "").strip()
                href = link_el.get_attribute("href") or ""
            except Exception:
                continue
            if not title or not href:
                continue

            # Pull the comments / discussion link to recover the HN id.
            obj_id = ""
            try:
                meta_links = r.query_selector_all(
                    ".Story_meta a, a"
                )
            except Exception:
                meta_links = []
            for ml in meta_links:
                try:
                    href2 = ml.get_attribute("href") or ""
                except Exception:
                    href2 = ""
                m = re.search(r"item\?id=(\d+)", href2)
                if m:
                    obj_id = m.group(1)
                    break
            if obj_id and obj_id in seen_ids:
                continue

            # Points and comments from .Story_meta — text shape:
            # "123 points | by user | 5h ago | 42 comments"
            full_meta = ""
            try:
                meta_el = r.query_selector(".Story_meta")
                if meta_el:
                    full_meta = (meta_el.inner_text() or "").strip()
            except Exception:
                full_meta = ""

            points = None
            mp = re.search(r"(-?[\d,.]+\s*[km]?)\s*points?", full_meta, re.I)
            if mp:
                points = _parse_int(mp.group(1))
            num_comments = None
            mc = re.search(
                r"([\d,.]+\s*[km]?)\s*comments?", full_meta, re.I
            )
            if mc:
                num_comments = _parse_int(mc.group(1))

            author = ""
            ma = re.search(r"by\s+([^\s|·]+)", full_meta, re.I)
            if ma:
                author = ma.group(1).strip()

            age = ""
            mage = re.search(
                r"\b(\d+\s*(?:s|m|h|d|mo|y|sec|min|hour|day|month|year)s?\s*ago)\b",
                full_meta,
                re.I,
            )
            if mage:
                age = mage.group(1).strip()

            hn_url = HN_ITEM_URL + obj_id if obj_id else ""
            final_url = href if not href.startswith("/") else _abs_hn(href)
            if not obj_id and "item?id=" in final_url:
                m2 = re.search(r"item\?id=(\d+)", final_url)
                if m2:
                    obj_id = m2.group(1)
                    hn_url = HN_ITEM_URL + obj_id

            if obj_id:
                seen_ids.add(obj_id)

            sr = SearchResult(
                title=title,
                url=final_url,
                snippet=_compose_snippet(author, num_comments, age),
                score=points,
            )
            _attach_extras(
                sr,
                points=points,
                comments=num_comments,
                author=author,
                object_id=obj_id,
                hn_url=hn_url or final_url,
                created_at=None,
            )
            results.append(sr)
            if len(results) >= limit:
                break

        self._last_mode = "hn_algolia_html"
        if results:
            self.last_status["mode"] = "hn_algolia_html"
            self.last_status["count"] = len(results)
        return results

    # -------------------------------------------------------- ddg_site mode

    def _try_ddg_site(self, query: str, limit: int) -> list[SearchResult]:
        site_query = f"site:news.ycombinator.com {query}"
        q = urllib.parse.quote(site_query)
        url = f"{DDG_HTML_ENDPOINT}?q={q}"
        log.info("[hn] ddg site search: %s", url)
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
        seen_ids: set[str] = set()
        try:
            items = self.page.query_selector_all(".result")
        except Exception:
            items = []
        log.info("[hn] ddg got %d .result items", len(items))

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
            if "news.ycombinator.com" not in href.lower():
                continue

            obj_id = ""
            m = re.search(r"item\?id=(\d+)", href)
            if m:
                obj_id = m.group(1)
            if obj_id and obj_id in seen_ids:
                continue
            if obj_id:
                seen_ids.add(obj_id)

            sr = SearchResult(
                title=title,
                url=href,
                snippet=snippet,
                score=None,
            )
            _attach_extras(
                sr,
                points=None,
                comments=None,
                author="",
                object_id=obj_id,
                hn_url=HN_ITEM_URL + obj_id if obj_id else href,
                created_at=None,
            )
            results.append(sr)
            if len(results) >= limit:
                break

        self._last_mode = "ddg_site"
        if results:
            self.last_status["count"] = len(results)
        return results

    # -------------------------------------------------------- block detection

    def _is_blocked(self, mode: str) -> bool:
        """Detect Cloudflare / Algolia interstitials and rate-limits."""
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

        head = body[:3000]
        for phrase in BLOCK_PHRASES:
            if phrase in head or phrase in title:
                self.last_status["block_reason"] = phrase
                log.warning("[hn] block phrase detected: %r", phrase)
                return True
        return False

    # -------------------------------------------------------------- diagnostics

    def selector_counts(self) -> dict[str, int]:
        """Per-selector counts; safe to call regardless of last_mode."""
        counts: dict[str, int] = {}
        # Always probe the cross-mode selectors so a failure mode is
        # easy to identify from a single diagnostic dump.
        for sel in (
            ".Story",
            ".Story_title a",
            ".Story_meta",
            "pre",
            ".result",
            ".result__a",
            ".result__snippet",
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
