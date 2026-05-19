"""GitHub search adapter.

GitHub's web search at ``https://github.com/search?q=<q>&type=...`` is a
React SPA that aggressively gates anonymous traffic behind a sign-in
wall and rate-limits / serves HTTP 429 to suspected bots. The public
REST API (``https://api.github.com/search/...``) is far more stable,
returns structured JSON, and works without auth (60 req/hour per IP for
``repositories`` / ``issues`` — code search requires auth, so for
``type="code"`` we skip API and try the HTML SPA first).

We layer three modes, mirroring ``stackoverflow.py`` / ``hackernews.py``:

1. **gh_api** — Hit the REST search endpoints directly (no auth):

   * ``type="repositories"`` → ``/search/repositories?q=<q>``
   * ``type="issues"``       → ``/search/issues?q=<q>``
   * ``type="code"``         → *skipped* (requires auth).

   Returns JSON with ``items[]`` containing ``full_name``,
   ``stargazers_count``, ``language``, ``html_url``, ``description``,
   ``forks_count``, ``updated_at`` (for repos); ``title``, ``html_url``,
   ``body``, ``state``, ``comments``, ``user.login``, ``labels[]`` (for
   issues). Cleanest, most stable path.

2. **gh_direct** — Navigate to ``github.com/search?q=<q>&type=...`` and
   scrape the SPA's result containers once they render. Selectors vary
   between layout revisions; we probe a list of known candidates and
   record which one matched on ``last_status['selector']``.

3. **ddg_site** — Last-resort fallback through the HTML-only DuckDuckGo
   endpoint with ``site:github.com <query>``. We can pull title + URL
   + a coarse snippet, but no stars / language / full metadata.

Each mode short-circuits on success. If a mode returns 0 parseable
results (or is rate-limited / blocked) the adapter falls through.

``SearchResult`` (see ``base.py``) carries ``title`` / ``url`` /
``snippet`` / ``score``. To preserve repo metadata:

* ``score`` holds the integer star count for repository results, the
  comment count for issue results, ``None`` otherwise.
* ``snippet`` is composed as ``"[lang] · <description>"`` for repos and
  ``"[state] · by <user> · <description>"`` for issues; missing parts
  are dropped and the separator collapses cleanly.
* In addition, every returned ``SearchResult`` has the following
  attributes set dynamically (the dataclass has no ``__slots__`` so
  this is supported):

  Common to all types:
    - ``result_type`` (str) — ``"repository"`` / ``"issue"`` / ``"code"``.
    - ``full_name``  (str) — ``"<owner>/<repo>"`` (always populated).
    - ``owner``      (str) — repository owner username.

  Repository results:
    - ``stars``       (int | None) — same as ``score``.
    - ``forks``       (int | None) — fork count.
    - ``language``    (str)        — primary language ("" if unknown).
    - ``description`` (str)        — repo description.
    - ``updated_at``  (str)        — ISO 8601 timestamp from API.

  Issue results:
    - ``state``       (str)        — ``"open"`` / ``"closed"``.
    - ``comments``    (int | None) — comment count (also ``score``).
    - ``user``        (str)        — issue author username.
    - ``labels``      (list[str])  — label names.
    - ``issue_number``(int | None) — issue number within repo.

  Code results (``gh_direct`` / ``ddg_site`` only):
    - ``path``     (str) — file path within repo.
    - ``language`` (str) — language by file extension (best-effort).

Diagnostics
-----------

* ``engine.last_status`` — ``mode``, ``url``, ``title``, ``body_len``,
  optional ``selector`` / ``block_reason`` / ``api_total_count`` /
  ``api_incomplete`` / ``count`` / ``pages_fetched``.
* ``engine.selector_counts()`` — per-selector counts useful across all
  three modes so test scripts can show why parsing missed.
"""

from __future__ import annotations

import html
import json
import logging
import os
import random
import re
import time
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

GH_HOME = "https://github.com"

# ---- gh_api ----------------------------------------------------------------

GH_API_BASE = "https://api.github.com"

# Map our ``search_type`` to the API endpoint path. Code search is omitted
# because the public ``/search/code`` endpoint requires authentication —
# anonymous calls return ``{"message": "Must authenticate ..."}``.
API_ENDPOINTS: dict[str, str] = {
    "repositories": "/search/repositories",
    "issues": "/search/issues",
}

# Hard upper bound on API pagination: GitHub caps total results to 1000
# regardless of pagination, and per_page is at most 100. 10 pages is the
# real ceiling.
MAX_API_PAGES = 10

# ---- gh_direct -------------------------------------------------------------

# Result container selectors for github.com/search, in priority order.
# GitHub has revised this markup multiple times; we list the variants
# we've observed so the engine survives layout flips. The first entry
# that produces hits is recorded on ``last_status['selector']``.
DIRECT_RESULT_SELECTORS: dict[str, list[str]] = {
    "repositories": [
        '[data-testid="results-list"] > div',
        '[data-testid="results-list"] [data-testid="results-list-item"]',
        "div.search-title",
        "ul.repo-list li.repo-list-item",
        "li.repo-list-item",
    ],
    "issues": [
        '[data-testid="results-list"] > div',
        '[data-testid="results-list"] [data-testid="results-list-item"]',
        "div.issue-list-item",
        "div.issue-list > div",
    ],
    "code": [
        '[data-testid="results-list"] > div',
        '[data-testid="results-list"] [data-testid="results-list-item"]',
        "div.code-list-item",
        "div.code-list > div",
    ],
}

# Phrases that indicate GitHub / Cloudflare blocked us.
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
    "whoa there",  # GitHub's custom rate-limit page wording.
    "abuse detection",
    "you have triggered an abuse detection",
]

# ---- ddg_site --------------------------------------------------------------

DDG_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"


# ----------------------------------------------------------------------------


def _abs_gh(href: str) -> str:
    """Normalize a relative GitHub URL to an absolute one."""
    if not href:
        return href
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return GH_HOME + href
    return GH_HOME + "/" + href


def _parse_int(text: str) -> int | None:
    """Parse a star/fork count string ('123', '1,234', '1.2k', '4.5M')."""
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
    """Strip HTML tags + collapse whitespace."""
    if not text:
        return text
    no_tags = _HTML_TAG_RE.sub("", text)
    return re.sub(r"\s+", " ", no_tags).strip()


def _split_full_name(full: str) -> tuple[str, str]:
    """Split an ``owner/repo`` string. Returns ``("", "")`` on failure."""
    if not full:
        return "", ""
    parts = full.strip().strip("/").split("/", 1)
    if len(parts) != 2:
        return "", ""
    return parts[0], parts[1]


def _compose_repo_snippet(language: str, description: str) -> str:
    """Render ``'[lang] · <description>'``; parts omitted when empty."""
    parts: list[str] = []
    if language:
        parts.append(f"[{language}]")
    if description:
        parts.append(description)
    return " · ".join(parts)


def _compose_issue_snippet(state: str, user: str, body: str) -> str:
    """Render ``'[state] · by <user> · <body>'``; parts omitted when empty."""
    parts: list[str] = []
    if state:
        parts.append(f"[{state}]")
    if user:
        parts.append(f"by {user}")
    if body:
        parts.append(body)
    return " · ".join(parts)


def _compose_code_snippet(language: str, path: str) -> str:
    """Render ``'[lang] · <path>'``; parts omitted when empty."""
    parts: list[str] = []
    if language:
        parts.append(f"[{language}]")
    if path:
        parts.append(path)
    return " · ".join(parts)


# Crude language-by-extension lookup for code / DDG results where the
# API isn't available. Only covers the obvious ones; falls back to "".
_EXT_LANG_MAP: dict[str, str] = {
    ".py": "Python",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".java": "Java",
    ".kt": "Kotlin",
    ".swift": "Swift",
    ".c": "C",
    ".h": "C",
    ".cc": "C++",
    ".cpp": "C++",
    ".cs": "C#",
    ".php": "PHP",
    ".sh": "Shell",
    ".html": "HTML",
    ".css": "CSS",
    ".md": "Markdown",
    ".json": "JSON",
    ".yml": "YAML",
    ".yaml": "YAML",
    ".toml": "TOML",
}


def _lang_from_path(path: str) -> str:
    if not path:
        return ""
    _, ext = os.path.splitext(path.lower())
    return _EXT_LANG_MAP.get(ext, "")


def _attach_repo_extras(
    r: SearchResult,
    *,
    full_name: str,
    owner: str,
    stars: int | None,
    forks: int | None,
    language: str,
    description: str,
    updated_at: str,
) -> SearchResult:
    r.result_type = "repository"
    r.full_name = full_name
    r.owner = owner
    r.stars = stars
    r.forks = forks
    r.language = language
    r.description = description
    r.updated_at = updated_at
    return r


def _attach_issue_extras(
    r: SearchResult,
    *,
    full_name: str,
    owner: str,
    state: str,
    comments: int | None,
    user: str,
    labels: list[str],
    issue_number: int | None,
) -> SearchResult:
    r.result_type = "issue"
    r.full_name = full_name
    r.owner = owner
    r.state = state
    r.comments = comments
    r.user = user
    r.labels = labels
    r.issue_number = issue_number
    return r


def _attach_code_extras(
    r: SearchResult,
    *,
    full_name: str,
    owner: str,
    path: str,
    language: str,
) -> SearchResult:
    r.result_type = "code"
    r.full_name = full_name
    r.owner = owner
    r.path = path
    r.language = language
    return r


# ----------------------------------------------------------------------------


class GitHubSearchEngine(BaseEngine):
    """Search GitHub for repositories / issues / code."""

    name = "github"
    max_retries = 3

    # Search type -> mode preference. ``code`` skips ``gh_api`` because
    # the unauthenticated REST endpoint refuses code search.
    _MODE_ORDER: dict[str, tuple[str, ...]] = {
        "repositories": ("gh_api", "gh_direct", "ddg_site"),
        "issues":       ("gh_api", "gh_direct", "ddg_site"),
        "code":         ("gh_direct", "ddg_site"),
    }

    def __init__(self, page, search_type: str = "repositories"):
        super().__init__(page)
        if search_type not in self._MODE_ORDER:
            raise ValueError(
                f"unsupported search_type {search_type!r}; "
                f"expected one of {sorted(self._MODE_ORDER)}"
            )
        self.search_type = search_type
        # Diagnostics for callers / tests.
        self.last_status: dict = {}
        self._last_mode: str = self._MODE_ORDER[search_type][0]
        self._pages_fetched: int = 0
        # Optional GitHub API token — picked up automatically when set,
        # so callers can extend the API rate limit (5000/hr authed vs
        # 60/hr anonymous) without code changes.
        self.api_token: str | None = (
            os.environ.get("GITHUB_TOKEN")
            or os.environ.get("GH_TOKEN")
            or None
        )

    # ------------------------------------------------------------------ search

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        order = self._MODE_ORDER[self.search_type]
        for mode in order:
            try:
                if mode == "gh_api":
                    results = self._try_gh_api(query, limit)
                elif mode == "gh_direct":
                    results = self._try_gh_direct(query, limit)
                elif mode == "ddg_site":
                    results = self._try_ddg_site(query, limit)
                else:  # pragma: no cover — _MODE_ORDER guards this.
                    results = []
            except Exception as e:
                log.warning("[gh] %s raised: %s", mode, e)
                results = []
            if results:
                self._last_mode = mode
                return results
        return []

    # ----------------------------------------------------------- gh_api mode

    def _try_gh_api(self, query: str, limit: int) -> list[SearchResult]:
        """Fetch from the GitHub REST search API and parse JSON."""
        endpoint = API_ENDPOINTS.get(self.search_type)
        if not endpoint:
            self.last_status = {
                "mode": "gh_api",
                "error": f"no API endpoint for type={self.search_type!r}",
            }
            return []

        # GitHub api.github.com responses are JSON regardless of headers.
        # We can't easily attach an Authorization header through
        # page.goto(); when ``api_token`` is set we fall back to setting
        # an extra HTTP header on the page. Best-effort — some browser
        # backends may not honour it.
        if self.api_token:
            try:
                self.page.set_extra_http_headers(
                    {"Authorization": f"Bearer {self.api_token}"}
                )
            except Exception as e:
                log.debug("[gh] couldn't set Authorization header: %s", e)

        results: list[SearchResult] = []
        seen_ids: set[str] = set()
        self._pages_fetched = 0
        page_size = max(min(limit, 100), 5)  # API caps per_page at 100.

        for api_page in range(1, MAX_API_PAGES + 1):
            params = {
                "q": query,
                "per_page": str(page_size),
                "page": str(api_page),
            }
            url = (
                f"{GH_API_BASE}{endpoint}?"
                + urllib.parse.urlencode(params)
            )
            log.info("[gh] api page %d: %s", api_page, url)
            if not safe_goto(self.page, url, timeout=25000, retries=1):
                self.last_status = {
                    "mode": "gh_api",
                    "url": url,
                    "error": "goto_failed",
                }
                return results

            self._pages_fetched = api_page
            human_delay(0.4, 1.0)

            payload = self._read_json_body()
            if not payload:
                # Empty / non-JSON response — Cloudflare gate or
                # captive portal. Bail to next mode.
                self.last_status = {
                    "mode": "gh_api",
                    "url": url,
                    "error": "non_json_body",
                }
                return results

            self.last_status = {
                "mode": "gh_api",
                "url": url,
                "selector": "json",
                "api_total_count": payload.get("total_count"),
                "api_incomplete": payload.get("incomplete_results"),
                "body_len": payload.get("_body_len", 0),
            }
            # Rate-limit / auth errors return ``{"message": "...",
            # "documentation_url": "..."}`` with no ``items``.
            if "message" in payload and "items" not in payload:
                self.last_status["block_reason"] = str(payload.get("message"))
                log.warning(
                    "[gh] api error: %s",
                    self.last_status["block_reason"],
                )
                return results

            items = payload.get("items") or []
            log.info(
                "[gh] api page %d returned %d items", api_page, len(items)
            )
            new_added = 0
            for it in items:
                r = self._build_api_result(it)
                if r is None:
                    continue
                # Dedupe by either repo full_name or issue id.
                key = self._dedupe_key(r)
                if key in seen_ids:
                    continue
                seen_ids.add(key)
                results.append(r)
                new_added += 1
                if len(results) >= limit:
                    break

            if len(results) >= limit:
                break
            total = payload.get("total_count") or 0
            fetched_so_far = api_page * page_size
            if fetched_so_far >= total:
                log.info(
                    "[gh] api: fetched %d >= total_count=%d, stopping",
                    fetched_so_far,
                    total,
                )
                break
            if new_added == 0:
                log.info("[gh] api: no new items on page %d, stopping", api_page)
                break

        if results:
            self.last_status["count"] = len(results)
            self.last_status["pages_fetched"] = self._pages_fetched
        return results

    def _build_api_result(self, it: dict) -> SearchResult | None:
        """Convert an API ``items[]`` entry into a SearchResult."""
        if self.search_type == "repositories":
            full_name = (it.get("full_name") or "").strip()
            html_url = (it.get("html_url") or "").strip()
            if not full_name or not html_url:
                return None
            owner_obj = it.get("owner") or {}
            owner = (owner_obj.get("login") if isinstance(owner_obj, dict) else "") or ""
            if not owner:
                owner, _ = _split_full_name(full_name)
            stars_raw = it.get("stargazers_count")
            stars = (
                int(stars_raw)
                if isinstance(stars_raw, (int, float))
                else None
            )
            forks_raw = it.get("forks_count")
            forks = (
                int(forks_raw)
                if isinstance(forks_raw, (int, float))
                else None
            )
            language = (it.get("language") or "") or ""
            description = (it.get("description") or "") or ""
            updated_at = (it.get("updated_at") or "") or ""
            r = SearchResult(
                title=full_name,
                url=html_url,
                snippet=_compose_repo_snippet(language, description),
                score=stars,
            )
            return _attach_repo_extras(
                r,
                full_name=full_name,
                owner=owner,
                stars=stars,
                forks=forks,
                language=language,
                description=description,
                updated_at=updated_at,
            )

        if self.search_type == "issues":
            title = (it.get("title") or "").strip()
            html_url = (it.get("html_url") or "").strip()
            if not title or not html_url:
                return None
            # /search/issues returns both issues and PRs; keep both,
            # caller can filter via ``q`` (e.g. ``is:issue``).
            state = (it.get("state") or "") or ""
            comments_raw = it.get("comments")
            comments = (
                int(comments_raw)
                if isinstance(comments_raw, (int, float))
                else None
            )
            user_obj = it.get("user") or {}
            user = (
                user_obj.get("login") if isinstance(user_obj, dict) else ""
            ) or ""
            labels_raw = it.get("labels") or []
            labels: list[str] = []
            for L in labels_raw:
                if isinstance(L, dict):
                    name = (L.get("name") or "").strip()
                    if name:
                        labels.append(name)
                elif isinstance(L, str):
                    if L.strip():
                        labels.append(L.strip())
            issue_number_raw = it.get("number")
            issue_number = (
                int(issue_number_raw)
                if isinstance(issue_number_raw, (int, float))
                else None
            )
            # html_url shape: https://github.com/<owner>/<repo>/issues/<n>
            full_name = ""
            owner = ""
            m = re.match(
                r"^https?://github\.com/([^/]+)/([^/]+)/(issues|pull)/\d+",
                html_url,
            )
            if m:
                owner, repo = m.group(1), m.group(2)
                full_name = f"{owner}/{repo}"
            body_raw = it.get("body") or ""
            body = _strip_html(html.unescape(body_raw))
            if len(body) > 240:
                body = body[:240].rstrip() + "..."
            r = SearchResult(
                title=title,
                url=html_url,
                snippet=_compose_issue_snippet(state, user, body),
                score=comments,
            )
            return _attach_issue_extras(
                r,
                full_name=full_name,
                owner=owner,
                state=state,
                comments=comments,
                user=user,
                labels=labels,
                issue_number=issue_number,
            )

        # Unreachable — _MODE_ORDER for "code" doesn't include gh_api.
        return None  # pragma: no cover

    def _dedupe_key(self, r: SearchResult) -> str:
        """Stable key for de-duping across pages."""
        if self.search_type == "repositories":
            return f"repo:{getattr(r, 'full_name', '') or r.url}"
        if self.search_type == "issues":
            return f"issue:{r.url}"
        return f"code:{r.url}"

    def _read_json_body(self) -> dict | None:
        """Pull JSON from the current page body (Chrome may pretty-print)."""
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
                "[gh] api JSON decode failed: %s; head=%r", e, raw[:200]
            )
            return None
        if not isinstance(data, dict):
            return None
        data["_body_len"] = len(raw)
        return data

    # -------------------------------------------------------- gh_direct mode

    def _try_gh_direct(self, query: str, limit: int) -> list[SearchResult]:
        """Scrape github.com/search?q=...&type=... after the SPA renders."""
        # Warm up on the homepage so cookies settle before /search.
        if safe_goto(self.page, GH_HOME + "/", timeout=20000, retries=1):
            human_delay(1.0, 2.0)
            self._human_hints()

        q = urllib.parse.quote(query)
        url = f"{GH_HOME}/search?q={q}&type={self.search_type}"
        log.info("[gh] direct search: %s", url)
        if not safe_goto(self.page, url, timeout=30000, retries=1):
            self.last_status = {
                "mode": "gh_direct",
                "error": "goto_failed",
            }
            return []

        # SPA renders results via JS. Wait for at least one candidate
        # selector to appear; tolerate timeout (we'll fall through if
        # nothing appears).
        for sel in DIRECT_RESULT_SELECTORS.get(self.search_type, []):
            try:
                self.page.wait_for_selector(sel, timeout=5000)
                break
            except Exception:
                continue

        human_delay(1.5, 3.0)
        self._human_hints()

        if self._is_blocked("gh_direct"):
            return []

        items = []
        used = None
        for sel in DIRECT_RESULT_SELECTORS.get(self.search_type, []):
            try:
                items = self.page.query_selector_all(sel)
            except Exception:
                items = []
            if items:
                used = sel
                break
        if not items:
            self.last_status.setdefault("mode", "gh_direct")
            self.last_status["count"] = 0
            return []

        log.info("[gh] direct via %s (%d items)", used, len(items))
        self.last_status["selector"] = used

        results: list[SearchResult] = []
        seen: set[str] = set()
        for r in items[: limit * 3]:
            sr = self._extract_direct_item(r)
            if sr is None:
                continue
            key = self._dedupe_key(sr)
            if key in seen:
                continue
            seen.add(key)
            results.append(sr)
            if len(results) >= limit:
                break

        if results:
            self.last_status["mode"] = "gh_direct"
            self.last_status["count"] = len(results)
        return results

    def _extract_direct_item(self, r) -> SearchResult | None:
        """Pull a single SearchResult out of one DOM container."""
        # Common: title link is the first <a> inside the item with an
        # href that looks like a repo / issue / file path.
        link_el = (
            r.query_selector('a[data-testid="results-list-item-link"]')
            or r.query_selector("h3 a")
            or r.query_selector(".search-title a")
            or r.query_selector("a[href^='/']")
        )
        if not link_el:
            return None
        try:
            title = (link_el.inner_text() or "").strip()
            href = link_el.get_attribute("href") or ""
        except Exception:
            return None
        if not href:
            return None
        href = _abs_gh(href)

        if self.search_type == "repositories":
            return self._extract_direct_repo(r, title, href)
        if self.search_type == "issues":
            return self._extract_direct_issue(r, title, href)
        # code
        return self._extract_direct_code(r, title, href)

    def _extract_direct_repo(
        self, r, title: str, href: str
    ) -> SearchResult | None:
        # ``title`` is typically rendered as ``"<owner>/<repo>"`` on the
        # search page, but some layouts use just the repo name. Pull
        # the canonical full_name from the URL when possible.
        full_name = ""
        m = re.match(r"^https?://github\.com/([^/]+/[^/?#]+)", href)
        if m:
            full_name = m.group(1)
        if not full_name:
            full_name = title
        owner, _ = _split_full_name(full_name)
        # Title we display: prefer full_name for clarity.
        display_title = full_name or title
        if not display_title:
            return None

        # Stars: hunt for an "octicon-star" sibling or a numeric chip
        # adjacent to a "stars" label.
        stars = self._extract_count(r, ["star"])
        forks = self._extract_count(r, ["fork"])

        # Language: GitHub renders the primary language as text after a
        # dot-coloured circle. The accessible label sometimes lives on
        # ``[itemprop="programmingLanguage"]``; we try several spots.
        language = ""
        try:
            lang_el = (
                r.query_selector('[itemprop="programmingLanguage"]')
                or r.query_selector('span[aria-label*="language" i]')
            )
            if lang_el:
                language = (lang_el.inner_text() or "").strip()
        except Exception:
            language = ""
        if not language:
            # Fallback heuristic: dot-coloured language pill.
            try:
                pill = r.query_selector(".repo-language-color")
                if pill:
                    parent = pill.evaluate_handle(
                        "el => el.parentElement"
                    )
                    if parent:
                        text = (parent.evaluate("el => el.textContent") or "").strip()
                        # Strip leading colour-circle whitespace.
                        language = re.sub(r"\s+", " ", text).strip()
            except Exception:
                language = ""

        description = ""
        try:
            desc_el = (
                r.query_selector('p[data-testid="results-list-item-description"]')
                or r.query_selector("p.col-12")
                or r.query_selector(".search-match")
                or r.query_selector("p")
            )
            if desc_el:
                description = (desc_el.inner_text() or "").strip()
        except Exception:
            description = ""

        sr = SearchResult(
            title=display_title,
            url=href,
            snippet=_compose_repo_snippet(language, description),
            score=stars,
        )
        return _attach_repo_extras(
            sr,
            full_name=full_name,
            owner=owner,
            stars=stars,
            forks=forks,
            language=language,
            description=description,
            updated_at="",
        )

    def _extract_direct_issue(
        self, r, title: str, href: str
    ) -> SearchResult | None:
        if not title:
            return None
        owner = ""
        full_name = ""
        m = re.match(
            r"^https?://github\.com/([^/]+)/([^/]+)/(issues|pull)/(\d+)", href
        )
        issue_number = None
        if m:
            owner, repo = m.group(1), m.group(2)
            full_name = f"{owner}/{repo}"
            try:
                issue_number = int(m.group(4))
            except ValueError:
                issue_number = None

        # Issue state badge: open/closed/merged.
        state = ""
        try:
            state_el = (
                r.query_selector('[data-testid="issue-state"]')
                or r.query_selector(".State")
            )
            if state_el:
                state = (state_el.inner_text() or "").strip().lower()
        except Exception:
            state = ""

        user = ""
        try:
            user_el = r.query_selector(
                'a[data-hovercard-type="user"], a[href^="/"][rel="author"]'
            )
            if user_el:
                user = (user_el.inner_text() or "").strip()
        except Exception:
            user = ""

        body = ""
        try:
            body_el = (
                r.query_selector('p[data-testid="results-list-item-description"]')
                or r.query_selector("p.col-9")
                or r.query_selector("p")
            )
            if body_el:
                body = (body_el.inner_text() or "").strip()
        except Exception:
            body = ""
        if len(body) > 240:
            body = body[:240].rstrip() + "..."

        labels: list[str] = []
        try:
            label_els = r.query_selector_all(".labels a, .IssueLabel")
            for L in label_els:
                txt = (L.inner_text() or "").strip()
                if txt and txt not in labels:
                    labels.append(txt)
        except Exception:
            pass

        comments = self._extract_count(r, ["comment"])

        sr = SearchResult(
            title=title,
            url=href,
            snippet=_compose_issue_snippet(state, user, body),
            score=comments,
        )
        return _attach_issue_extras(
            sr,
            full_name=full_name,
            owner=owner,
            state=state,
            comments=comments,
            user=user,
            labels=labels,
            issue_number=issue_number,
        )

    def _extract_direct_code(
        self, r, title: str, href: str
    ) -> SearchResult | None:
        if not title:
            return None
        # href shape: /<owner>/<repo>/blob/<branch>/<path>
        m = re.match(
            r"^https?://github\.com/([^/]+)/([^/]+)/blob/[^/]+/(.+)$", href
        )
        if not m:
            return None
        owner, repo, path = m.group(1), m.group(2), m.group(3)
        full_name = f"{owner}/{repo}"
        # Strip any URL fragment from the path.
        path = path.split("#", 1)[0]
        language = _lang_from_path(path)

        sr = SearchResult(
            title=f"{full_name}/{path}",
            url=href,
            snippet=_compose_code_snippet(language, path),
            score=None,
        )
        return _attach_code_extras(
            sr,
            full_name=full_name,
            owner=owner,
            path=path,
            language=language,
        )

    def _extract_count(self, container, keywords: list[str]) -> int | None:
        """Find the first numeric chip whose text matches any keyword.

        GitHub renders the star / fork / comment counts as small chips
        next to an ``aria-label`` that contains the metric name. We
        iterate any element that contains a digit and check its
        accessible label or surrounding text.
        """
        try:
            els = container.query_selector_all(
                "a, span, div, button"
            )
        except Exception:
            return None
        for el in els:
            try:
                aria = (el.get_attribute("aria-label") or "").lower()
                text = (el.inner_text() or "").strip()
            except Exception:
                continue
            label_text = aria + " " + text.lower()
            if not any(kw in label_text for kw in keywords):
                continue
            # Pull the leading numeric token from the text.
            m = re.search(r"(\d[\d,.]*\s*[km]?)", text, re.I)
            if not m:
                continue
            n = _parse_int(m.group(1))
            if n is not None:
                return n
        return None

    # -------------------------------------------------------- ddg_site mode

    def _try_ddg_site(self, query: str, limit: int) -> list[SearchResult]:
        # Tailor the site filter to the search type so we don't surface
        # docs / marketing pages.
        if self.search_type == "code":
            site_query = f"site:github.com inurl:blob {query}"
        elif self.search_type == "issues":
            site_query = f"site:github.com inurl:issues {query}"
        else:
            site_query = f"site:github.com {query}"
        q = urllib.parse.quote(site_query)
        url = f"{DDG_HTML_ENDPOINT}?q={q}"
        log.info("[gh] ddg site search: %s", url)
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
        log.info("[gh] ddg got %d .result items", len(items))

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
            if "github.com" not in href.lower():
                continue

            sr = self._build_ddg_result(title, href, snippet)
            if sr is None:
                continue
            key = self._dedupe_key(sr)
            if key in seen:
                continue
            seen.add(key)
            results.append(sr)
            if len(results) >= limit:
                break

        if results:
            self.last_status["count"] = len(results)
        return results

    def _build_ddg_result(
        self, title: str, href: str, snippet: str
    ) -> SearchResult | None:
        """Convert a DDG hit into a SearchResult, filtered by search_type."""
        if self.search_type == "repositories":
            # Accept the bare ``/owner/repo`` form; reject any deeper
            # paths so we don't surface issues / PRs / wiki pages.
            m = re.match(
                r"^https?://github\.com/([^/]+)/([^/?#]+)/?(?:[?#].*)?$",
                href,
            )
            if not m:
                return None
            owner, repo = m.group(1), m.group(2)
            # Skip non-repo slugs that look like reserved namespaces.
            if owner in {"about", "features", "topics", "trending", "search"}:
                return None
            full_name = f"{owner}/{repo}"
            sr = SearchResult(
                title=full_name,
                url=href,
                snippet=_compose_repo_snippet("", snippet),
                score=None,
            )
            return _attach_repo_extras(
                sr,
                full_name=full_name,
                owner=owner,
                stars=None,
                forks=None,
                language="",
                description=snippet,
                updated_at="",
            )

        if self.search_type == "issues":
            m = re.match(
                r"^https?://github\.com/([^/]+)/([^/]+)/(issues|pull)/(\d+)",
                href,
            )
            if not m:
                return None
            owner, repo = m.group(1), m.group(2)
            full_name = f"{owner}/{repo}"
            try:
                issue_number = int(m.group(4))
            except ValueError:
                issue_number = None
            sr = SearchResult(
                title=title,
                url=href,
                snippet=_compose_issue_snippet("", "", snippet),
                score=None,
            )
            return _attach_issue_extras(
                sr,
                full_name=full_name,
                owner=owner,
                state="",
                comments=None,
                user="",
                labels=[],
                issue_number=issue_number,
            )

        # code
        m = re.match(
            r"^https?://github\.com/([^/]+)/([^/]+)/blob/[^/]+/(.+)$", href
        )
        if not m:
            return None
        owner, repo, path = m.group(1), m.group(2), m.group(3)
        full_name = f"{owner}/{repo}"
        path = path.split("#", 1)[0]
        language = _lang_from_path(path)
        sr = SearchResult(
            title=f"{full_name}/{path}",
            url=href,
            snippet=_compose_code_snippet(language, path) or snippet,
            score=None,
        )
        return _attach_code_extras(
            sr,
            full_name=full_name,
            owner=owner,
            path=path,
            language=language,
        )

    # -------------------------------------------------------- block detection

    def _is_blocked(self, mode: str) -> bool:
        """Detect Cloudflare / GitHub interstitials and rate-limits."""
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

        # GitHub redirects unauth'd searches that look bot-like to the
        # sign-in page; treat that as a soft block so we fall through
        # to ddg_site.
        if "/login" in url and "return_to" in url:
            self.last_status["block_reason"] = "login_redirect"
            log.warning("[gh] login redirect: %s", url)
            return True

        head = body[:3000]
        for phrase in BLOCK_PHRASES:
            if phrase in head or phrase in title:
                self.last_status["block_reason"] = phrase
                log.warning("[gh] block phrase detected: %r", phrase)
                return True
        return False

    # -------------------------------------------------------------- diagnostics

    def selector_counts(self) -> dict[str, int]:
        """Per-selector counts; safe to call regardless of last_mode."""
        counts: dict[str, int] = {}
        # Always probe the cross-mode selectors so a failure mode is
        # easy to identify from a single diagnostic dump.
        probe = [
            "pre",
            '[data-testid="results-list"]',
            '[data-testid="results-list"] > div',
            '[data-testid="results-list-item"]',
            "div.search-title",
            "li.repo-list-item",
            ".repo-language-color",
            '[itemprop="programmingLanguage"]',
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
