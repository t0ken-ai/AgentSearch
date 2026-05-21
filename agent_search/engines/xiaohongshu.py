"""小红书 (Xiaohongshu / RED) search adapter.

Xiaohongshu's web search at:

    https://www.xiaohongshu.com/search_result?keyword=<q>

is heavily auth-gated:

* Without a logged-in session it serves a login modal and the body
  collapses to a few hundred characters.
* It tries to push every visitor onto the mobile app via QR codes.
* Note cards (`.note-item`) only render when a ``web_session``
  cookie is present.

This adapter therefore mirrors the netflix / instagram / tiktok
strategy:

1. **Direct path** — visit ``/search_result`` and try to harvest any
   ``/explore/<note_id>`` anchors. When logged out this returns nothing.
2. **Google site: fallback** — drive :class:`GoogleEngine` with
   ``site:xiaohongshu.com/explore <q>`` (and looser variants), then
   keep only hits that match a note URL.

Each :class:`SearchResult` carries:

* ``note_id``      — the 24-hex id from ``/explore/<id>`` / ``/discovery/<id>``
* ``user``         — author display name (only on direct path)
* ``likes``        — ``"3.2万"``-style string (only on direct path)
* ``note_type``    — ``"image"`` / ``"video"`` / ``""``
* ``source``       — ``"xiaohongshu"`` (direct) or ``"google"`` (fallback)
"""

from __future__ import annotations

import logging
import random
import re
import time
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult
from .bing import BingEngine
from .google import GoogleEngine

log = logging.getLogger(__name__)

XHS_HOME = "https://www.xiaohongshu.com"
XHS_SEARCH = "https://www.xiaohongshu.com/search_result"

# A xiaohongshu note id is typically 24 hex chars but the path can include
# other segments; we extract whatever id-looking token is in the URL.
NOTE_ID_RE = re.compile(r"([0-9a-f]{20,32})", re.IGNORECASE)
# Match any xiaohongshu.com URL — note pages live at /explore/<id>,
# /discovery/item/<id>, /user/profile/<uid>/<id>, etc.
XHS_HOST_RE = re.compile(
    r"https?://(?:[a-z0-9-]+\.)*xiaohongshu\.com/", re.IGNORECASE
)
EXPLORE_RE = re.compile(
    r"https?://(?:www\.)?xiaohongshu\.com/(?:explore|discovery/item)/([0-9a-f]{20,32})",
    re.IGNORECASE,
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
  // Cards have changed shape several times; collect via /explore/ anchors.
  const anchors = Array.from(document.querySelectorAll('a[href*="/explore/"]'));
  const seen = new Set();
  const out = [];
  for (const a of anchors) {
    if (out.length >= limit) break;
    const href = a.getAttribute('href') || '';
    const m = href.match(/\/explore\/([0-9a-f]{24})/i);
    if (!m) continue;
    const noteId = m[1].toLowerCase();
    if (seen.has(noteId)) continue;
    seen.add(noteId);

    let card = a.closest('section') || a.closest('.note-item') ||
               a.closest('[class*="card"]') || a.closest('div');
    if (!card) card = a;

    let title = (a.getAttribute('aria-label') || '').trim();
    if (!title) {
      const img = card.querySelector('img[alt]');
      if (img) title = (img.getAttribute('alt') || '').trim();
    }
    if (!title) {
      title = pickText(card, [
        '.title', '.note-title', '[class*="title" i]',
        'h2', 'h3', 'p',
      ]);
    }
    if (!title) {
      title = (a.textContent || '').trim();
    }
    const user = pickText(card, [
      '.user-info .name',
      '.author', '.user-name', '[class*="author" i]',
      '.name',
    ]);
    let likes = pickText(card, [
      '.like-count', '.like-wrapper .count',
      '[class*="like" i] [class*="count" i]',
      '.count',
    ]);
    likes = likes.replace(/^\s*点赞\s*/, '').trim();

    let url = href;
    if (url.startsWith('//')) url = 'https:' + url;
    else if (url.startsWith('/')) url = 'https://www.xiaohongshu.com' + url;

    out.push({note_id: noteId, title, url, user, likes});
  }
  return {anchors_seen: anchors.length, rows: out};
}
"""


class XiaohongshuEngine(BaseEngine):
    """Xiaohongshu (RED) search adapter with Google fallback."""

    name = "xiaohongshu"
    max_retries = 1

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}

    # Skip BaseEngine.search()'s check_blocked / retry — Xiaohongshu
    # always trips empty_page on the direct path.
    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        return self._do_search(query, limit)

    # ------------------------------------------------------------------ search

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        direct = self._search_direct(query, limit)
        if direct:
            self.last_status["mode"] = "direct"
            return direct

        log.info("[xiaohongshu] direct path empty (likely login wall); "
                 "falling back to Google site:xiaohongshu.com")
        fallback = self._search_google_fallback(query, limit)
        if fallback:
            self.last_status["mode"] = "google"
            return fallback

        log.info("[xiaohongshu] google fallback empty (likely rate-limited); "
                 "trying Bing site:xiaohongshu.com")
        bing_fallback = self._search_bing_fallback(query, limit)
        if bing_fallback:
            self.last_status["mode"] = "bing"
        return bing_fallback

    # ------------------------------------------------------------ direct path

    def _search_direct(self, query: str, limit: int) -> list[SearchResult]:
        if safe_goto(self.page, XHS_HOME + "/", timeout=20000, retries=1):
            human_delay(0.8, 1.6)

        q = urllib.parse.quote(query)
        url = f"{XHS_SEARCH}?keyword={q}"
        log.info("[xiaohongshu] navigating to %s", url)
        if not safe_goto(self.page, url, timeout=25000):
            return []

        human_delay(1.5, 3.0)
        self._human_hints()

        try:
            data = self.page.evaluate(_PARSE_JS, limit) or {}
        except Exception as e:
            log.warning("[xiaohongshu] parse JS failed: %s", e)
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
            note_id = (row.get("note_id") or "").strip()
            if not title or not url2 or not note_id:
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
            r.note_id = note_id           # type: ignore[attr-defined]
            r.user = user                 # type: ignore[attr-defined]
            r.likes = likes               # type: ignore[attr-defined]
            r.note_type = ""              # type: ignore[attr-defined]
            r.source = "xiaohongshu"      # type: ignore[attr-defined]
            results.append(r)
            if len(results) >= limit:
                break
        return results

    # ------------------------------------------------------------ Google fallback

    def _search_google_fallback(self, query: str, limit: int) -> list[SearchResult]:
        try:
            google = GoogleEngine(self.page)
        except Exception as e:
            log.warning("[xiaohongshu] cannot construct GoogleEngine: %s", e)
            return []

        query_attempts = [
            f'site:xiaohongshu.com/explore "{query}"',
            f'site:xiaohongshu.com "{query}"',
            f"{query} 小红书",
        ]

        results: list[SearchResult] = []
        seen: set[str] = set()
        attempt_log: list[dict] = []

        for q_idx, gq in enumerate(query_attempts, start=1):
            try:
                google_results = google.search(gq, limit=max(limit * 3, 15))
            except Exception as e:
                log.warning("[xiaohongshu] google fallback raised on %r: %s", gq, e)
                google_results = []

            attempt_log.append({"query": gq, "organic": len(google_results)})

            for r in google_results:
                u = r.url or ""
                if not XHS_HOST_RE.search(u):
                    continue
                if not self._is_content_url(u):
                    continue
                # Try to pull a note id; if absent, dedupe by full URL.
                m_explore = EXPLORE_RE.search(u)
                note_id = m_explore.group(1).lower() if m_explore else ""
                key = note_id or u
                if key in seen:
                    continue
                seen.add(key)

                title = self._clean_google_title(r.title or "") or u
                snippet = (r.snippet or "")[:320]

                if note_id:
                    canonical = f"https://www.xiaohongshu.com/explore/{note_id}"
                else:
                    canonical = u
                new_r = SearchResult(title=title[:200], url=canonical, snippet=snippet)
                new_r.note_id = note_id           # type: ignore[attr-defined]
                new_r.user = ""                    # type: ignore[attr-defined]
                new_r.likes = ""                   # type: ignore[attr-defined]
                new_r.note_type = ""               # type: ignore[attr-defined]
                new_r.source = "google"            # type: ignore[attr-defined]
                results.append(new_r)
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break

        self.last_status["google_attempts"] = attempt_log
        log.info("[xiaohongshu] google fallback returned %d results", len(results))
        return results

    # ------------------------------------------------------------ Bing fallback

    def _search_bing_fallback(self, query: str, limit: int) -> list[SearchResult]:
        """Last-resort fallback when Google blocks us."""
        try:
            bing = BingEngine(self.page)
        except Exception as e:
            log.warning("[xiaohongshu] cannot construct BingEngine: %s", e)
            return []

        query_attempts = [
            f'site:xiaohongshu.com "{query}"',
            f"{query} 小红书",
        ]

        results: list[SearchResult] = []
        seen: set[str] = set()
        attempt_log: list[dict] = []

        for gq in query_attempts:
            try:
                bing_results = bing.search(gq, limit=max(limit * 3, 15))
            except Exception as e:
                log.warning("[xiaohongshu] bing fallback raised on %r: %s", gq, e)
                bing_results = []

            attempt_log.append({"query": gq, "organic": len(bing_results)})

            for r in bing_results:
                u = r.url or ""
                if not XHS_HOST_RE.search(u):
                    continue
                if not self._is_content_url(u):
                    continue
                m_explore = EXPLORE_RE.search(u)
                note_id = m_explore.group(1).lower() if m_explore else ""
                key = note_id or u
                if key in seen:
                    continue
                seen.add(key)

                title = self._clean_google_title(r.title or "") or u
                snippet = (r.snippet or "")[:320]

                if note_id:
                    canonical = f"https://www.xiaohongshu.com/explore/{note_id}"
                else:
                    canonical = u
                new_r = SearchResult(title=title[:200], url=canonical, snippet=snippet)
                new_r.note_id = note_id            # type: ignore[attr-defined]
                new_r.user = ""                     # type: ignore[attr-defined]
                new_r.likes = ""                    # type: ignore[attr-defined]
                new_r.note_type = ""                # type: ignore[attr-defined]
                new_r.source = "bing"               # type: ignore[attr-defined]
                results.append(new_r)
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break

        self.last_status["bing_attempts"] = attempt_log
        log.info("[xiaohongshu] bing fallback returned %d results", len(results))
        return results

    @staticmethod
    def _clean_google_title(title: str) -> str:
        if not title:
            return ""
        t = title.strip()
        for sep in (" - 小红书", " | 小红书", " - Xiaohongshu", " | Xiaohongshu"):
            if t.endswith(sep):
                t = t[: -len(sep)].strip()
                break
        return t

    @staticmethod
    def _is_content_url(url: str) -> bool:
        """Filter out generic feed / landing pages.

        Google sometimes surfaces channel feeds like
        ``/explore?channel_id=homefeed.travel_v3`` which match
        ``xiaohongshu.com`` but are not actual notes. Keep only URLs that
        either (a) carry a note id in the path or (b) point at a non-root
        path that is not the bare explore / discovery feed.
        """
        if not url:
            return False
        try:
            parsed = urllib.parse.urlparse(url)
        except Exception:
            return False
        path = (parsed.path or "/").rstrip("/")
        # Real note URLs: keep.
        if EXPLORE_RE.search(url):
            return True
        # Bare landing pages — drop.
        if path in ("", "/", "/explore", "/discovery", "/discovery/item"):
            return False
        # Anything with a deeper path is most likely real content.
        return True

    # -------------------------------------------------------------- diagnostics

    def selector_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for sel in (
            "section.note-item",
            "a[href*='/explore/']",
            "a[href*='/discovery/item/']",
            "main a",
        ):
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        return counts

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
