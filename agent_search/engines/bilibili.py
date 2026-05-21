"""Bilibili (哔哩哔哩) search adapter.

Bilibili exposes a public web search at::

    https://search.bilibili.com/all?keyword=<query>

The page is a heavy Vue/SPA — the URL is set early but the actual result
list (`.bili-video-card` / `.video-list-item`) is hydrated client-side
after a few hundred ms of JS work and a couple of XHR calls. Anti-bot
behaviour we have to handle:

1. **GeeTest / 412 risk control**. When the fingerprint or rate looks
   off, Bilibili returns HTTP 412 with a "请求被风控系统拦截" page or
   inserts a GeeTest puzzle into the SERP. CloakBrowser bypasses most
   of this, but we still need to detect the fail-state and bail.
2. **Login modal**. Occasionally a "登录" lightbox appears over the
   results (especially on cold cookies). It dims the page but does not
   actually block extraction; we still try to dismiss it because it
   sometimes pauses lazy-loading.
3. **Layout drift**. Bilibili rotates between two card components:
       - newer (2023+) ``.bili-video-card`` web component
       - older          ``.video-list-item`` / ``.video.matrix``
   We try both, in priority order, and aggregate matches.
4. **Numeric formatting**. Play / danmaku counts are localised:
       "1.2万" → 12,000      "3,456" → 3456     "8.7亿" → 870,000,000
   These are parsed into ``int``\\s; the original string is kept as
   ``play_count_text`` / ``danmaku_count_text``.

Each :class:`SearchResult` has the following structured extension
fields attached:

* ``author``            – uploader display name (UP主)
* ``author_url``        – absolute URL to the uploader's space
* ``play_count``        – integer play count (parsed)
* ``play_count_text``   – original "1.2万" string
* ``danmaku_count``     – integer danmaku/弹幕 count (parsed)
* ``danmaku_count_text`` – original string
* ``duration``          – integer seconds, parsed from "12:34" / "1:02:33"
* ``duration_text``     – original "12:34" string
* ``upload_date``       – original "2 days ago" / "2024-01-05" string (best effort)
* ``bvid``              – Bilibili BV id parsed from the URL (e.g. ``BV1xx411c7mD``)
* ``thumbnail``         – URL of the cover image

Diagnostics surface on ``engine.last_status`` (url / title / body_len /
block_reason / selector / count) just like the other Chinese-site
adapters (baidu / sogou / so360).
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


BILIBILI_HOME = "https://www.bilibili.com"
SEARCH_URL = "https://search.bilibili.com/all?keyword={q}"


# Result-card selectors in priority order. Bilibili rotates between
# the newer web-component card and the older list-item layout; we
# aggregate matches across all of them so the search keeps working
# during A/B experiments.
RESULT_SELECTORS = [
    ".bili-video-card",                       # 2023+ web component
    ".video-list-item",                       # 2022 layout
    ".video.matrix",                          # legacy desktop
    ".search-page .video-list .video-item",   # fallback
]

# Title link selectors evaluated *inside* a result row.
TITLE_LINK_SELECTORS = [
    ".bili-video-card__info--tit a",
    "a.bili-video-card__info--tit",
    ".bili-video-card__info--right > a",
    "a.title",
    "a.titletext",
    "h3.bili-video-card__info--tit a",
    ".info > a.title",
    "a[href*='/video/BV']",                   # last-resort
]

# Title text selectors (sometimes the <a> is wrapped around an inner
# h3 / span that holds the actual full title with `title=` attribute).
TITLE_TEXT_SELECTORS = [
    ".bili-video-card__info--tit",
    "h3.bili-video-card__info--tit",
    "a.title",
    "a.titletext",
]

# Author / UP主 selectors.
AUTHOR_SELECTORS = [
    ".bili-video-card__info--author",
    ".bili-video-card__info--owner span.bili-video-card__info--author",
    ".bili-video-card__info--owner",
    "a.up-name",
    "span.up-name",
    ".upname a",
    ".upname",
]

AUTHOR_LINK_SELECTORS = [
    "a.bili-video-card__info--owner",
    ".bili-video-card__info--owner a",
    "a.up-name",
    ".upname a",
]

# Stats area: play count + danmaku count appear as adjacent spans.
STATS_CONTAINER_SELECTORS = [
    ".bili-video-card__stats--left",
    ".bili-video-card__stats",
    ".tags",
    ".so-tags",
]
STATS_ITEM_SELECTORS = [
    ".bili-video-card__stats--item",
    "span.so-icon",
    "span.tag",
]

# Duration badge in the cover.
DURATION_SELECTORS = [
    ".bili-video-card__stats__duration",
    "span.so-imgTag_rb",
    ".length",
    ".so-imgTag_rb",
]

# Upload-date / "X days ago" — best effort; not every layout has it.
DATE_SELECTORS = [
    ".bili-video-card__info--date",
    "span.time",
    ".time",
]

# Thumbnail / cover image.
THUMBNAIL_SELECTORS = [
    ".bili-video-card__cover img",
    ".bili-video-card__image--img",
    "img.lazy-image",
    ".pic img",
    "img",
]


# Login / overlay buttons we may want to dismiss.
DISMISS_BUTTON_SELECTORS = [
    ".bili-mini-mask .bili-mini-close-icon",
    ".bili-mini-close-icon",
    ".close-btn",
    ".van-popup__close-icon",
    ".login-tip-mini .close",
    "button:has-text('稍后')",
    "button:has-text('暂不登录')",
    "[aria-label='close' i]",
    "[aria-label='关闭']",
]


# Block / risk-control indicators.
BLOCK_URL_FRAGMENTS = (
    "passport.bilibili.com/login",
    "passport.bilibili.com/register",
    "geetest",
    "/risk",
)

BLOCK_PHRASES = (
    "请求被风控系统拦截",
    "请完成下方验证",
    "拒绝访问",
    "访问异常",
    "您的请求过于频繁",
    "页面不存在",
    "captcha",
    "verify you are human",
)


# ---------------------------------------------------------------- helpers

_BV_RE = re.compile(r"/video/(BV[0-9A-Za-z]{10})")
_LEADING_SEP_RE = re.compile(r"^[\s\u00b7\u2022·•,\-]+")

# Bilibili occasionally renders cards whose title element has no real
# title text and instead leaks the stats line ("43.2万 98 00:32") into
# the same DOM node we'd scrape. We use this matcher in two places:
#   1) to *trigger* extra fallback title sources (img alt, aria-label)
#   2) to *reject* a card outright if no fallback gave us a real title.
_STATS_BLOB_RE = re.compile(
    r"^[\d\.,]+[万亿wWkKmM]?\s+[\d\.,]+[万亿wWkKmM]?\s+\d{1,3}:\d{2}(?::\d{2})?$"
)


def _is_stats_blob(text: str) -> bool:
    """True if `text` is just "<count> <count> <duration>" with no real title."""
    if not text:
        return False
    return bool(_STATS_BLOB_RE.fullmatch(text.strip()))


def _abs_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://www.bilibili.com" + href
    return href


def _bvid_from_url(url: str) -> str:
    if not url:
        return ""
    m = _BV_RE.search(url)
    return m.group(1) if m else ""


def _parse_count(text: str) -> int | None:
    """Parse Bilibili's localised counts.

    Examples
    --------
    "1.2万"      → 12000
    "3,456"      → 3456
    "8.7亿"      → 870_000_000
    "12.5w"      → 125000      (some old layouts)
    "5000"       → 5000
    "--" / ""    → None
    """
    if not text:
        return None
    t = text.strip()
    if not t or t in ("--", "—", "-"):
        return None
    # Strip any non-digit prefix (e.g. play-count icon glyphs leaked into text).
    t = re.sub(r"^[^\d]+", "", t)
    if not t:
        return None

    m = re.match(r"([\d,\.]+)\s*([万亿wWkKmM]?)", t)
    if not m:
        return None
    raw, suffix = m.group(1), m.group(2)
    try:
        n = float(raw.replace(",", ""))
    except ValueError:
        return None
    mult_map = {
        "": 1,
        "万": 10_000,
        "w": 10_000,
        "W": 10_000,
        "亿": 100_000_000,
        "k": 1_000,
        "K": 1_000,
        "m": 1_000_000,
        "M": 1_000_000,
    }
    return int(n * mult_map.get(suffix, 1))


def _parse_duration(text: str) -> int | None:
    """Parse "M:SS" / "H:MM:SS" / "MM:SS" into total seconds."""
    if not text:
        return None
    t = text.strip()
    m = re.fullmatch(r"\s*(\d{1,3}):(\d{2})(?::(\d{2}))?\s*", t)
    if not m:
        return None
    a = int(m.group(1))
    b = int(m.group(2))
    c = m.group(3)
    if c is not None:
        return a * 3600 + b * 60 + int(c)
    return a * 60 + b


def _clean_inline(s: str) -> str:
    """Collapse whitespace and trim leading separator chars."""
    if not s:
        return ""
    s = re.sub(r"\s+", " ", s).strip()
    s = _LEADING_SEP_RE.sub("", s).strip()
    return s


# ---------------------------------------------------------------- engine


class BilibiliEngine(BaseEngine):
    """Bilibili search adapter (https://search.bilibili.com/all)."""

    name = "bilibili"
    max_retries = 3

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}

    # ------------------------------------------------------------ main flow

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # 1) Warm-up the homepage so cookies (buvid3, b_nut, sid …) settle
        #    before we hit the search endpoint. Skipping this routinely
        #    makes the first search redirect to login or 412.
        if safe_goto(self.page, BILIBILI_HOME + "/", timeout=20000, retries=1):
            human_delay(1.0, 2.2)
            self._dismiss_overlays()
            self._human_hints()

        # 2) Issue the actual search.
        url = SEARCH_URL.format(q=urllib.parse.quote(query))
        log.info("[bilibili] navigating to %s", url)
        if not safe_goto(self.page, url, timeout=30000):
            return []

        human_delay(1.5, 3.0)
        self._dismiss_overlays()
        self._human_hints()

        if self._is_blocked():
            return []

        # 3) Wait for the SPA to hydrate at least one result card. Bilibili
        #    typically takes 800-1500ms after domcontentloaded for the
        #    cards to attach.
        if not self._wait_for_results(timeout_ms=15000):
            log.info("[bilibili] no result cards after wait; trying anyway")

        # Trigger lazy-loading by scrolling so play/danmaku spans render.
        self._human_hints()
        self._dismiss_overlays()

        results = self._extract_results(limit)
        self.last_status["count"] = len(results)
        return results

    # ------------------------------------------------------------ utilities

    def _wait_for_results(self, timeout_ms: int = 15000) -> bool:
        deadline = time.time() + timeout_ms / 1000.0
        for sel in RESULT_SELECTORS:
            try:
                self.page.wait_for_selector(sel, timeout=2000)
                return True
            except Exception:
                continue
        # Fallback: poll loop.
        while time.time() < deadline:
            for sel in RESULT_SELECTORS:
                try:
                    if self.page.query_selector(sel) is not None:
                        return True
                except Exception:
                    continue
            time.sleep(0.5)
        return False

    def _dismiss_overlays(self):
        for sel in DISMISS_BUTTON_SELECTORS:
            try:
                btn = self.page.query_selector(sel)
                if btn:
                    btn.click(timeout=2000)
                    log.info("[bilibili] dismissed overlay (%s)", sel)
                    human_delay(0.4, 1.0)
            except Exception:
                continue

    def _is_blocked(self) -> bool:
        try:
            url = (self.page.url or "").lower()
        except Exception:
            url = ""
        try:
            title = (self.page.title() or "")
        except Exception:
            title = ""
        try:
            body = self.page.inner_text("body") or ""
        except Exception:
            body = ""

        self.last_status = {
            "url": url,
            "title": title,
            "body_len": len(body),
        }

        for frag in BLOCK_URL_FRAGMENTS:
            if frag in url:
                log.warning("[bilibili] block url fragment: %r", frag)
                self.last_status["block_reason"] = f"url:{frag}"
                return True

        body_lc = body.lower()
        title_lc = title.lower()
        for phrase in BLOCK_PHRASES:
            p = phrase.lower()
            if p in body_lc or p in title_lc:
                log.warning("[bilibili] block phrase: %r", phrase)
                self.last_status["block_reason"] = phrase
                return True

        return False

    def _human_hints(self):
        try:
            self.page.mouse.move(
                random.randint(100, 500),
                random.randint(100, 400),
                steps=10,
            )
        except Exception:
            pass
        try:
            self.page.evaluate(
                "() => window.scrollBy(0, Math.floor(Math.random()*500) + 200)"
            )
        except Exception:
            pass
        time.sleep(random.uniform(0.4, 0.9))

    def selector_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for sel in RESULT_SELECTORS:
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        return counts

    # ------------------------------------------------------------ extraction

    def _extract_results(self, limit: int) -> list[SearchResult]:
        # Walk the result containers in priority order and accumulate
        # rows from each tier. Bilibili occasionally renders results in
        # multiple tiers (recommended at top, search results below); we
        # de-duplicate by URL/BV id later.
        all_items: list[tuple[str, object]] = []
        seen_handles: set[int] = set()
        used_selectors: list[str] = []
        for sel in RESULT_SELECTORS:
            try:
                rows = self.page.query_selector_all(sel)
            except Exception:
                rows = []
            if not rows:
                continue
            new_rows = 0
            for row in rows:
                key = id(row)
                if key in seen_handles:
                    continue
                seen_handles.add(key)
                all_items.append((sel, row))
                new_rows += 1
            if new_rows:
                used_selectors.append(f"{sel}({new_rows})")
            if len(all_items) >= limit * 4:
                break

        if not all_items:
            log.info("[bilibili] no result selector matched")
            self.last_status["selector"] = None
            return []

        log.info(
            "[bilibili] using selectors %s (%d rows total)",
            ", ".join(used_selectors), len(all_items),
        )
        self.last_status["selector"] = ", ".join(used_selectors)

        results: list[SearchResult] = []
        seen_urls: set[str] = set()

        for _sel, r in all_items:
            try:
                # ---- Title + URL.
                # Try every title-link selector; prefer the candidate
                # whose ``title=`` attribute is set, which is the
                # canonical full title and avoids inline-player text
                # leaking in via ``inner_text()``.
                best_el = None
                best_text = ""
                best_href = ""
                best_from_attr = False
                for sel in TITLE_LINK_SELECTORS:
                    try:
                        cand = r.query_selector(sel)
                    except Exception:
                        cand = None
                    if not cand:
                        continue
                    try:
                        cand_href = cand.get_attribute("href") or ""
                    except Exception:
                        cand_href = ""
                    if not cand_href:
                        continue
                    try:
                        attr_title = (cand.get_attribute("title") or "").strip()
                    except Exception:
                        attr_title = ""
                    try:
                        inner = (cand.inner_text() or "").strip()
                    except Exception:
                        inner = ""
                    cand_text = attr_title or inner
                    if not cand_text:
                        continue
                    take = False
                    if best_el is None:
                        take = True
                    elif attr_title and not best_from_attr:
                        take = True
                    elif (bool(attr_title) == best_from_attr) and len(cand_text) > len(best_text):
                        take = True
                    if take:
                        best_el = cand
                        best_text = cand_text
                        best_href = cand_href
                        best_from_attr = bool(attr_title)
                    if best_from_attr and len(best_text) > 8:
                        break

                if not best_el or not best_href:
                    continue

                # Always prefer the title-text container's ``title=``
                # attribute when available — Bilibili stores the
                # canonical full title there. ``inner_text`` on the
                # link can leak the inline player UI ("00:00 / 00:32",
                # play count badges) when a card has a preview open.
                title = ""
                for sel in TITLE_TEXT_SELECTORS:
                    try:
                        t_el = r.query_selector(sel)
                    except Exception:
                        t_el = None
                    if not t_el:
                        continue
                    try:
                        t_attr = (t_el.get_attribute("title") or "").strip()
                    except Exception:
                        t_attr = ""
                    if t_attr:
                        title = t_attr
                        break

                if not title and best_from_attr:
                    title = best_text

                title = _clean_inline(title)

                # If we still have nothing usable (or the candidate
                # looks like a stats blob — "数字万 数字 数字:数字"),
                # fall back to the title-text container's ``inner_text``
                # then to the link's ``inner_text``.
                if not title or _is_stats_blob(title):
                    for sel in TITLE_TEXT_SELECTORS:
                        try:
                            t_el = r.query_selector(sel)
                        except Exception:
                            t_el = None
                        if not t_el:
                            continue
                        try:
                            t = (
                                t_el.get_attribute("title")
                                or t_el.inner_text()
                                or ""
                            ).strip()
                        except Exception:
                            t = ""
                        if t and not _is_stats_blob(t):
                            title = _clean_inline(t)
                            break

                # Last-resort fallbacks for cards whose ``.bili-video-card__info--tit``
                # element only contains stats (some short / "story" cards)
                # — pull the canonical title from:
                #   • the cover image's ``alt`` attribute
                #   • the title-link's ``aria-label`` attribute
                if not title or _is_stats_blob(title):
                    for sel in THUMBNAIL_SELECTORS:
                        try:
                            img_el = r.query_selector(sel)
                        except Exception:
                            img_el = None
                        if not img_el:
                            continue
                        try:
                            alt_text = (img_el.get_attribute("alt") or "").strip()
                        except Exception:
                            alt_text = ""
                        if alt_text and not _is_stats_blob(alt_text):
                            title = _clean_inline(alt_text)
                            break

                if not title or _is_stats_blob(title):
                    for sel in TITLE_LINK_SELECTORS:
                        try:
                            cand2 = r.query_selector(sel)
                        except Exception:
                            cand2 = None
                        if not cand2:
                            continue
                        try:
                            aria = (cand2.get_attribute("aria-label") or "").strip()
                        except Exception:
                            aria = ""
                        if aria and not _is_stats_blob(aria):
                            title = _clean_inline(aria)
                            break

                # If we *still* couldn't find a real title, the card has
                # no usable text (likely a "故事/短视频" stub or an ad
                # placeholder) — drop it so the caller never sees a
                # stats-blob masquerading as a title.
                if not title or _is_stats_blob(title):
                    log.debug(
                        "[bilibili] skipping card with stats-only title %r (%s)",
                        title, best_href,
                    )
                    continue

                video_url = _abs_url(best_href)
                # Filter out non-video results (live rooms, articles,
                # users) — keep this simple: require a /video/BV path.
                if "/video/BV" not in video_url:
                    continue

                # De-dup by URL (strip query string).
                key_url = video_url.split("?", 1)[0].rstrip("/")
                if key_url in seen_urls:
                    continue
                seen_urls.add(key_url)

                bvid = _bvid_from_url(video_url)

                # ---- Author / UP主.
                author = ""
                for sel in AUTHOR_SELECTORS:
                    try:
                        a_el = r.query_selector(sel)
                    except Exception:
                        a_el = None
                    if not a_el:
                        continue
                    try:
                        text = (
                            a_el.get_attribute("title")
                            or a_el.inner_text()
                            or ""
                        ).strip()
                    except Exception:
                        text = ""
                    if text:
                        author = _clean_inline(text.splitlines()[0])
                        break

                author_url = ""
                for sel in AUTHOR_LINK_SELECTORS:
                    try:
                        link_el = r.query_selector(sel)
                    except Exception:
                        link_el = None
                    if not link_el:
                        continue
                    try:
                        href2 = link_el.get_attribute("href") or ""
                    except Exception:
                        href2 = ""
                    if href2 and "space.bilibili.com" in href2:
                        author_url = _abs_url(href2)
                        break
                    if href2 and href2.startswith(("/", "http")):
                        # don't break — prefer space.bilibili.com if seen later
                        author_url = _abs_url(href2)

                # ---- Stats: play count + danmaku count.
                play_count_text = ""
                danmaku_count_text = ""

                stat_spans: list = []
                for cont_sel in STATS_CONTAINER_SELECTORS:
                    try:
                        cont = r.query_selector(cont_sel)
                    except Exception:
                        cont = None
                    if not cont:
                        continue
                    for item_sel in STATS_ITEM_SELECTORS:
                        try:
                            spans = cont.query_selector_all(item_sel)
                        except Exception:
                            spans = []
                        if spans:
                            stat_spans = list(spans)
                            break
                    if stat_spans:
                        break

                if not stat_spans:
                    for item_sel in STATS_ITEM_SELECTORS:
                        try:
                            spans = r.query_selector_all(item_sel)
                        except Exception:
                            spans = []
                        if spans:
                            stat_spans = list(spans)
                            break

                stat_texts: list[str] = []
                for span in stat_spans:
                    try:
                        aria = span.get_attribute("aria-label") or ""
                    except Exception:
                        aria = ""
                    try:
                        text = (span.inner_text() or "").strip()
                    except Exception:
                        text = ""
                    if aria:
                        stat_texts.append(aria.strip())
                    if text:
                        stat_texts.append(text)

                for s in stat_texts:
                    s_low = s.lower()
                    if not play_count_text and ("播放" in s or "次" in s_low or "view" in s_low):
                        play_count_text = s
                    elif not danmaku_count_text and ("弹幕" in s or "danmaku" in s_low):
                        danmaku_count_text = s

                if not play_count_text and stat_texts:
                    play_count_text = stat_texts[0]
                if not danmaku_count_text and len(stat_texts) > 1:
                    danmaku_count_text = stat_texts[1]

                play_count = _parse_count(play_count_text)
                danmaku_count = _parse_count(danmaku_count_text)

                # ---- Duration.
                duration_text = ""
                for sel in DURATION_SELECTORS:
                    try:
                        d_el = r.query_selector(sel)
                    except Exception:
                        d_el = None
                    if not d_el:
                        continue
                    try:
                        text = (d_el.inner_text() or "").strip()
                    except Exception:
                        text = ""
                    if text:
                        duration_text = _clean_inline(text.splitlines()[0])
                        break
                duration = _parse_duration(duration_text)

                # ---- Date.
                upload_date = ""
                for sel in DATE_SELECTORS:
                    try:
                        d_el = r.query_selector(sel)
                    except Exception:
                        d_el = None
                    if not d_el:
                        continue
                    try:
                        text = (d_el.inner_text() or "").strip()
                    except Exception:
                        text = ""
                    if text:
                        upload_date = _clean_inline(text.splitlines()[0])
                        break

                # ---- Thumbnail.
                thumbnail = ""
                for sel in THUMBNAIL_SELECTORS:
                    try:
                        img_el = r.query_selector(sel)
                    except Exception:
                        img_el = None
                    if not img_el:
                        continue
                    src = (
                        img_el.get_attribute("src")
                        or img_el.get_attribute("data-src")
                        or ""
                    )
                    if src:
                        thumbnail = _abs_url(src)
                        break

                # ---- Compose snippet so callers without the extension
                #      attributes still see the metadata.
                head_bits: list[str] = []
                if author:
                    head_bits.append(f"UP: {author}")
                if play_count_text:
                    head_bits.append(f"播放 {play_count_text}")
                if danmaku_count_text:
                    head_bits.append(f"弹幕 {danmaku_count_text}")
                if duration_text:
                    head_bits.append(duration_text)
                if upload_date:
                    head_bits.append(upload_date)
                snippet = " · ".join(head_bits)

                sr = SearchResult(title=title, url=video_url, snippet=snippet)
                sr.author = author                              # type: ignore[attr-defined]
                sr.author_url = author_url                      # type: ignore[attr-defined]
                sr.play_count = play_count                      # type: ignore[attr-defined]
                sr.play_count_text = play_count_text            # type: ignore[attr-defined]
                sr.danmaku_count = danmaku_count                # type: ignore[attr-defined]
                sr.danmaku_count_text = danmaku_count_text      # type: ignore[attr-defined]
                sr.duration = duration                          # type: ignore[attr-defined]
                sr.duration_text = duration_text                # type: ignore[attr-defined]
                sr.upload_date = upload_date                    # type: ignore[attr-defined]
                sr.bvid = bvid                                  # type: ignore[attr-defined]
                sr.thumbnail = thumbnail                        # type: ignore[attr-defined]

                results.append(sr)
                if len(results) >= limit:
                    break
            except Exception as e:
                log.debug("[bilibili] failed to parse one row: %s", e)
                continue

        return results
