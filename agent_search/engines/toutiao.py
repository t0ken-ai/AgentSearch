"""今日头条 (Toutiao) search adapter.

Strategy
--------
Toutiao's web search at:

    https://so.toutiao.com/search?keyword=<q>

is a single-page React/SSR app. The result data is fetched via XHR after
hydration; in our headless browser hydration often stalls (``inner_text``
collapses to ~80 chars) and the page exposes only the section-tab links.
We therefore:

1. **Direct path** — visit ``so.toutiao.com/search`` and try to harvest any
   article-like anchors. Mostly returns ``[]`` from headless.
2. **Google site: fallback** — drive :class:`GoogleEngine` with
   ``site:toutiao.com <q>`` (and looser variants).
3. **Bing site: fallback** — when Google is rate-limited or empty,
   try the same query against :class:`BingEngine`.

Each :class:`SearchResult` carries:

* ``article_id``  — numeric id from ``/group/<id>`` / ``/i<id>`` / ``/a<id>``
                    URLs (when present)
* ``content_type`` — ``"article"`` / ``"video"`` / ``"page"``
* ``source``      — ``"toutiao"`` (direct) or ``"google"`` / ``"bing"`` (fallback)
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

TOUTIAO_HOME = "https://www.toutiao.com"
TOUTIAO_SEARCH = "https://so.toutiao.com/search"

# Match any toutiao URL.
TOUTIAO_HOST_RE = re.compile(
    r"https?://(?:[a-z0-9-]+\.)*toutiao\.com/", re.IGNORECASE
)
# Common article URL shapes:
#   https://www.toutiao.com/article/<id>/
#   https://www.toutiao.com/a<id>
#   https://www.toutiao.com/i<id>
#   https://www.toutiao.com/group/<id>
#   https://www.toutiao.com/w/a<id>/
ARTICLE_RE = re.compile(
    r"https?://(?:www\.)?toutiao\.com/(?:article|group|w/a|a|i)/?(\d+)",
    re.IGNORECASE,
)
VIDEO_RE = re.compile(
    r"https?://(?:www\.)?toutiao\.com/video/(\d+)", re.IGNORECASE
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
  // Toutiao result anchors point at /article/, /group/, /a..., or /i...
  const anchors = Array.from(document.querySelectorAll(
    'a[href*="toutiao.com/article/"], a[href*="toutiao.com/group/"], ' +
    'a[href*="toutiao.com/a"], a[href*="toutiao.com/i"], ' +
    'a[href*="/article/"], a[href*="/group/"]'
  ));
  const seen = new Set();
  const out = [];
  for (const a of anchors) {
    if (out.length >= limit) break;
    let href = a.getAttribute('href') || '';
    if (href.startsWith('//')) href = 'https:' + href;
    else if (href.startsWith('/')) href = 'https://www.toutiao.com' + href;
    const m = href.match(/(article|group|a|i|video)\/?(\d+)/);
    if (!m) continue;
    const id = m[2];
    if (seen.has(id)) continue;
    seen.add(id);

    let card = a.closest('article') || a.closest('[class*="result" i]') ||
               a.closest('li') || a.closest('div');
    if (!card) card = a;

    let title = (a.getAttribute('aria-label') || '').trim() ||
                pickText(card, ['h2', 'h3', '[class*="title" i]']) ||
                (a.textContent || '').trim();
    const source = pickText(card, [
      '[class*="source" i]', '[class*="name" i]', '[class*="author" i]',
    ]);
    const abstract = pickText(card, [
      '[class*="abstract" i]', '[class*="summary" i]', '[class*="content" i]',
    ]);
    const comments = pickText(card, [
      '[class*="comment" i]',
    ]).replace(/[^\d,]/g, '').replace(/,/g, '');

    out.push({article_id: id, url: href, title, source, abstract, comments});
  }
  return {anchors_seen: anchors.length, rows: out};
}
"""


class ToutiaoEngine(BaseEngine):
    """Toutiao search adapter with Google + Bing fallbacks."""

    name = "toutiao"
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

        log.info("[toutiao] direct path empty (likely SPA hydration stall); "
                 "falling back to Google site:toutiao.com")
        google = self._search_via(GoogleEngine, "google", query, limit)
        if google:
            self.last_status["mode"] = "google"
            return google

        log.info("[toutiao] Google fallback empty; trying Bing")
        bing = self._search_via(BingEngine, "bing", query, limit)
        if bing:
            self.last_status["mode"] = "bing"
        return bing

    # ------------------------------------------------------------ direct path

    def _search_direct(self, query: str, limit: int) -> list[SearchResult]:
        q = urllib.parse.quote(query)
        url = f"{TOUTIAO_SEARCH}?keyword={q}"
        log.info("[toutiao] navigating to %s", url)
        if not safe_goto(self.page, url, timeout=25000):
            return []

        # Give SPA a generous chance to hydrate.
        for _ in range(4):
            human_delay(1.5, 2.5)
            self._human_hints()
            try:
                count = self.page.evaluate(
                    "() => document.querySelectorAll("
                    "'a[href*=\"toutiao.com/article\"], a[href*=\"toutiao.com/group\"], "
                    "a[href*=\"toutiao.com/a\"], a[href*=\"toutiao.com/i\"]"
                    "').length"
                )
                if (count or 0) > 0:
                    break
            except Exception:
                pass

        try:
            data = self.page.evaluate(_PARSE_JS, limit) or {}
        except Exception as e:
            log.warning("[toutiao] parse JS failed: %s", e)
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
            article_id = (row.get("article_id") or "").strip()
            if not title or not url2:
                continue
            source = (row.get("source") or "").strip()
            abstract = (row.get("abstract") or "").strip()
            comments = (row.get("comments") or "").strip()

            head = []
            if source:
                head.append(source)
            if comments:
                head.append(f"评论 {comments}")
            snippet = " — ".join(p for p in (" · ".join(head), abstract) if p)[:320]

            r = SearchResult(title=title[:200], url=url2, snippet=snippet)
            r.article_id = article_id    # type: ignore[attr-defined]
            r.content_type = "article"   # type: ignore[attr-defined]
            r.source_name = source       # type: ignore[attr-defined]
            r.abstract = abstract        # type: ignore[attr-defined]
            r.comments_count = comments  # type: ignore[attr-defined]
            r.source = "toutiao"         # type: ignore[attr-defined]
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
            log.warning("[toutiao] cannot construct %s: %s", engine_cls.__name__, e)
            return []

        query_attempts = [
            f'site:toutiao.com "{query}"',
            f"site:toutiao.com {query}",
            f"toutiao.com {query}",
            f"{query} 头条",
        ]

        results: list[SearchResult] = []
        seen: set[str] = set()
        attempt_log: list[dict] = []

        for q in query_attempts:
            try:
                outer_results = outer.search(q, limit=max(limit * 3, 15))
            except Exception as e:
                log.warning("[toutiao] %s raised on %r: %s", source_label, q, e)
                outer_results = []

            attempt_log.append({"query": q, "organic": len(outer_results)})

            for r in outer_results:
                u = r.url or ""
                if not TOUTIAO_HOST_RE.search(u):
                    continue
                content_type, aid = self._classify_url(u)
                key = f"{content_type}:{aid or u}"
                if key in seen:
                    continue
                seen.add(key)

                title = self._clean_title(r.title or "") or u
                snippet = (r.snippet or "")[:320]

                new_r = SearchResult(title=title[:200], url=u, snippet=snippet)
                new_r.article_id = aid               # type: ignore[attr-defined]
                new_r.content_type = content_type    # type: ignore[attr-defined]
                new_r.source_name = ""               # type: ignore[attr-defined]
                new_r.abstract = snippet             # type: ignore[attr-defined]
                new_r.comments_count = ""            # type: ignore[attr-defined]
                new_r.source = source_label          # type: ignore[attr-defined]
                results.append(new_r)
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break

        self.last_status[f"{source_label}_attempts"] = attempt_log
        log.info("[toutiao] %s fallback returned %d results", source_label, len(results))
        return results

    @staticmethod
    def _classify_url(url: str) -> tuple[str, str]:
        if not url:
            return ("page", "")
        m = ARTICLE_RE.search(url)
        if m:
            return ("article", m.group(1))
        m = VIDEO_RE.search(url)
        if m:
            return ("video", m.group(1))
        return ("page", "")

    @staticmethod
    def _clean_title(title: str) -> str:
        if not title:
            return ""
        t = title.strip()
        for sep in (" - 今日头条", " | 今日头条", " - 头条", " | 头条",
                    " - Toutiao", " | Toutiao"):
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
