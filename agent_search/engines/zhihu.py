"""知乎 (Zhihu) search adapter with Google site: fallback.

Strategy
--------
1. **Direct path** — ``https://www.zhihu.com/search?type=content&q=<q>``.
   Without auth cookies Zhihu hard-walls the SERP behind a login dialog
   (body collapses to ~600 chars of login UI). When that happens the
   direct path returns ``[]`` and we move on.
2. **Google site: fallback** — drive :class:`GoogleEngine` with the
   query ``site:zhihu.com <q>`` and keep only hits that point at a
   ``/question/<id>``, ``/answer/<id>`` or ``zhuanlan.zhihu.com/p/<id>``
   URL (the three real content surfaces).

Each :class:`SearchResult` carries (when known):

* ``zhihu_id``  — numeric id from the URL
* ``content_type`` — ``"question"`` / ``"answer"`` / ``"article"`` / ``""``
* ``author`` / ``voteup_count`` / ``comment_count`` (only on direct path —
  Google snippets do not expose engagement counters)
* ``excerpt``   — first paragraph of the content
* ``source``    — ``"zhihu"`` (direct) or ``"google"`` (fallback)
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

log = logging.getLogger(__name__)

ZHIHU_HOME = "https://www.zhihu.com"
ZHIHU_SEARCH = "https://www.zhihu.com/search"

RESULT_WAIT_MS = 8000

RESULT_SELECTORS = [
    ".SearchResult-Card",
    "div.List-item",
    ".Card.SearchResult-Card",
    "section.SearchResult",
]

DISMISS_BUTTON_SELECTORS = [
    ".Modal-closeButton",
    ".Modal .Modal-closeButton",
    "button.Modal-closeButton",
    "[aria-label='关闭']",
    "[aria-label*='close' i]",
    ".signFlowModal__close",
]

# URL fragments that mean we're on a real piece of Zhihu content.
ZHIHU_QUESTION_RE = re.compile(
    r"https?://(?:www\.)?zhihu\.com/question/(\d+)(?:/answer/(\d+))?"
)
ZHIHU_ANSWER_RE = re.compile(
    r"https?://(?:www\.)?zhihu\.com/answer/(\d+)"
)
ZHIHU_ARTICLE_RE = re.compile(
    r"https?://zhuanlan\.zhihu\.com/p/(\d+)"
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
  const cardSelectors = [
    ".SearchResult-Card",
    "div.List-item",
    "section.SearchResult",
  ];
  let cards = [];
  for (const sel of cardSelectors) {
    const list = document.querySelectorAll(sel);
    if (list.length) {
      cards = Array.from(list);
      break;
    }
  }
  const out = [];
  for (const card of cards) {
    if (out.length >= limit) break;
    const titleA =
      card.querySelector("h2.ContentItem-title a") ||
      card.querySelector(".ContentItem-title a") ||
      card.querySelector("h2 a") ||
      card.querySelector("a[href*='/question/']") ||
      card.querySelector("a[href*='zhuanlan.zhihu.com/p/']") ||
      card.querySelector("a[href*='/answer/']");
    if (!titleA) continue;
    let title = (titleA.textContent || "").trim();
    let href = titleA.getAttribute("href") || "";
    if (!title || !href) continue;
    if (href.startsWith("//")) href = "https:" + href;
    else if (href.startsWith("/")) href = "https://www.zhihu.com" + href;
    const author = pickText(card, [
      ".AuthorInfo-name a",
      ".AuthorInfo-name .UserLink-link",
      ".AuthorInfo-name",
      ".UserLink-link",
    ]);
    let voteup = pickText(card, [
      "button.VoteButton--up .Button-label",
      "button.VoteButton--up",
      ".VoteButton--up .Button-label",
      ".VoteButton--up",
    ]);
    voteup = voteup.replace(/^赞同/, "").replace(/[条赞同 ]+$/g, "").trim();
    // Comment counter — Zhihu uses several layouts:
    //   <button class="ContentItem-action ... Button--withIcon"> 12 条评论 </button>
    //   <a href="…/answer/123" class="...">… 评论</a>
    let comment = "";
    {
      // 1) Try the action bar buttons first.
      const actions = card.querySelectorAll(
        ".ContentItem-actions button, .ContentItem-actions a, " +
        ".RichContent .ContentItem-actions button"
      );
      for (const a of actions) {
        const t = (a.textContent || "").trim();
        const m = t.match(/(\d[\d,]*)\s*(?:条)?\s*评论/);
        if (m) { comment = m[1].replace(/,/g, ""); break; }
      }
      // 2) Fallback: scan whole card text for "N 条评论" / "N 评论".
      if (!comment) {
        const all = (card.textContent || "");
        const m = all.match(/(\d[\d,]*)\s*(?:条)?\s*评论/);
        if (m) comment = m[1].replace(/,/g, "");
      }
    }
    const excerpt = pickText(card, [
      ".RichContent-inner",
      ".SearchItem-excerpt",
      ".Highlight",
      ".RichText",
      "p",
    ]);
    out.push({title, url: href, author, voteup_count: voteup, comment_count: comment, excerpt});
  }
  return {cards_seen: cards.length, rows: out};
}
"""


class ZhihuEngine(BaseEngine):
    """Zhihu search adapter with Google fallback."""

    name = "zhihu"
    max_retries = 1  # Both layers self-recover; no need for outer retries.

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}

    # Override search() to skip BaseEngine's check_blocked / retry, since
    # we explicitly handle login walls via Google fallback.
    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        return self._do_search(query, limit)

    # ------------------------------------------------------------------ search

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # 1) Try direct.
        direct = self._search_direct(query, limit)
        if direct:
            self.last_status["mode"] = "direct"
            return direct

        # 2) Google site: fallback.
        log.info("[zhihu] direct path empty (likely login wall); "
                 "falling back to Google site:zhihu.com")
        fallback = self._search_google_fallback(query, limit)
        if fallback:
            self.last_status["mode"] = "google"
        return fallback

    # ------------------------------------------------------------ direct path

    def _search_direct(self, query: str, limit: int) -> list[SearchResult]:
        if safe_goto(self.page, ZHIHU_HOME + "/", timeout=20000, retries=1):
            human_delay(0.8, 1.6)
            self._dismiss_overlays()

        q = urllib.parse.quote(query)
        url = f"{ZHIHU_SEARCH}?type=content&q={q}"
        log.info("[zhihu] navigating to %s", url)
        if not safe_goto(self.page, url, timeout=25000):
            return []

        human_delay(1.2, 2.4)
        self._dismiss_overlays()

        for sel in RESULT_SELECTORS:
            try:
                self.page.wait_for_selector(sel, timeout=RESULT_WAIT_MS)
                break
            except Exception:
                continue

        self._human_hints()
        self._dismiss_overlays()

        try:
            data = self.page.evaluate(_PARSE_JS, limit) or {}
        except Exception as e:
            log.warning("[zhihu] parse JS failed: %s", e)
            data = {}

        cards_seen = int(data.get("cards_seen") or 0)
        rows = data.get("rows") or []
        try:
            body_len = len(self.page.inner_text("body") or "")
        except Exception:
            body_len = 0

        self.last_status = {
            "url": self.page.url,
            "title": self.page.title() if hasattr(self.page, "title") else "",
            "body_len": body_len,
            "cards_seen": cards_seen,
            "count": len(rows),
        }

        results: list[SearchResult] = []
        for row in rows:
            title = (row.get("title") or "").strip()
            url2 = (row.get("url") or "").strip()
            if not title or not url2:
                continue
            author = (row.get("author") or "").strip()
            voteup = (row.get("voteup_count") or "").strip()
            comment = (row.get("comment_count") or "").strip()
            excerpt = (row.get("excerpt") or "").strip()

            head = []
            if author:
                head.append(author)
            if voteup:
                head.append(f"赞同 {voteup}")
            if comment:
                head.append(f"评论 {comment}")
            head_text = " · ".join(head)
            snippet = " — ".join(p for p in (head_text, excerpt) if p)
            if len(snippet) > 320:
                snippet = snippet[:320].rstrip() + "…"

            r = SearchResult(title=title, url=url2, snippet=snippet)
            r.author = author                # type: ignore[attr-defined]
            r.voteup_count = voteup          # type: ignore[attr-defined]
            r.comment_count = comment        # type: ignore[attr-defined]
            r.excerpt = excerpt              # type: ignore[attr-defined]
            r.content_type = self._infer_content_type(url2)  # type: ignore[attr-defined]
            r.zhihu_id = self._extract_id(url2)              # type: ignore[attr-defined]
            r.source = "zhihu"               # type: ignore[attr-defined]
            results.append(r)
            if len(results) >= limit:
                break
        return results

    # ------------------------------------------------------------ Google fallback

    def _search_google_fallback(self, query: str, limit: int) -> list[SearchResult]:
        try:
            google = GoogleEngine(self.page)
        except Exception as e:
            log.warning("[zhihu] cannot construct GoogleEngine: %s", e)
            return []

        query_attempts = [
            f'site:zhihu.com "{query}"',
            f"site:zhihu.com {query}",
            f"site:zhuanlan.zhihu.com {query}",
        ]

        results: list[SearchResult] = []
        seen: set[str] = set()
        attempt_log: list[dict] = []

        for q_idx, gq in enumerate(query_attempts, start=1):
            try:
                google_results = google.search(gq, limit=max(limit * 3, 15))
            except Exception as e:
                log.warning("[zhihu] google fallback raised on %r: %s", gq, e)
                google_results = []

            attempt_log.append({
                "query": gq,
                "organic": len(google_results),
            })

            for r in google_results:
                url = r.url or ""
                content_type, zhihu_id = self._classify_zhihu_url(url)
                if not content_type:
                    continue
                # Dedupe by id
                key = f"{content_type}:{zhihu_id}"
                if key in seen:
                    continue
                seen.add(key)

                title = self._clean_google_title(r.title or "") or url
                snippet = self._clean_google_snippet(r.snippet or "")[:320]

                new_r = SearchResult(title=title, url=url, snippet=snippet)
                new_r.zhihu_id = zhihu_id              # type: ignore[attr-defined]
                new_r.content_type = content_type      # type: ignore[attr-defined]
                new_r.author = ""                       # type: ignore[attr-defined]
                new_r.voteup_count = ""                # type: ignore[attr-defined]
                new_r.comment_count = ""               # type: ignore[attr-defined]
                new_r.excerpt = snippet                # type: ignore[attr-defined]
                new_r.source = "google"                # type: ignore[attr-defined]
                results.append(new_r)
                if len(results) >= limit:
                    break
            if len(results) >= limit:
                break

        self.last_status["google_attempts"] = attempt_log
        log.info("[zhihu] google fallback returned %d results", len(results))
        return results

    @staticmethod
    def _classify_zhihu_url(url: str) -> tuple[str, str]:
        if not url:
            return ("", "")
        m = ZHIHU_QUESTION_RE.match(url)
        if m:
            return ("question", m.group(2) or m.group(1))
        m = ZHIHU_ANSWER_RE.match(url)
        if m:
            return ("answer", m.group(1))
        m = ZHIHU_ARTICLE_RE.match(url)
        if m:
            return ("article", m.group(1))
        return ("", "")

    @staticmethod
    def _infer_content_type(url: str) -> str:
        if "/answer/" in url:
            return "answer"
        if "/question/" in url:
            return "question"
        if "zhuanlan.zhihu.com/p/" in url:
            return "article"
        return ""

    @staticmethod
    def _extract_id(url: str) -> str:
        for rx in (ZHIHU_QUESTION_RE, ZHIHU_ANSWER_RE, ZHIHU_ARTICLE_RE):
            m = rx.match(url)
            if m:
                return m.group(2) if rx is ZHIHU_QUESTION_RE and m.group(2) else m.group(1)
        return ""

    @staticmethod
    def _clean_google_title(title: str) -> str:
        if not title:
            return ""
        t = title.strip()
        for sep in (" - 知乎", " | 知乎", " — 知乎", " - 知乎专栏"):
            if t.endswith(sep):
                t = t[: -len(sep)].strip()
                break
        return t

    @staticmethod
    def _clean_google_snippet(snippet: str) -> str:
        """Strip Google-specific noise from a result snippet."""
        if not snippet:
            return ""
        s = snippet.strip()
        # Google appends "Read more" / "More" / "阅读全文" as a clickable
        # truncator on long answers — drop it.
        for tail in ("Read more", "More", "阅读全文", "展开阅读全文"):
            if s.endswith(tail):
                s = s[: -len(tail)].rstrip(" .…")
        # Collapse whitespace.
        return " ".join(s.split())

    # -------------------------------------------------------------- diagnostics

    def selector_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for sel in RESULT_SELECTORS:
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        for sel in (
            "h2.ContentItem-title a",
            "a[href*='/question/']",
            "a[href*='zhuanlan.zhihu.com/p/']",
            "main a",
        ):
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        return counts

    # ------------------------------------------------------------------ helpers

    def _dismiss_overlays(self):
        for sel in DISMISS_BUTTON_SELECTORS:
            try:
                btn = self.page.query_selector(sel)
                if btn:
                    btn.click(timeout=2000)
                    log.info("[zhihu] dismissed overlay (%s)", sel)
                    human_delay(0.2, 0.5)
            except Exception:
                continue

    def _human_hints(self):
        try:
            self.page.mouse.move(
                random.randint(120, 760),
                random.randint(180, 520),
                steps=6,
            )
        except Exception:
            pass
        try:
            self.page.evaluate(
                "(y) => window.scrollBy(0, y)",
                random.randint(180, 520),
            )
        except Exception:
            pass
        time.sleep(random.uniform(0.3, 0.7))
