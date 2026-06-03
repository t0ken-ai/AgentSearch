"""WeChat Official Account (微信公众号) search adapter.

Mirrors the behaviour of the ``weixin_search_mcp`` project
(https://github.com/fancyboi999/weixin_search_mcp) but routes every
request through AgentSearch's stealth browser instead of plain
``requests`` so Sogou's aggressive anti-spider is far easier to ride.

Sogou is the only public gateway into WeChat's otherwise-closed article
graph. We hit ``https://weixin.sogou.com/weixin`` with:

* ``type=2`` — article search. Searching an account's name returns that
  account's recent articles (this is how you "crawl all articles of an
  author" without a logged-in WeChat session). Default mode.

Note: Sogou exposes NO server-side "restrict to this account" parameter —
article search always mixes results from many accounts. To crawl a single
author, pass ``author="<公众号名>"``: the engine auto-paginates and keeps
only rows whose ``公众号`` (read from ``.s-p .all-time-y2``) matches.
* ``type=1`` — official-account search. Returns the accounts themselves
  (name, WeChat id, intro, qr) so the agent can confirm the right author
  before pulling articles.

Pipeline (article mode)
-----------------------
1. Navigate the SERP for pages ``page`` .. ``page + max_pages - 1`` and
   scrape each ``li[id^='sogou_vr_11002601_box_']`` row: title, Sogou
   ``/link?url=...`` redirector, account name, publish time.
2. ``resolve_urls`` (default True): open each redirector in the browser
   and read the final ``mp.weixin.qq.com`` URL it lands on. Sogou serves
   the real link through a JS snippet (``url += '...'``); the browser
   executes it for us, so we just read ``page.url`` after navigation
   (with a JS-parse fallback for the rare no-redirect case).
3. ``fetch_content`` (default False): also pull the article body text
   from ``#js_content`` on the ``mp.weixin.qq.com`` page.

Anti-spider detection: Sogou bounces abusive traffic to
``/antispider/...`` or injects ``seccodeRight`` / ``anti.min.css`` into
the body — both are treated as a block.

Diagnostics: ``engine.last_status`` holds ``url``, ``title``,
``body_len``, ``mode``, ``pages``, optional ``block_reason`` / ``count``.
"""

from __future__ import annotations

import logging
import random
import re
import time
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

WEIXIN_HOME = "https://weixin.sogou.com"

# Anti-spider signatures (URL fragment or body markers).
BLOCK_URL_FRAGMENTS = ("/antispider/", "antispider.so")
BLOCK_BODY_MARKERS = ("seccoderight", "anti.min.css", "请输入验证码", "请输入下方验证码")

# Article-row title links carry ids like ``sogou_vr_11002601_title_0``.
ARTICLE_BOX_SELECTOR = "li[id^='sogou_vr_11002601_box_']"
ARTICLE_TITLE_SELECTOR = "a[id^='sogou_vr_11002601_title_']"

# Official-account rows.
ACCOUNT_BOX_SELECTOR = "li"
ACCOUNT_TITLE_SELECTOR = "p.tit a, a[id^='sogou_vr_11002301_title_']"


class WeixinEngine(BaseEngine):
    """微信公众号搜索 via Sogou (https://weixin.sogou.com/weixin)."""

    name = "weixin"
    max_retries = 2

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}

    # Override search() to accept the engine-specific options forwarded by
    # the MCP ``search`` tool / CLI, and to drive pagination ourselves.
    def search(
        self,
        query: str,
        limit: int = 10,
        page: int = 1,
        max_pages: int = 1,
        mode: str = "article",
        author: str = "",
        resolve_urls: bool = True,
        fetch_content: bool = False,
    ) -> list[SearchResult]:
        if mode not in ("article", "account"):
            raise ValueError(
                f"unsupported mode {mode!r}; expected 'article' or 'account'"
            )
        # When restricting to one author, Sogou still returns mixed
        # accounts per page, so widen the crawl automatically to find
        # enough matches unless the caller asked for a specific span.
        if author and max_pages <= 1:
            max_pages = 10
        for attempt in range(self.max_retries):
            try:
                results = self._do_search(
                    query,
                    limit=limit,
                    start_page=page,
                    max_pages=max_pages,
                    mode=mode,
                    author=author,
                    resolve_urls=resolve_urls,
                    fetch_content=fetch_content,
                )
                if results:
                    return results
                if self.last_status.get("block_reason"):
                    log.warning(
                        "[weixin] blocked (attempt %d): %s",
                        attempt + 1, self.last_status["block_reason"],
                    )
                    human_delay(3, 6)
                    continue
                log.warning("[weixin] no results (attempt %d)", attempt + 1)
            except Exception as e:
                log.error("[weixin] error (attempt %d): %s", attempt + 1, e)
            human_delay(2, 4)
        return []

    # ------------------------------------------------------------------ search

    def _do_search(
        self,
        query: str,
        limit: int,
        start_page: int,
        max_pages: int,
        mode: str,
        author: str,
        resolve_urls: bool,
        fetch_content: bool,
    ) -> list[SearchResult]:
        # Warm up so cookies (SUID/SUV/SNUID) settle before searching.
        if safe_goto(self.page, WEIXIN_HOME + "/", timeout=20000, retries=1):
            human_delay(1.0, 2.0)
            self._human_hints()

        s_type = "1" if mode == "account" else "2"
        author_norm = self._norm_author(author)
        results: list[SearchResult] = []
        seen: set[str] = set()
        pages_fetched = 0
        raw_seen = 0

        # Pull a few extra raw rows per page so the per-page page cap (≈10)
        # still leaves room to reach ``limit`` after author filtering.
        page_cap = limit if not author_norm else max(limit, 10)

        end_page = start_page + max(1, max_pages) - 1
        for pg in range(start_page, end_page + 1):
            url = self._build_url(query, pg, s_type)
            log.info("[weixin] navigating to %s", url)
            if not safe_goto(self.page, url):
                break
            human_delay(1.2, 2.4)
            self._human_hints()
            pages_fetched += 1

            if self._is_blocked():
                # Stop paginating; surface whatever we already have.
                break

            page_rows = (
                self._extract_articles(page_cap)
                if mode == "article"
                else self._extract_accounts(page_cap)
            )
            new_rows = 0
            for r in page_rows:
                key = r.url
                if not key or key in seen:
                    continue
                seen.add(key)
                new_rows += 1
                # Author filter: keep only rows whose 公众号 matches.
                if author_norm and not self._author_matches(r, author_norm):
                    continue
                results.append(r)
                if len(results) >= limit:
                    break
            raw_seen += new_rows
            # No new rows on this page → Sogou has no more results.
            if new_rows == 0 or len(results) >= limit:
                break
            if pg < end_page:
                time.sleep(1.0 + random.random())

        self.last_status["mode"] = mode
        self.last_status["pages"] = pages_fetched
        self.last_status["raw_seen"] = raw_seen
        self.last_status["count"] = len(results)
        if author:
            self.last_status["author"] = author

        # Resolve Sogou redirectors → real mp.weixin.qq.com URLs, and
        # optionally fetch the article body. Only meaningful for articles.
        if mode == "article" and (resolve_urls or fetch_content):
            self._enrich(results, fetch_content=fetch_content)

        return results

    def _build_url(self, query: str, page: int, s_type: str) -> str:
        params = {
            "type": s_type,
            "s_from": "input",
            "query": query,
            "ie": "utf8",
            "page": page,
            "_sug_": "n",
            "_sug_type_": "",
        }
        return f"{WEIXIN_HOME}/weixin?" + urllib.parse.urlencode(params)

    # ---------------------------------------------------------------- articles

    def _extract_articles(self, limit: int) -> list[SearchResult]:
        try:
            boxes = self.page.query_selector_all(ARTICLE_BOX_SELECTOR)
        except Exception:
            boxes = []
        if not boxes:
            log.info("[weixin] no article boxes matched")
            return []

        out: list[SearchResult] = []
        for box in boxes:
            title_el = box.query_selector(ARTICLE_TITLE_SELECTOR) or box.query_selector(
                ".txt-box h3 a"
            )
            if not title_el:
                continue
            try:
                title = (title_el.inner_text() or "").strip()
                href = title_el.get_attribute("href") or ""
            except Exception:
                continue
            if not title or not href:
                continue
            link = self._abs(href)

            # The 公众号 (account) name sits in ``.s-p .all-time-y2`` on the
            # article SERP — NOT in an ``a.account`` anchor.
            account = self._text(
                box,
                [".s-p .all-time-y2", ".all-time-y2", "a.account", ".s-p .account"],
            )
            snippet = self._text(box, ["p.txt-info", ".txt-box p"])
            publish = self._text(box, [".s-p .s2", ".s2"])
            published_date = self._normalize_date(publish)

            composed = snippet
            if account:
                composed = f"[{account}] {snippet}".strip()

            sr = SearchResult(
                title=title,
                url=link,
                snippet=composed,
                published_date=published_date,
            )
            sr.account = account  # type: ignore[attr-defined]
            sr.sogou_link = link  # type: ignore[attr-defined]
            sr.real_url = ""  # type: ignore[attr-defined]
            out.append(sr)
            if len(out) >= limit:
                break
        return out

    # ---------------------------------------------------------------- accounts

    def _extract_accounts(self, limit: int) -> list[SearchResult]:
        try:
            boxes = self.page.query_selector_all(ACCOUNT_BOX_SELECTOR)
        except Exception:
            boxes = []
        out: list[SearchResult] = []
        for box in boxes:
            title_el = box.query_selector(ACCOUNT_TITLE_SELECTOR)
            if not title_el:
                continue
            try:
                name = (title_el.inner_text() or "").strip()
                href = title_el.get_attribute("href") or ""
            except Exception:
                continue
            if not name or not href:
                continue
            wxid = self._text(box, ["label + *", "p.info", ".info"])
            intro = self._text(box, ["dl dd", "p.sp-txt", ".gzh-box2 .sp-txt"])
            sr = SearchResult(
                title=name,
                url=self._abs(href),
                snippet=(f"微信号: {wxid} | {intro}".strip(" |") if (wxid or intro) else ""),
            )
            sr.wechat_id = wxid  # type: ignore[attr-defined]
            out.append(sr)
            if len(out) >= limit:
                break
        return out

    # -------------------------------------------------------- url + content

    def _enrich(self, results: list[SearchResult], fetch_content: bool) -> None:
        """Resolve Sogou redirectors and (optionally) fetch article bodies.

        Opening the Sogou ``/link?url=...`` redirector in the browser makes
        the JS hop straight to the real ``mp.weixin.qq.com`` article — so a
        single navigation per result yields both the real URL (read from the
        in-page ``location.href``, since the redirect doesn't update the
        Python-side ``page.url``) and the article body (``#js_content``).
        """
        for r in results:
            sogou_link = getattr(r, "sogou_link", "") or r.url
            real_url, body = self._open_article(sogou_link, fetch_content)
            if real_url:
                r.real_url = real_url  # type: ignore[attr-defined]
                r.url = real_url
            if body:
                r.body_text = body  # type: ignore[attr-defined]
                r.body_word_count = len(body)  # type: ignore[attr-defined]
            human_delay(0.6, 1.4)

    def _open_article(self, sogou_link: str, fetch_content: bool) -> tuple[str, str]:
        """Navigate the Sogou redirector → return (real_url, body_text)."""
        if not sogou_link:
            return "", ""
        try:
            if not safe_goto(self.page, sogou_link, timeout=20000, retries=1):
                return "", ""
        except Exception:
            return "", ""

        # The hop to mp.weixin.qq.com runs in-page; poll location.href
        # (NOT page.url, which keeps the stale redirector URL).
        real_url = ""
        for _ in range(10):
            cur = self._loc_href()
            if "mp.weixin.qq.com" in cur:
                real_url = cur
                break
            time.sleep(0.5)

        body = ""
        if fetch_content:
            body = self._extract_js_content()
        return real_url, body

    def _loc_href(self) -> str:
        try:
            return self.page.evaluate("() => location.href") or ""
        except Exception:
            try:
                return self.page.url or ""
            except Exception:
                return ""

    def _extract_js_content(self) -> str:
        """Read the WeChat article body from ``#js_content`` once it loads."""
        text = ""
        for _ in range(8):
            try:
                el = self.page.query_selector("#js_content")
                if el:
                    text = el.inner_text() or ""
                    if text.strip():
                        break
            except Exception:
                pass
            time.sleep(0.5)
        # Collapse the per-line whitespace the way the original tool did.
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return "\n".join(lines)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _norm_author(author: str) -> str:
        """Normalize an account name for tolerant matching."""
        if not author:
            return ""
        return "".join(author.split()).strip().lower()

    def _author_matches(self, r: SearchResult, author_norm: str) -> bool:
        """True when the result's 公众号 matches the requested author.

        Matching is whitespace-insensitive and case-insensitive, and
        succeeds when either name contains the other (so '老刘投放笔记'
        matches whether Sogou reports the full name or a truncated form).
        """
        acc = self._norm_author(getattr(r, "account", "") or "")
        if not acc:
            return False
        return author_norm in acc or acc in author_norm

    def _abs(self, href: str) -> str:
        if not href:
            return ""
        if href.startswith("//"):
            return "https:" + href
        if href.startswith("/"):
            return WEIXIN_HOME + href
        return href

    def _text(self, box, selectors: list[str]) -> str:
        for sel in selectors:
            try:
                el = box.query_selector(sel)
            except Exception:
                el = None
            if not el:
                continue
            try:
                txt = (el.inner_text() or "").strip()
            except Exception:
                txt = ""
            if txt:
                return txt
        return ""

    @staticmethod
    def _normalize_date(text: str) -> str:
        """Best-effort ISO date from Sogou's publish-time string."""
        if not text:
            return ""
        m = re.search(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})", text)
        if m:
            y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
            return f"{y}-{mo:02d}-{d:02d}"
        return text.strip()

    def _is_blocked(self) -> bool:
        try:
            url = (self.page.url or "").lower()
        except Exception:
            url = ""
        try:
            title = self.page.title() or ""
        except Exception:
            title = ""
        try:
            body = self.page.inner_text("body") or ""
        except Exception:
            body = ""
        try:
            html = self.page.content() or ""
        except Exception:
            html = ""

        self.last_status = {
            "url": url,
            "title": title,
            "body_len": len(body),
        }

        for frag in BLOCK_URL_FRAGMENTS:
            if frag in url:
                self.last_status["block_reason"] = f"url:{frag}"
                log.warning("[weixin] block url fragment: %r", frag)
                return True
        haystack = (body + html).lower()
        for marker in BLOCK_BODY_MARKERS:
            if marker.lower() in haystack:
                self.last_status["block_reason"] = marker
                log.warning("[weixin] block marker: %r", marker)
                return True
        return False

    def _human_hints(self):
        try:
            self.page.mouse.move(
                random.randint(100, 400), random.randint(100, 400), steps=10
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
