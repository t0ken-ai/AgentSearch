"""Reddit subreddit JSON API adapter.

Reddit exposes a public JSON view of every listing simply by appending
``.json`` to the path:

    https://www.reddit.com/r/<subreddit>.json?limit=<n>
    https://www.reddit.com/r/<subreddit>/<sort>.json?limit=<n>

where ``<sort>`` is one of ``hot`` / ``new`` / ``top`` / ``rising``.
The endpoint requires **no OAuth, no API key**, and returns the same
listing the redesign UI uses internally. The response shape::

    {
        "kind": "Listing",
        "data": {
            "children": [
                {
                    "kind": "t3",                  # link / post
                    "data": {
                        "title": "...",
                        "permalink": "/r/<sub>/comments/<id>/<slug>/",
                        "url": "...",
                        "author": "...",
                        "score": 1234,
                        "num_comments": 56,
                        "created_utc": 1700000000.0,
                        "selftext": "...",
                        "subreddit_name_prefixed": "r/<sub>",
                        "is_self": true | false,
                        ...
                    }
                },
                ...
            ]
        }
    }

For each post we map:
    title             -> SearchResult.title
    permalink         -> SearchResult.url   (always reddit-internal so the
                         user can read comments; the original ``url`` field
                         is attached as ``r.link_url`` for callers who want
                         the linked article instead)
    score             -> SearchResult.score (and ``r.score``)
    num_comments      -> ``r.num_comments``
    author            -> ``r.author``
    created_utc       -> ``r.created_utc`` (epoch seconds)
    selftext (trim)   -> SearchResult.snippet (with author / score / comments
                         pre-pended for human readability)

Reddit does sometimes serve the ``.json`` endpoint with a 429 / rate-limit
or a Cloudflare interstitial. We use the same warm-up trick as
``RedditEngine`` (visit ``www.reddit.com/`` first to drop ``js_challenge``
cookies) and fall back to ``old.reddit.com/r/<sub>.json`` if the canonical
host is throttled.
"""

from __future__ import annotations

import json
import logging
import time

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

VALID_SORTS = ("hot", "new", "top", "rising")
WARMUP_URL = "https://www.reddit.com/"
HOSTS = ("https://www.reddit.com", "https://old.reddit.com")


class RedditSubredditEngine(BaseEngine):
    """Fetch a subreddit's listing via the public ``.json`` endpoint.

    Use as ``engine.search("python", limit=10, sort="hot")`` or, because
    BaseEngine.search has a fixed signature, also via
    ``engine.fetch("python", limit=10, sort="hot")``. The query string
    is the **subreddit name** (with or without a leading ``r/``).
    """

    name = "reddit_subreddit"
    max_retries = 3
    SNIPPET_MAX = 320

    def __init__(self, page):
        super().__init__(page)
        self.sort: str = "hot"
        self.last_status: dict = {}

    # --------------------------------------------------------- public helpers

    def fetch(
        self,
        subreddit: str,
        limit: int = 10,
        sort: str = "hot",
    ) -> list[SearchResult]:
        """Convenience wrapper that lets callers pick the sort order."""
        sort = (sort or "hot").lower()
        if sort not in VALID_SORTS:
            raise ValueError(
                f"invalid sort {sort!r}; expected one of {VALID_SORTS}"
            )
        self.sort = sort
        return self.search(subreddit, limit=limit)

    # --------------------------------------------------------- BaseEngine API

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        sub = self._normalize_subreddit(query)
        if not sub:
            log.error("[reddit_subreddit] empty subreddit name")
            return []

        # Warm up so Reddit's edge gate (Akamai/CF) drops js_challenge
        # cookies on us. Without this old.reddit.com sometimes serves
        # an HTML "blocked by network security" page even for /.json.
        if safe_goto(self.page, WARMUP_URL, timeout=20000, retries=1):
            human_delay(2.0, 3.5)

        n = max(1, min(int(limit), 100))
        sort = self.sort
        for host in HOSTS:
            url = self._build_url(host, sub, sort, n)
            log.info("[reddit_subreddit] fetching %s", url)
            try:
                self.page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception as e:
                log.warning("[reddit_subreddit] goto failed for %s: %s", url, e)
                continue

            body = ""
            try:
                body = self.page.evaluate("() => document.body.innerText") or ""
            except Exception as e:
                log.warning(
                    "[reddit_subreddit] body extract failed for %s: %s", url, e
                )
                continue

            try:
                page_url = self.page.url or ""
            except Exception:
                page_url = ""
            try:
                page_title = self.page.title() or ""
            except Exception:
                page_title = ""

            self.last_status = {
                "url": page_url,
                "title": page_title,
                "body_len": len(body),
                "host": host,
                "sub": sub,
                "sort": sort,
            }

            if self._looks_blocked(page_url, page_title, body):
                self.last_status["block_reason"] = (
                    f"blocked or rate-limited at {host}"
                )
                log.warning(
                    "[reddit_subreddit] block detected at %s, trying next host",
                    host,
                )
                time.sleep(1.5)
                continue

            try:
                data = json.loads(body)
            except json.JSONDecodeError as e:
                log.warning(
                    "[reddit_subreddit] non-JSON from %s: %s; body[:200]=%r",
                    url, e, body[:200],
                )
                self.last_status["block_reason"] = "non_json_response"
                continue

            results = self._parse_listing(data, n)
            self.last_status["count"] = len(results)
            if results:
                return results
        return []

    # --------------------------------------------------------- diagnostics

    def selector_counts(self) -> dict[str, int]:
        # JSON adapter has no DOM selectors; surface body length only.
        try:
            body = self.page.evaluate("() => document.body.innerText") or ""
        except Exception:
            body = ""
        return {"body_chars": len(body)}

    # --------------------------------------------------------- helpers

    @staticmethod
    def _normalize_subreddit(name: str) -> str:
        """Strip leading ``r/`` / ``/r/`` / spaces and lower-case the name."""
        if not name:
            return ""
        s = name.strip()
        for prefix in ("/r/", "r/", "/"):
            if s.lower().startswith(prefix):
                s = s[len(prefix):]
                break
        return s.strip().strip("/").lower()

    @staticmethod
    def _build_url(host: str, sub: str, sort: str, limit: int) -> str:
        # ``hot`` is the default — emit /r/<sub>.json (cleaner) for it.
        if sort == "hot":
            return f"{host}/r/{sub}.json?limit={limit}&raw_json=1"
        return f"{host}/r/{sub}/{sort}.json?limit={limit}&raw_json=1"

    @staticmethod
    def _looks_blocked(url: str, title: str, body: str) -> bool:
        u = (url or "").lower()
        t = (title or "").lower()
        head = (body or "")[:600].lower()
        for frag in ("blocked", "/over18", "/login", "challenge", "captcha"):
            if frag in u:
                return True
        for phrase in (
            "you've been blocked",
            "you have been blocked",
            "blocked by network security",
            "rate limit",
            "too many requests",
            "verify you are human",
            "checking your browser",
            "just a moment",
            "access denied",
        ):
            if phrase in t or phrase in head:
                return True
        return False

    def _parse_listing(self, data, limit: int) -> list[SearchResult]:
        if not isinstance(data, dict):
            return []
        kind = data.get("kind")
        if kind != "Listing":
            log.warning(
                "[reddit_subreddit] unexpected kind=%r; data keys=%r",
                kind, list(data.keys())[:8],
            )
            return []
        children = (data.get("data") or {}).get("children") or []
        results: list[SearchResult] = []
        for child in children:
            if not isinstance(child, dict):
                continue
            if child.get("kind") != "t3":  # only "link" posts
                continue
            d = child.get("data") or {}
            r = self._build_result(d)
            if r is not None:
                results.append(r)
            if len(results) >= limit:
                break
        return results

    def _build_result(self, d: dict) -> SearchResult | None:
        title = (d.get("title") or "").strip()
        if not title:
            return None
        permalink = d.get("permalink") or ""
        if not permalink:
            return None
        if permalink.startswith("/"):
            url = "https://www.reddit.com" + permalink
        else:
            url = permalink

        link_url = (d.get("url_overridden_by_dest") or d.get("url") or "").strip()
        author = (d.get("author") or "").strip()
        try:
            score = int(d.get("score") or 0)
        except (TypeError, ValueError):
            score = 0
        try:
            num_comments = int(d.get("num_comments") or 0)
        except (TypeError, ValueError):
            num_comments = 0
        try:
            created_utc = float(d.get("created_utc") or 0)
        except (TypeError, ValueError):
            created_utc = 0.0
        is_self = bool(d.get("is_self"))
        sub_prefixed = (d.get("subreddit_name_prefixed") or "").strip()
        selftext = (d.get("selftext") or "").strip()

        head_bits = []
        if sub_prefixed:
            head_bits.append(sub_prefixed)
        if author:
            head_bits.append(f"u/{author}")
        head_bits.append(f"↑ {score}")
        head_bits.append(f"💬 {num_comments}")
        head = " · ".join(head_bits)

        body_part = selftext if (is_self and selftext) else (
            f"link → {link_url}" if (link_url and link_url != url) else ""
        )
        snippet = " — ".join(p for p in (head, body_part) if p)
        if len(snippet) > self.SNIPPET_MAX:
            snippet = snippet[: self.SNIPPET_MAX].rstrip() + "…"

        sr = SearchResult(title=title, url=url, snippet=snippet, score=score)
        sr.author = author                  # type: ignore[attr-defined]
        sr.score = score                    # type: ignore[attr-defined]
        sr.num_comments = num_comments      # type: ignore[attr-defined]
        sr.created_utc = created_utc        # type: ignore[attr-defined]
        sr.subreddit = sub_prefixed         # type: ignore[attr-defined]
        sr.link_url = link_url              # type: ignore[attr-defined]
        sr.is_self = is_self                # type: ignore[attr-defined]
        sr.selftext = selftext              # type: ignore[attr-defined]
        return sr
