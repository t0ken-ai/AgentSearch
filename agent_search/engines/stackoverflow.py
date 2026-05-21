"""Stack Overflow search adapter.

Stack Overflow's ``/search?q=`` endpoint is gated by an anti-bot layer:
headless requests are redirected to ``/nocaptcha?s=...`` ("Human
verification - Stack Overflow") with effectively no public bypass. To
make the adapter useful out of the box this module layers three modes,
mirroring the ``blackhatworld.py`` strategy:

1. **so_direct** — Hit ``https://stackoverflow.com/search?q=<query>`` and
   parse the modern Stacks 2.0 ``div.s-post-summary`` containers (or the
   legacy ``div.question-summary`` containers as a fallback). Only
   succeeds when SO doesn't bounce us to ``/nocaptcha``. This is the
   "happy path" the task spec asks for.

2. **so_api** — Use the Stack Exchange public API
   (``https://api.stackexchange.com/2.3/search/excerpts``). Requires no
   authentication, returns JSON with ``title``, ``score`` (votes),
   ``tags``, ``excerpt``, and ``question_id``. We navigate the cloak
   browser to the API URL and parse the JSON body — same browser stack
   as everything else, no extra network deps. Filters out
   ``item_type != "question"`` so answers / comments don't pollute
   results.

3. **ddg_site** — Final fallback via the HTML-only DuckDuckGo endpoint
   (``html.duckduckgo.com``) with ``site:stackoverflow.com <query>``.
   We can pull title + URL + a coarse snippet but no votes / tags.

Each mode short-circuits on success. If a mode is blocked or returns 0
parseable results, the adapter falls through to the next.

``SearchResult`` (see ``base.py``) carries ``title`` / ``url`` /
``snippet`` / ``score`` only. To preserve votes, tags and the excerpt:

* ``score`` holds the integer vote count (negative scores supported).
* ``snippet`` is composed as ``"[tag1, tag2, ...] · <excerpt>"``. Either
  half is omitted when missing, so ``ddg_site`` results with no tag
  data render as plain excerpts.

Diagnostics
-----------

* ``engine.last_status`` — ``mode``, ``url``, ``title``, ``body_len``,
  optional ``selector`` / ``layout`` / ``block_reason`` /
  ``api_quota_remaining`` / ``api_has_more`` / ``count`` /
  ``pages_fetched``.
* ``engine.selector_counts()`` — per-selector counts for whichever mode
  was attempted last so test scripts can show why parsing missed.
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

SO_HOME = "https://stackoverflow.com"

# ---- so_direct -------------------------------------------------------------

# Modern Stacks 2.0 result containers, in priority order.
MODERN_RESULT_SELECTORS = [
    "div.s-post-summary",
    ".js-search-results div.s-post-summary",
    "#questions div.s-post-summary",
]

# Legacy layout containers.
LEGACY_RESULT_SELECTORS = [
    "div.question-summary",
    ".js-search-results div.question-summary",
    "#questions div.question-summary",
]

# Phrases that indicate Stack Overflow / Cloudflare blocked us.
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

# How many pages we'll walk in so_direct mode if the caller asks for more
# results than fit on a single SO results page (SO renders 15 per page).
MAX_DIRECT_PAGES = 5

# ---- so_api ----------------------------------------------------------------

# Stack Exchange v2.3 API base. Search/excerpts returns the question
# title + score + tags + excerpt + question_id in a single hit, no auth
# required (300 requests / day per IP).
SE_API_BASE = "https://api.stackexchange.com/2.3"

# ---- ddg_site --------------------------------------------------------------

DDG_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"


# ----------------------------------------------------------------------------


def _abs_so(href: str) -> str:
    """Normalize a relative SO URL to an absolute one."""
    if not href:
        return href
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return SO_HOME + href
    return SO_HOME + "/" + href


def _parse_int(text: str) -> int | None:
    """Parse a vote/answer count string ('123', '-3', '1,234', '1.2k')."""
    if not text:
        return None
    t = text.strip().lower().replace(",", "")
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*([km])?", t)
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

    The Stack Exchange API embeds ``<span class="highlight">…</span>``
    markers in the ``excerpt`` field around words matching the query;
    we drop the markup so ``SearchResult.snippet`` stays plain text in
    line with the other engines.
    """
    if not text:
        return text
    no_tags = _HTML_TAG_RE.sub("", text)
    return re.sub(r"\s+", " ", no_tags).strip()


def _compose_snippet(tags: list[str], excerpt: str) -> str:
    """Return ``'[tag1, tag2] · <excerpt>'`` (parts omitted when empty)."""
    parts: list[str] = []
    if tags:
        parts.append("[" + ", ".join(tags) + "]")
    if excerpt:
        parts.append(excerpt)
    return " · ".join(parts)


# ----------------------------------------------------------------------------


class StackOverflowEngine(BaseEngine):
    name = "stackoverflow"
    max_retries = 3

    def __init__(self, page):
        super().__init__(page)
        # Diagnostics for callers / tests.
        self.last_status: dict = {}
        # Set after each attempt so selector_counts() / tests can inspect it.
        self._last_mode: str = "so_direct"
        self._last_layout: str = ""
        self._pages_fetched: int = 0

    # ------------------------------------------------------------------ search

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # 1) Try the native SO search first (matches the task spec).
        try:
            results = self._try_so_direct(query, limit)
        except Exception as e:
            log.warning("[so] so_direct raised: %s", e)
            results = []
        if results:
            self._last_mode = "so_direct"
            return results

        # 2) Stack Exchange API — reliable, structured, no auth.
        try:
            results = self._try_so_api(query, limit)
        except Exception as e:
            log.warning("[so] so_api raised: %s", e)
            results = []
        if results:
            self._last_mode = "so_api"
            return results

        # 3) DuckDuckGo HTML site: last-resort.
        try:
            results = self._try_ddg_site(query, limit)
        except Exception as e:
            log.warning("[so] ddg_site raised: %s", e)
            results = []
        if results:
            self._last_mode = "ddg_site"
            return results

        return []

    # -------------------------------------------------------- so_direct mode

    def _try_so_direct(self, query: str, limit: int) -> list[SearchResult]:
        # Warm-up on the homepage so cookies settle before we hit /search.
        if safe_goto(self.page, SO_HOME + "/", timeout=20000, retries=1):
            human_delay(1.0, 2.0)
            self._human_hints()

        q = urllib.parse.quote(query)
        first_url = f"{SO_HOME}/search?q={q}"
        log.info("[so] direct search: %s", first_url)
        if not safe_goto(self.page, first_url, timeout=30000):
            self.last_status = {"mode": "so_direct", "error": "goto_failed"}
            return []

        human_delay(1.5, 3.0)
        self._human_hints()

        if self._is_so_blocked():
            return []

        results: list[SearchResult] = []
        seen_urls: set[str] = set()
        self._pages_fetched = 0

        for page_idx in range(1, MAX_DIRECT_PAGES + 1):
            page_results = self._extract_so_current_page()
            self._pages_fetched = page_idx

            new_added = 0
            for r in page_results:
                if r.url in seen_urls:
                    continue
                seen_urls.add(r.url)
                results.append(r)
                new_added += 1
                if len(results) >= limit:
                    break

            log.info(
                "[so] direct page %d: %d new (total %d / limit %d)",
                page_idx,
                new_added,
                len(results),
                limit,
            )

            if len(results) >= limit:
                break

            if not self._goto_so_next_page():
                log.info("[so] no next-page link; stopping pagination")
                break

            human_delay(1.5, 3.0)
            self._human_hints()
            if self._is_so_blocked():
                log.warning("[so] blocked after pagination; stopping")
                break

        self._last_mode = "so_direct"
        if results:
            self.last_status["mode"] = "so_direct"
            self.last_status["count"] = len(results)
            self.last_status["pages_fetched"] = self._pages_fetched
        return results

    def _is_so_blocked(self) -> bool:
        """Detect Cloudflare / SO interstitials and rate-limits."""
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
            "mode": "so_direct",
            "url": url,
            "title": title,
            "body_len": len(body),
        }

        # SO bounces suspected bots to /nocaptcha?s=... or /users/login.
        if "/nocaptcha" in url or "/users/login" in url:
            self.last_status["block_reason"] = "nocaptcha_redirect"
            log.warning("[so] nocaptcha/login redirect: %s", url)
            return True

        head = body[:3000]
        for phrase in BLOCK_PHRASES:
            if phrase in head or phrase in title:
                self.last_status["block_reason"] = phrase
                log.warning("[so] block phrase detected: %r", phrase)
                return True
        return False

    def _goto_so_next_page(self) -> bool:
        """Click the 'Next' link / follow ``a[rel='next']`` if present."""
        next_selectors = [
            "a[rel='next']",
            "a.s-pagination--item[rel='next']",
            ".s-pagination a[rel='next']",
            ".pager a[rel='next']",
            ".pager-answers a[rel='next']",
        ]
        for sel in next_selectors:
            try:
                el = self.page.query_selector(sel)
            except Exception:
                el = None
            if not el:
                continue
            try:
                href = el.get_attribute("href") or ""
            except Exception:
                href = ""
            if not href:
                continue
            href = _abs_so(href)
            log.info("[so] follow next-page %s -> %s", sel, href)
            if safe_goto(self.page, href, timeout=30000):
                return True
            return False
        return False

    def _extract_so_current_page(self) -> list[SearchResult]:
        """Try modern layout first; fall back to legacy on the same DOM."""
        results = self._extract_so_modern()
        if results:
            self._last_layout = "modern"
            self.last_status["layout"] = "modern"
            return results
        results = self._extract_so_legacy()
        if results:
            self._last_layout = "legacy"
            self.last_status["layout"] = "legacy"
            return results
        self._last_layout = ""
        self.last_status["layout"] = ""
        return []

    def _extract_so_modern(self) -> list[SearchResult]:
        items = []
        used = None
        for sel in MODERN_RESULT_SELECTORS:
            try:
                items = self.page.query_selector_all(sel)
            except Exception:
                items = []
            if items:
                used = sel
                break
        if not items:
            return []

        log.info("[so] modern layout via %s (%d items)", used, len(items))
        self.last_status["selector"] = used

        results: list[SearchResult] = []
        for r in items:
            link_el = (
                r.query_selector(".s-post-summary--content-title a.s-link")
                or r.query_selector(".s-post-summary--content-title a")
                or r.query_selector("h3 a.s-link")
                or r.query_selector("h3 a")
            )
            if not link_el:
                continue
            try:
                title = (link_el.inner_text() or "").strip()
                href = link_el.get_attribute("href") or ""
            except Exception:
                continue
            href = _abs_so(href)
            if not title or not href:
                continue

            # Vote count: prefer the explicitly labelled 'votes' stats item.
            votes = None
            try:
                stats_items = r.query_selector_all(
                    ".s-post-summary--stats-item"
                )
            except Exception:
                stats_items = []
            for it in stats_items:
                try:
                    txt = (it.inner_text() or "").strip().lower()
                except Exception:
                    txt = ""
                if "vote" in txt:
                    num_el = it.query_selector(
                        ".s-post-summary--stats-item-number"
                    )
                    if num_el:
                        try:
                            votes = _parse_int(
                                (num_el.inner_text() or "").strip()
                            )
                        except Exception:
                            votes = None
                    break
            if votes is None and stats_items:
                # Fallback: first stats-item-number on the row.
                try:
                    first_num = stats_items[0].query_selector(
                        ".s-post-summary--stats-item-number"
                    )
                    if first_num:
                        votes = _parse_int(
                            (first_num.inner_text() or "").strip()
                        )
                except Exception:
                    pass

            excerpt = ""
            try:
                ex_el = r.query_selector(".s-post-summary--content-excerpt")
                if ex_el:
                    excerpt = (ex_el.inner_text() or "").strip()
            except Exception:
                excerpt = ""

            tags: list[str] = []
            try:
                tag_els = r.query_selector_all(
                    ".s-post-summary--meta-tags a.post-tag, "
                    ".post-taglist a.post-tag, "
                    "a.post-tag"
                )
            except Exception:
                tag_els = []
            for t in tag_els:
                try:
                    tag_text = (t.inner_text() or "").strip()
                except Exception:
                    tag_text = ""
                if tag_text and tag_text not in tags:
                    tags.append(tag_text)

            snippet = _compose_snippet(tags, excerpt)
            results.append(
                SearchResult(
                    title=title, url=href, snippet=snippet, score=votes
                )
            )
        return results

    def _extract_so_legacy(self) -> list[SearchResult]:
        items = []
        used = None
        for sel in LEGACY_RESULT_SELECTORS:
            try:
                items = self.page.query_selector_all(sel)
            except Exception:
                items = []
            if items:
                used = sel
                break
        if not items:
            return []

        log.info("[so] legacy layout via %s (%d items)", used, len(items))
        self.last_status["selector"] = used

        results: list[SearchResult] = []
        for r in items:
            link_el = (
                r.query_selector("a.question-hyperlink")
                or r.query_selector("h3 a")
                or r.query_selector(".result-link a")
            )
            if not link_el:
                continue
            try:
                title = (link_el.inner_text() or "").strip()
                href = link_el.get_attribute("href") or ""
            except Exception:
                continue
            href = _abs_so(href)
            if not title or not href:
                continue

            votes = None
            try:
                v_el = (
                    r.query_selector(".vote .vote-count-post")
                    or r.query_selector(".vote .vote-count-post strong")
                    or r.query_selector(".status strong")
                )
                if v_el:
                    votes = _parse_int((v_el.inner_text() or "").strip())
            except Exception:
                votes = None

            excerpt = ""
            try:
                ex_el = r.query_selector(".excerpt")
                if ex_el:
                    excerpt = (ex_el.inner_text() or "").strip()
            except Exception:
                excerpt = ""

            tags: list[str] = []
            try:
                tag_els = r.query_selector_all(
                    ".tags a.post-tag, a.post-tag"
                )
            except Exception:
                tag_els = []
            for t in tag_els:
                try:
                    tag_text = (t.inner_text() or "").strip()
                except Exception:
                    tag_text = ""
                if tag_text and tag_text not in tags:
                    tags.append(tag_text)

            snippet = _compose_snippet(tags, excerpt)
            results.append(
                SearchResult(
                    title=title, url=href, snippet=snippet, score=votes
                )
            )
        return results

    # ----------------------------------------------------------- so_api mode

    def _try_so_api(self, query: str, limit: int) -> list[SearchResult]:
        """Fetch results from Stack Exchange v2.3 ``/search/excerpts``.

        We navigate the browser to the API URL and parse the JSON body.
        Pagination is handled via ``&page=N`` until ``has_more`` is false
        or ``limit`` is satisfied.
        """
        results: list[SearchResult] = []
        seen_urls: set[str] = set()
        self._pages_fetched = 0
        page_size = max(min(limit, 100), 5)  # API allows 1..100 per page.

        for api_page in range(1, 11):  # hard upper bound: 10 API pages.
            params = {
                "order": "desc",
                "sort": "relevance",
                "q": query,
                "site": "stackoverflow",
                "page": str(api_page),
                "pagesize": str(page_size),
            }
            url = (
                f"{SE_API_BASE}/search/excerpts?"
                + urllib.parse.urlencode(params)
            )
            log.info("[so] api page %d: %s", api_page, url)
            if not safe_goto(self.page, url, timeout=25000, retries=1):
                self.last_status = {"mode": "so_api", "error": "goto_failed"}
                return results

            self._pages_fetched = api_page
            human_delay(0.5, 1.2)

            payload = self._read_json_body()
            if not payload:
                # Empty / non-JSON response (e.g. a Cloudflare gate on the
                # API host — extremely rare). Bail to next mode.
                self.last_status = {
                    "mode": "so_api",
                    "url": url,
                    "error": "non_json_body",
                }
                return results

            self.last_status = {
                "mode": "so_api",
                "url": url,
                "selector": "json",
                "api_quota_remaining": payload.get("quota_remaining"),
                "api_has_more": payload.get("has_more"),
                "body_len": payload.get("_body_len", 0),
            }
            if "error_message" in payload or "error_id" in payload:
                self.last_status["block_reason"] = payload.get(
                    "error_message", "api_error"
                )
                log.warning(
                    "[so] api error: %s", self.last_status["block_reason"]
                )
                return results

            items = payload.get("items") or []
            log.info("[so] api page %d returned %d items", api_page, len(items))
            new_added = 0
            for it in items:
                if it.get("item_type") and it.get("item_type") != "question":
                    # /search/excerpts can return answer-type entries too;
                    # skip those, we only want questions.
                    continue
                qid = it.get("question_id")
                if not qid:
                    continue
                href = f"{SO_HOME}/questions/{qid}"
                if href in seen_urls:
                    continue
                title = _strip_html(html.unescape(it.get("title") or ""))
                excerpt = _strip_html(html.unescape(it.get("excerpt") or ""))
                tags = list(it.get("tags") or [])
                score_raw = it.get("score")
                votes = (
                    int(score_raw) if isinstance(score_raw, (int, float))
                    else None
                )
                if not title:
                    continue
                seen_urls.add(href)
                results.append(
                    SearchResult(
                        title=title,
                        url=href,
                        snippet=_compose_snippet(tags, excerpt),
                        score=votes,
                    )
                )
                new_added += 1
                if len(results) >= limit:
                    break

            if len(results) >= limit:
                break
            if not payload.get("has_more"):
                log.info("[so] api: has_more=false, stopping pagination")
                break
            if new_added == 0:
                log.info("[so] api: no new items on page %d, stopping", api_page)
                break

        self._last_mode = "so_api"
        if results:
            self.last_status["mode"] = "so_api"
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
            log.warning("[so] api JSON decode failed: %s; head=%r", e, raw[:200])
            return None
        if not isinstance(data, dict):
            return None
        data["_body_len"] = len(raw)
        return data

    # -------------------------------------------------------- ddg_site mode

    def _try_ddg_site(self, query: str, limit: int) -> list[SearchResult]:
        site_query = f"site:stackoverflow.com {query}"
        q = urllib.parse.quote(site_query)
        url = f"{DDG_HTML_ENDPOINT}?q={q}"
        log.info("[so] ddg site search: %s", url)
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
        try:
            items = self.page.query_selector_all(".result")
        except Exception:
            items = []
        log.info("[so] ddg got %d .result items", len(items))

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
            if "stackoverflow.com" not in href.lower():
                continue
            # ddg_site has no native vote / tag fields. We pass an empty
            # tag list so the snippet renders as the plain excerpt.
            results.append(
                SearchResult(
                    title=title,
                    url=href,
                    snippet=_compose_snippet([], snippet),
                    score=None,
                )
            )
            if len(results) >= limit:
                break

        self._last_mode = "ddg_site"
        if results:
            self.last_status["count"] = len(results)
        return results

    # -------------------------------------------------------------- diagnostics

    def selector_counts(self) -> dict[str, int]:
        """Per-selector counts for whichever mode was attempted last."""
        counts: dict[str, int] = {}
        sel_lists: dict[str, list[str]] = {
            "so_direct": MODERN_RESULT_SELECTORS + LEGACY_RESULT_SELECTORS,
            "so_api": [],  # api mode has no DOM selectors
            "ddg_site": [".result"],
        }
        for sel in sel_lists.get(self._last_mode, []):
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1

        # Always also report a few generic selectors useful for diagnostics
        # regardless of mode.
        for sel in (
            ".s-post-summary--content-title a.s-link",
            ".s-post-summary--stats-item-number",
            ".s-post-summary--meta-tags a.post-tag",
            ".s-post-summary--content-excerpt",
            "a.question-hyperlink",
            ".vote-count-post",
            ".excerpt",
            "a.post-tag",
            "a[rel='next']",
            ".result__a",
            "pre",
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
