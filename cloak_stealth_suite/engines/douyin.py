"""抖音 (Douyin / TikTok-CN) search adapter with Google + Bing site: fallbacks.

Strategy
--------
Douyin's web search at:

    https://www.douyin.com/search/<keyword>

is locked down hard:

* Without a logged-in session it serves a slider/captcha + login modal
  almost immediately.
* Even when a captcha solver succeeds, video cards (`a[href*="/video/"]`)
  only render when sufficient cookies are present.

In addition Douyin sets aggressive ``robots.txt`` / ``noindex`` directives,
so neither Google nor Bing index much of ``douyin.com`` beyond the
homepage and a handful of category pages. Our strategy is:

1. **Direct path** — visit ``/search/<q>`` and try to harvest any
   ``/video/<id>`` / ``/note/<id>`` anchors. When logged out this
   returns nothing.
2. **Google site: fallback** — drive :class:`GoogleEngine` with
   ``site:douyin.com <q>`` (and looser variants).
3. **Bing site: fallback** — when Google is rate-limited or empty,
   try the same query against :class:`BingEngine`.

Each :class:`SearchResult` carries:

* ``video_id``     — numeric id from ``/video/<id>`` / ``/note/<id>`` (when present)
* ``content_type`` — ``"video"`` / ``"note"`` / ``"user"`` / ``""`` (other surfaces)
* ``user``         — author display name (only on direct path)
* ``likes``        — ``"3.2万"``-style string (only on direct path)
* ``source``       — ``"douyin"`` (direct), ``"google"`` or ``"bing"`` (fallback)
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

DOUYIN_HOME = "https://www.douyin.com"
DOUYIN_SEARCH = "https://www.douyin.com/search"

# Match any douyin URL.
DOUYIN_HOST_RE = re.compile(
    r"https?://(?:[a-z0-9-]+\.)*douyin\.com/", re.IGNORECASE
)
VIDEO_RE = re.compile(
    r"https?://(?:www\.)?douyin\.com/video/(\d+)", re.IGNORECASE
)
NOTE_RE = re.compile(
    r"https?://(?:www\.)?douyin\.com/note/(\d+)", re.IGNORECASE
)
USER_RE = re.compile(
    r"https?://(?:www\.)?douyin\.com/user/([A-Za-z0-9_\-]+)", re.IGNORECASE
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
  const anchors = Array.from(
    document.querySelectorAll('a[href*="/video/"], a[href*="/note/"]')
  );
  const seen = new Set();
  const out = [];
  for (const a of anchors) {
    if (out.length >= limit) break;
    const href = a.getAttribute('href') || '';
    let m = href.match(/\/video\/(\d+)/);
    let kind = 'video';
    if (!m) { m = href.match(/\/note\/(\d+)/); kind = 'note'; }
    if (!m) continue;
    const vid = m[1];
    if (seen.has(vid)) continue;
    seen.add(vid);

    let card = a.closest('[data-e2e]') ||
               a.closest('li') || a.closest('section') ||
               a.closest('[class*="card" i]') ||
               a.closest('div');
    if (!card) card = a;

    let title = (a.getAttribute('aria-label') || '').trim();
    if (!title) {
      const img = card.querySelector('img[alt]');
      if (img) title = (img.getAttribute('alt') || '').trim();
    }
    if (!title) {
      title = pickText(card, [
        '[data-e2e*="title" i]', '.title', '[class*="title" i]',
        'h2', 'h3', 'p',
      ]);
    }
    if (!title) {
      title = (a.textContent || '').trim();
    }
    const user = pickText(card, [
      '[data-e2e*="user" i]', '.user-name', '.author',
      '[class*="author" i]', '[class*="nickname" i]',
    ]);
    let likes = pickText(card, [
      '[data-e2e*="like" i]', '.like-count',
      '[class*="like" i] [class*="count" i]', '.count',
    ]);
    likes = likes.replace(/^\s*点赞\s*/, '').trim();

    let url = href;
    if (url.startsWith('//')) url = 'https:' + url;
    else if (url.startsWith('/')) url = 'https://www.douyin.com' + url;

    out.push({video_id: vid, kind, title, url, user, likes});
  }
  return {anchors_seen: anchors.length, rows: out};
}
"""


class DouyinEngine(BaseEngine):
    """Douyin search adapter with Google + Bing fallbacks."""

    name = "douyin"
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

        log.info("[douyin] direct path empty (likely captcha/login wall); "
                 "falling back to Google site:douyin.com")
        google = self._search_via(GoogleEngine, "google", query, limit)
        if google:
            self.last_status["mode"] = "google"
            return google

        log.info("[douyin] Google fallback empty; trying Bing site:douyin.com")
        bing = self._search_via(BingEngine, "bing", query, limit)
        if bing:
            self.last_status["mode"] = "bing"
        return bing

    # ------------------------------------------------------------ direct path

    def _search_direct(self, query: str, limit: int) -> list[SearchResult]:
        if safe_goto(self.page, DOUYIN_HOME + "/", timeout=20000, retries=1):
            human_delay(0.8, 1.6)

        q = urllib.parse.quote(query)
        url = f"{DOUYIN_SEARCH}/{q}"
        log.info("[douyin] navigating to %s", url)
        if not safe_goto(self.page, url, timeout=25000):
            return []

        human_delay(1.5, 3.0)
        self._human_hints()

        try:
            data = self.page.evaluate(_PARSE_JS, limit) or {}
        except Exception as e:
            log.warning("[douyin] parse JS failed: %s", e)
            data = {}

        anchors_seen = int(data.get("anchors_seen") or 0)
        rows = data.get("rows") or []
        try:
            body_len = len(self.page.inner_text("body") or "")
        except Exception:
            body_len = 0

        self.last_status = {
            "url": getattr(self.page, "url", ""),
            "body_len": body_len,
            "anchors_seen": anchors_seen,
            "count": len(rows),
        }

        results: list[SearchResult] = []
        for row in rows:
            title = (row.get("title") or "").strip()
            url2 = (row.get("url") or "").strip()
            video_id = (row.get("video_id") or "").strip()
            kind = (row.get("kind") or "video").strip()
            if not title or not url2 or not video_id:
                continue
            user = (row.get("user") or "").strip()
            likes = (row.get("likes") or "").strip()

            head = []
            if user:
                head.append(f"@{user}")
            if likes:
                head.append(f"♡ {likes}")
            snippet = " · ".join(head)

            r = SearchResult(title=title[:200], url=url2, snippet=snippet)
            r.video_id = video_id        # type: ignore[attr-defined]
            r.content_type = kind        # type: ignore[attr-defined]
            r.user = user                # type: ignore[attr-defined]
            r.likes = likes              # type: ignore[attr-defined]
            r.source = "douyin"          # type: ignore[attr-defined]
            results.append(r)
            if len(results) >= limit:
                break
        return results

    # --------------------------------------------------------- generic fallback

    def _search_via(self, engine_cls, source_label: str,
                    query: str, limit: int) -> list[SearchResult]:
        """Drive an outer search engine (Google or Bing) and keep douyin.com hits."""
        try:
            outer = engine_cls(self.page)
        except Exception as e:
            log.warning("[douyin] cannot construct %s: %s", engine_cls.__name__, e)
            return []

        query_attempts = [
            f'site:douyin.com "{query}"',
            f"site:douyin.com {query}",
            f"douyin.com {query}",
            f"{query} 抖音",
        ]

        results: list[SearchResult] = []
        seen: set[str] = set()
        attempt_log: list[dict] = []

        for q in query_attempts:
            try:
                outer_results = outer.search(q, limit=max(limit * 3, 15))
            except Exception as e:
                log.warning("[douyin] %s raised on %r: %s", source_label, q, e)
                outer_results = []

            attempt_log.append({"query": q, "organic": len(outer_results)})

            for r in outer_results:
                u = r.url or ""
                if not DOUYIN_HOST_RE.search(u):
                    continue
                content_type, vid = self._classify_url(u)
                key = f"{content_type or 'page'}:{vid or u}"
                if key in seen:
                    continue
                seen.add(key)

                title = self._clean_google_title(r.title or "") or u
                snippet = (r.snippet or "")[:320]

                new_r = SearchResult(title=title[:200], url=u, snippet=snippet)
                new_r.video_id = vid                # type: ignore[attr-defined]
                new_r.content_type = content_type   # type: ignore[attr-defined]
                new_r.user = ""                     # type: ignore[attr-defined]
                new_r.likes = ""                    # type: ignore[attr-defined]
                new_r.source = source_label         # type: ignore[attr-defined]
                results.append(new_r)
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break

        self.last_status[f"{source_label}_attempts"] = attempt_log
        log.info("[douyin] %s fallback returned %d results", source_label, len(results))
        return results

    @staticmethod
    def _classify_url(url: str) -> tuple[str, str]:
        if not url:
            return ("", "")
        m = VIDEO_RE.search(url)
        if m:
            return ("video", m.group(1))
        m = NOTE_RE.search(url)
        if m:
            return ("note", m.group(1))
        m = USER_RE.search(url)
        if m:
            return ("user", m.group(1))
        return ("", "")

    @staticmethod
    def _clean_google_title(title: str) -> str:
        if not title:
            return ""
        t = title.strip()
        for sep in (" - 抖音", " | 抖音", " - Douyin", " | Douyin"):
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
