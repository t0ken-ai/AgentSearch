"""微博 (Weibo) search adapter with Google + Bing site: fallbacks.

Strategy
--------
Weibo's public search at:

    https://s.weibo.com/weibo?q=<keyword>

requires login cookies. Without them the SERP collapses to a login modal
and the body returns a few hundred chars only. So:

1. **Direct path** — visit ``s.weibo.com/weibo`` and try to harvest cards.
   When logged out this returns nothing.
2. **Google site: fallback** — drive :class:`GoogleEngine` with
   ``site:weibo.com <q>`` (and looser variants).
3. **Bing site: fallback** — when Google is rate-limited or empty,
   try the same query against :class:`BingEngine`.

Each :class:`SearchResult` carries:

* ``post_id``     — numeric id from ``/<uid>/<bid>`` URLs (when present)
* ``content_type`` — ``"post"`` / ``"user"`` / ``"page"``
* ``user``        — author display name (only on direct path)
* ``reposts`` / ``comments`` / ``likes`` — engagement counters (direct only)
* ``source``      — ``"weibo"`` (direct), ``"google"`` or ``"bing"`` (fallback)
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
from .bing import BingEngine

log = logging.getLogger(__name__)

WEIBO_HOME = "https://weibo.com"
WEIBO_SEARCH = "https://s.weibo.com/weibo"

# Match any weibo URL.
WEIBO_HOST_RE = re.compile(
    r"https?://(?:[a-z0-9-]+\.)*weibo\.(?:com|cn)/", re.IGNORECASE
)
# Status URLs come in two shapes:
#   https://weibo.com/<uid>/<bid>     -- old style
#   https://m.weibo.cn/status/<bid>   -- mobile
USER_POST_RE = re.compile(
    r"https?://(?:www\.)?weibo\.com/(\d{8,})/([A-Za-z0-9]+)", re.IGNORECASE
)
MOBILE_STATUS_RE = re.compile(
    r"https?://m\.weibo\.cn/(?:status|detail)/([A-Za-z0-9]+)", re.IGNORECASE
)
USER_PROFILE_RE = re.compile(
    r"https?://(?:www\.)?weibo\.com/u/(\d+)", re.IGNORECASE
)


_PARSE_JS = r"""
(limit) => {
  function pickText(root, selectors) {
    for (const sel of selectors) {
      const el = root.querySelector(sel);
      if (el) {
        const t = (el.textContent || "").trim();
        if (t) return t;
      }
    }
    return "";
  }
  // Weibo SERP cards live in #pl_feedlist_index .card-wrap
  const cards = Array.from(
    document.querySelectorAll('#pl_feedlist_index .card-wrap, .card-wrap')
  );
  const out = [];
  const seen = new Set();
  for (const card of cards) {
    if (out.length >= limit) break;
    // Each post anchors a permalink with action-type='feed_list_item_menu'
    // or contains <a class="from"> with the date+permalink.
    const fromA = card.querySelector('p.from a[href*="weibo.com/"]') ||
                  card.querySelector('a[href*="weibo.com/"][href*="/"]');
    if (!fromA) continue;
    let href = fromA.getAttribute('href') || '';
    if (href.startsWith('//')) href = 'https:' + href;
    else if (href.startsWith('/')) href = 'https://s.weibo.com' + href;
    const m = href.match(/weibo\.com\/(\d+)\/([A-Za-z0-9]+)/);
    const post_id = m ? m[2] : '';
    const key = post_id || href;
    if (seen.has(key)) continue;
    seen.add(key);

    const text = pickText(card, [
      'p.txt[node-type="feed_list_content_full"]',
      'p.txt[node-type="feed_list_content"]',
      'p.txt',
    ]);
    const user = pickText(card, [
      '.info .name', 'a.name', '.card-feed .info .name',
    ]);
    const reposts = pickText(card, [
      '.card-act li:nth-child(2)', '[action-type*="forward"]',
    ]).replace(/转发\s*/, '');
    const comments = pickText(card, [
      '.card-act li:nth-child(3)', '[action-type*="comment"]',
    ]).replace(/评论\s*/, '');
    const likes = pickText(card, [
      '.card-act li:nth-child(4)', '.woo-like-count',
    ]).replace(/赞\s*/, '');

    out.push({post_id, url: href, text, user, reposts, comments, likes});
  }
  return {cards_seen: cards.length, rows: out};
}
"""


class WeiboEngine(BaseEngine):
    """Weibo search adapter with Google + Bing fallbacks."""

    name = "weibo"
    max_retries = 1

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        return self._do_search(query, limit)

    # ------------------------------------------------------------------ search

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        direct = self._search_direct(query, limit)
        if direct:
            self.last_status["mode"] = "direct"
            return direct

        log.info("[weibo] direct path empty (likely login wall); "
                 "falling back to Google site:weibo.com")
        google = self._search_via(GoogleEngine, "google", query, limit)
        if google:
            self.last_status["mode"] = "google"
            return google

        log.info("[weibo] Google fallback empty; trying Bing site:weibo.com")
        bing = self._search_via(BingEngine, "bing", query, limit)
        if bing:
            self.last_status["mode"] = "bing"
        return bing

    # ------------------------------------------------------------ direct path

    def _search_direct(self, query: str, limit: int) -> list[SearchResult]:
        q = urllib.parse.quote(query)
        url = f"{WEIBO_SEARCH}?q={q}"
        log.info("[weibo] navigating to %s", url)
        if not safe_goto(self.page, url, timeout=25000):
            return []

        human_delay(1.2, 2.4)
        self._human_hints()

        try:
            data = self.page.evaluate(_PARSE_JS, limit) or {}
        except Exception as e:
            log.warning("[weibo] parse JS failed: %s", e)
            data = {}

        cards_seen = int(data.get("cards_seen") or 0)
        rows = data.get("rows") or []
        try:
            body_len = len(self.page.inner_text("body") or "")
        except Exception:
            body_len = 0

        self.last_status = {
            "url": getattr(self.page, "url", ""),
            "body_len": body_len,
            "cards_seen": cards_seen,
            "count": len(rows),
        }

        results: list[SearchResult] = []
        for row in rows:
            text = (row.get("text") or "").strip()
            url2 = (row.get("url") or "").strip()
            post_id = (row.get("post_id") or "").strip()
            if not url2:
                continue
            user = (row.get("user") or "").strip()
            reposts = (row.get("reposts") or "").strip()
            comments = (row.get("comments") or "").strip()
            likes = (row.get("likes") or "").strip()

            head = []
            if user:
                head.append(f"@{user}")
            if reposts:
                head.append(f"转 {reposts}")
            if comments:
                head.append(f"评 {comments}")
            if likes:
                head.append(f"赞 {likes}")
            head_text = " · ".join(head)
            snippet = " — ".join(p for p in (head_text, text) if p)[:320]

            title = text[:80] or url2
            r = SearchResult(title=title, url=url2, snippet=snippet)
            r.post_id = post_id          # type: ignore[attr-defined]
            r.content_type = "post"      # type: ignore[attr-defined]
            r.user = user                # type: ignore[attr-defined]
            r.reposts = reposts          # type: ignore[attr-defined]
            r.comments = comments        # type: ignore[attr-defined]
            r.likes = likes              # type: ignore[attr-defined]
            r.source = "weibo"           # type: ignore[attr-defined]
            results.append(r)
            if len(results) >= limit:
                break
        return results

    # --------------------------------------------------------- generic fallback

    def _search_via(self, engine_cls, source_label: str,
                    query: str, limit: int) -> list[SearchResult]:
        try:
            outer = engine_cls(self.page)
        except Exception as e:
            log.warning("[weibo] cannot construct %s: %s", engine_cls.__name__, e)
            return []

        query_attempts = [
            f'site:weibo.com "{query}"',
            f"site:weibo.com {query}",
            f"weibo.com {query}",
            f"{query} 微博",
        ]

        results: list[SearchResult] = []
        seen: set[str] = set()
        attempt_log: list[dict] = []

        for q in query_attempts:
            try:
                outer_results = outer.search(q, limit=max(limit * 3, 15))
            except Exception as e:
                log.warning("[weibo] %s raised on %r: %s", source_label, q, e)
                outer_results = []

            attempt_log.append({"query": q, "organic": len(outer_results)})

            for r in outer_results:
                u = r.url or ""
                if not WEIBO_HOST_RE.search(u):
                    continue
                content_type, post_id = self._classify_url(u)
                key = f"{content_type}:{post_id or u}"
                if key in seen:
                    continue
                seen.add(key)

                title = self._clean_google_title(r.title or "") or u
                snippet = (r.snippet or "")[:320]

                new_r = SearchResult(title=title[:200], url=u, snippet=snippet)
                new_r.post_id = post_id              # type: ignore[attr-defined]
                new_r.content_type = content_type    # type: ignore[attr-defined]
                new_r.user = ""                      # type: ignore[attr-defined]
                new_r.reposts = ""                   # type: ignore[attr-defined]
                new_r.comments = ""                  # type: ignore[attr-defined]
                new_r.likes = ""                     # type: ignore[attr-defined]
                new_r.source = source_label          # type: ignore[attr-defined]
                results.append(new_r)
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break

        self.last_status[f"{source_label}_attempts"] = attempt_log
        log.info("[weibo] %s fallback returned %d results", source_label, len(results))
        return results

    @staticmethod
    def _classify_url(url: str) -> tuple[str, str]:
        if not url:
            return ("page", "")
        m = USER_POST_RE.search(url)
        if m:
            return ("post", m.group(2))
        m = MOBILE_STATUS_RE.search(url)
        if m:
            return ("post", m.group(1))
        m = USER_PROFILE_RE.search(url)
        if m:
            return ("user", m.group(1))
        return ("page", "")

    @staticmethod
    def _clean_google_title(title: str) -> str:
        if not title:
            return ""
        t = title.strip()
        for sep in (" - 微博", " | 微博", " - 新浪微博", " - Weibo", " | Weibo"):
            if t.endswith(sep):
                t = t[: -len(sep)].strip()
                break
        return t

    # ------------------------------------------------------------------ helpers

    def _human_hints(self):
        try:
            self.page.mouse.move(
                random.randint(150, 700),
                random.randint(180, 500),
                steps=6,
            )
        except Exception:
            pass
        try:
            self.page.evaluate(
                "(y) => window.scrollBy(0, y)",
                random.randint(150, 480),
            )
        except Exception:
            pass
        time.sleep(random.uniform(0.3, 0.7))
