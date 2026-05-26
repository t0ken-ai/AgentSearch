"""YouTube search adapter.

YouTube returns video search results from the public web search endpoint:

    https://www.youtube.com/results?search_query=<query>

Quirks we have to handle:

1. **EU consent.youtube.com redirect**. First-time visitors from EU geos and
   many headless browsers get redirected to ``consent.youtube.com`` and have
   to accept (or reject) cookies before the search page renders. The consent
   page can also appear inline as a modal with ``role="dialog"``.
2. **Age verification interstitials**. Some videos and search refinements
   render an "Sign in to confirm your age" gate. We don't sign in, so we
   skip those entries instead of failing the whole search.
3. **JS-heavy SPA**. Result cards are rendered client-side as
   ``ytd-video-renderer`` web components inside ``ytd-search`` /
   ``ytd-section-list-renderer``. They take a moment to settle, so we wait
   for the renderer before extraction.
4. **Backup data path**. If the DOM has not finished hydrating, the same
   data is embedded in ``ytInitialData`` on the HTML page; we parse that
   JSON as a fallback.

Each :class:`SearchResult` carries the structured fields callers care about
on attached attributes (so they don't have to re-parse the snippet):

* ``channel``       – channel display name (e.g. "Tech With Tim")
* ``channel_url``   – absolute URL to the channel
* ``views``         – integer view count, parsed from "1.2M views" / "543 views"
* ``views_text``    – original "X views" string (kept for display)
* ``duration``      – integer seconds, parsed from "12:34" / "1:02:33"
* ``duration_text`` – original "12:34" string (kept for display)
* ``upload_date``   – original "Streamed 2 years ago" / "3 days ago" string
* ``video_id``      – YouTube video id (the ``v=`` query parameter)
* ``thumbnail``     – URL of the thumbnail image
"""

from __future__ import annotations

import json
import logging
import random
import re
import time
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)


# Selectors used to find a result card in the rendered DOM, in priority order.
RESULT_SELECTORS = [
    "ytd-video-renderer",
    "ytd-search ytd-video-renderer",
    "ytd-section-list-renderer ytd-video-renderer",
]

# Consent dialog buttons (consent.youtube.com landing page + inline modal).
CONSENT_BUTTON_SELECTORS = [
    # consent.youtube.com landing page — the form actually submits the choice.
    "form[action*='consent'] button",
    "button[aria-label*='Accept all' i]",
    "button[aria-label*='Accept the use' i]",
    "button[aria-label*='Reject all' i]",
    "button[aria-label*='Agree' i]",
    "button[aria-label*='Akzeptieren' i]",
    "button[aria-label*='Accepter' i]",
    "button[aria-label*='Aceptar' i]",
    # Inline modal (some locales).
    "tp-yt-paper-dialog button",
    "ytd-consent-bump-v2-lightbox button",
    "div[role='dialog'] button",
]

# Phrases that indicate YouTube is blocking us / forcing sign-in.
BLOCK_PHRASES = [
    "before you continue to youtube",
    "before you continue",
    "sign in to confirm you",
    "we want to make sure",
    "verify it's you",
    "this content isn",
    "video unavailable",
    "automated queries",
    "unusual traffic",
]


# ---------------------------------------------------------------- helpers


def _parse_views(text: str) -> int | None:
    """Parse '1.2M views' / '543 views' / 'No views' / '12K views' into int.

    Returns ``None`` when the text doesn't look like a view count.
    """
    if not text:
        return None
    t = text.strip().lower()
    if "no view" in t:
        return 0

    m = re.search(r"([\d,]+\.?\d*)\s*([kmb]?)\s*view", t)
    if not m:
        return None
    raw, suffix = m.group(1), m.group(2)
    try:
        n = float(raw.replace(",", ""))
    except ValueError:
        return None
    mult = {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(suffix, 1)
    return int(n * mult)


def _parse_duration(text: str) -> int | None:
    """Parse 'H:MM:SS' / 'M:SS' / 'SS' into total seconds.

    Also tolerates ARIA-label style strings like '12 minutes, 34 seconds'.
    Returns ``None`` for unparseable / live (no fixed duration) entries.
    """
    if not text:
        return None
    t = text.strip().lower()

    # Plain "H:MM:SS" / "M:SS" / "SS".
    m = re.fullmatch(r"\s*(\d{1,2}:)?(\d{1,2}):(\d{2})\s*", text.strip())
    if m:
        h = int(m.group(1)[:-1]) if m.group(1) else 0
        return h * 3600 + int(m.group(2)) * 60 + int(m.group(3))

    # Bare seconds.
    if re.fullmatch(r"\s*\d+\s*", t):
        try:
            return int(t)
        except ValueError:
            return None

    # ARIA label: "1 hour, 2 minutes, 33 seconds" / "12 minutes, 34 seconds".
    h = re.search(r"(\d+)\s*hour", t)
    mn = re.search(r"(\d+)\s*minute", t)
    s = re.search(r"(\d+)\s*second", t)
    if h or mn or s:
        return (
            (int(h.group(1)) if h else 0) * 3600
            + (int(mn.group(1)) if mn else 0) * 60
            + (int(s.group(1)) if s else 0)
        )

    return None


def _video_id_from_url(url: str) -> str:
    """Extract the ``v=`` parameter from a watch URL, returning '' on miss."""
    if not url:
        return ""
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return ""
    if parsed.path == "/watch":
        return urllib.parse.parse_qs(parsed.query).get("v", [""])[0] or ""
    if parsed.netloc.endswith("youtu.be"):
        return parsed.path.lstrip("/")
    return ""


def _abs_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://www.youtube.com" + href
    return href


# ---------------------------------------------------------------- engine


class YouTubeEngine(BaseEngine):
    """Search YouTube via the public ``/results`` page."""

    name = "youtube"
    max_retries = 3

    SEARCH_URL = "https://www.youtube.com/results?search_query={q}"
    HOMEPAGE_URL = "https://www.youtube.com/"

    def __init__(self, page):
        super().__init__(page)
        # Diagnostics surface for callers / tests.
        self.last_status: dict = {}

    # ------------------------------------------------------------ public API

    def search(  # type: ignore[override]
        self,
        query: str,
        limit: int = 10,
        *,
        mode: str = "search",
        lang: str = "en",
    ) -> list[SearchResult]:
        """Run a YouTube query.

        Modes:
          * ``"search"`` (default): keep the legacy ``/results?search_query=``
            behaviour — returns a list of video results from the SERP.
          * ``"video"``: treat ``query`` as a watch URL or 11-char video ID,
            visit the watch page, parse ``ytInitialPlayerResponse`` +
            ``ytInitialData`` for full video metadata.
          * ``"channel"``: treat ``query`` as a channel handle (``@name``),
            channel ID (``UC...``), or channel URL; return basic channel
            metadata + the recent video grid (visits ``/videos`` so the
            grid is server-rendered into ``ytInitialData``).
          * ``"transcript"``: like ``"video"`` but additionally fetches the
            ``captions.baseUrl`` XML and attaches a ``transcript`` field.
            ``lang`` selects the caption track language code (default
            ``"en"``); falls back to the first track if the language is
            missing. NB: many videos now require a ``pot=`` token from the
            player binary that we can't sign for, so the response may be
            empty — the per-track URL is still returned in ``captions``.
        """
        self.last_status = {"mode_requested": mode}
        m = (mode or "search").lower()
        if m == "video":
            return self._mode_video(query)
        if m == "channel":
            return self._mode_channel(query, limit)
        if m == "transcript":
            return self._mode_transcript(query, lang)
        return super().search(query, limit)

    # ------------------------------------------------------------ main flow

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # 1) Warm-up the homepage so consent / cookies settle before search.
        if safe_goto(self.page, self.HOMEPAGE_URL, timeout=20000, retries=1):
            human_delay(1.0, 2.5)
            self._handle_consent()
            self._human_hints()

        # 2) Issue the actual search.
        url = self.SEARCH_URL.format(q=urllib.parse.quote(query))
        log.info("[youtube] navigating to %s", url)
        if not safe_goto(self.page, url, timeout=30000):
            return []

        # 3) Sometimes the search itself bounces through consent.
        human_delay(1.5, 3.0)
        if "consent" in (self.page.url or "").lower():
            self._handle_consent()
            human_delay(1.0, 2.0)
            # After accepting consent we usually need to re-issue the search.
            if "results" not in (self.page.url or ""):
                safe_goto(self.page, url, timeout=30000)
                human_delay(1.5, 3.0)

        self._handle_consent()
        self._human_hints()

        if self._is_blocked():
            return []

        # 4) Wait for the result renderer to attach (SPA hydration).
        if not self._wait_for_results(timeout_ms=10000):
            log.info("[youtube] no DOM result selector; falling back to ytInitialData")
            results = self._extract_from_initial_data(limit)
            if results:
                return results
            return []

        results = self._extract_from_dom(limit)
        if results:
            return results

        # 5) Last-resort: try the embedded JSON.
        log.info("[youtube] DOM extraction yielded 0 results; trying ytInitialData")
        return self._extract_from_initial_data(limit)

    # ------------------------------------------------------------ utilities

    def _handle_consent(self):
        """Click consent / cookie acceptance, including consent.youtube.com."""
        # 1) Top-level frame buttons.
        for sel in CONSENT_BUTTON_SELECTORS:
            try:
                btn = self.page.query_selector(sel)
                if btn:
                    btn.click(timeout=3000)
                    log.info("[youtube] clicked consent (%s)", sel)
                    human_delay(1, 2)
                    return
            except Exception:
                continue

        # 2) consent.youtube.com sometimes loads inside a frame.
        try:
            for frame in self.page.frames:
                furl = (frame.url or "").lower()
                if "consent" not in furl:
                    continue
                for sel in CONSENT_BUTTON_SELECTORS:
                    try:
                        btn = frame.query_selector(sel)
                        if btn:
                            btn.click(timeout=3000)
                            log.info(
                                "[youtube] clicked consent inside frame %s (%s)",
                                furl, sel,
                            )
                            human_delay(1, 2)
                            return
                    except Exception:
                        continue
        except Exception:
            pass

    def _is_blocked(self) -> bool:
        """Detect block / consent-loop / sign-in interstitials."""
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
            "url": url,
            "title": title,
            "body_len": len(body),
        }

        # Stuck on consent page after our handler ran.
        if "consent.youtube.com" in url or "consent.google.com" in url:
            log.warning("[youtube] still on consent page: %s", url)
            self.last_status["block_reason"] = "consent_loop"
            return True

        # Hard sign-in walls.
        if "accounts.google.com" in url and "signin" in url:
            log.warning("[youtube] redirected to sign-in: %s", url)
            self.last_status["block_reason"] = "signin_required"
            return True

        for phrase in BLOCK_PHRASES:
            if phrase in body:
                # "before you continue" is also normal consent text — only
                # treat it as a block when we're on a non-results page.
                if phrase == "before you continue" and "results" in url:
                    continue
                log.warning("[youtube] block phrase: %r", phrase)
                self.last_status["block_reason"] = phrase
                return True

        return False

    def _human_hints(self):
        """Small mouse / scroll movement so the page commits to hydrating."""
        try:
            self.page.mouse.move(
                random.randint(100, 400),
                random.randint(100, 400),
                steps=8,
            )
        except Exception:
            pass
        try:
            self.page.evaluate(
                "() => window.scrollBy(0, Math.floor(Math.random()*500) + 200)"
            )
        except Exception:
            pass
        time.sleep(random.uniform(0.4, 1.0))

    def _wait_for_results(self, timeout_ms: int = 10000) -> bool:
        """Wait for at least one ytd-video-renderer to attach to the DOM."""
        deadline = time.time() + timeout_ms / 1000.0
        sel = RESULT_SELECTORS[0]
        # Try the page-native waiter first (faster path).
        try:
            self.page.wait_for_selector(sel, timeout=timeout_ms)
            return True
        except Exception:
            pass
        # Manual poll fallback.
        while time.time() < deadline:
            try:
                if self.page.query_selector(sel) is not None:
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

    def selector_counts(self) -> dict[str, int]:
        """Per-selector match counts on the current page (for diagnostics)."""
        counts: dict[str, int] = {}
        for sel in RESULT_SELECTORS:
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        return counts

    # ------------------------------------------------------------ extraction

    def _extract_from_dom(self, limit: int) -> list[SearchResult]:
        """Walk ``ytd-video-renderer`` cards and build SearchResults."""
        items = self.page.query_selector_all(RESULT_SELECTORS[0])
        log.info("[youtube] dom items: %d", len(items))
        results: list[SearchResult] = []

        for r in items:
            if len(results) >= limit:
                break
            try:
                # Title + watch URL.
                title_el = r.query_selector("a#video-title") or r.query_selector(
                    "#video-title-link"
                ) or r.query_selector("h3 a")
                if not title_el:
                    continue
                title = (
                    title_el.get_attribute("title")
                    or title_el.inner_text()
                    or ""
                ).strip()
                href = title_el.get_attribute("href") or ""
                video_url = _abs_url(href)
                if not (title and video_url and "/watch" in video_url):
                    continue

                # Channel name + URL.
                ch_el = (
                    r.query_selector("ytd-channel-name a")
                    or r.query_selector("#channel-name a")
                    or r.query_selector("ytd-channel-name yt-formatted-string")
                )
                channel = (ch_el.inner_text().strip() if ch_el else "")
                channel_url = ""
                if ch_el:
                    ch_href = ch_el.get_attribute("href") or ""
                    channel_url = _abs_url(ch_href)

                # Metadata line: ["1.2M views", "3 days ago"].
                meta_text_parts: list[str] = []
                for span in r.query_selector_all("#metadata-line span"):
                    try:
                        txt = span.inner_text().strip()
                    except Exception:
                        txt = ""
                    if txt:
                        meta_text_parts.append(txt)

                views_text = ""
                upload_date = ""
                for part in meta_text_parts:
                    low = part.lower()
                    if "view" in low and not views_text:
                        views_text = part
                    elif "ago" in low or "streamed" in low or "premiered" in low:
                        upload_date = part
                # Fallback: when there are exactly two parts, second is upload.
                if not upload_date and len(meta_text_parts) >= 2:
                    upload_date = meta_text_parts[-1]

                views = _parse_views(views_text)

                # Duration: shown in the thumbnail badge.
                dur_el = r.query_selector(
                    "ytd-thumbnail-overlay-time-status-renderer #text"
                ) or r.query_selector(
                    "ytd-thumbnail-overlay-time-status-renderer span"
                ) or r.query_selector(
                    "span.ytd-thumbnail-overlay-time-status-renderer"
                )
                duration_text = ""
                if dur_el:
                    try:
                        duration_text = dur_el.inner_text().strip()
                    except Exception:
                        duration_text = ""
                duration = _parse_duration(duration_text)

                # Snippet / description shown under the title.
                snippet_el = (
                    r.query_selector("#description-text")
                    or r.query_selector("yt-formatted-string#description-text")
                    or r.query_selector(".metadata-snippet-text")
                )
                snippet = ""
                if snippet_el:
                    try:
                        snippet = snippet_el.inner_text().strip()
                    except Exception:
                        snippet = ""

                # Thumbnail.
                thumb_el = r.query_selector("img")
                thumbnail = ""
                if thumb_el:
                    thumbnail = (
                        thumb_el.get_attribute("src")
                        or thumb_el.get_attribute("data-thumb")
                        or ""
                    )

                # Build a compact human-friendly snippet header.
                head_bits: list[str] = []
                if channel:
                    head_bits.append(channel)
                if views_text:
                    head_bits.append(views_text)
                if upload_date:
                    head_bits.append(upload_date)
                if duration_text:
                    head_bits.append(duration_text)
                head = " · ".join(head_bits)
                merged_snippet = (
                    " — ".join(b for b in (head, snippet) if b)
                    if head
                    else snippet
                )

                result = SearchResult(
                    title=title,
                    url=video_url,
                    snippet=merged_snippet,
                )
                # Structured extension fields — type: ignore for dataclass.
                result.channel = channel                       # type: ignore[attr-defined]
                result.channel_url = channel_url               # type: ignore[attr-defined]
                result.views = views                           # type: ignore[attr-defined]
                result.views_text = views_text                 # type: ignore[attr-defined]
                result.duration = duration                     # type: ignore[attr-defined]
                result.duration_text = duration_text           # type: ignore[attr-defined]
                result.upload_date = upload_date               # type: ignore[attr-defined]
                result.video_id = _video_id_from_url(video_url)  # type: ignore[attr-defined]
                result.thumbnail = thumbnail                   # type: ignore[attr-defined]
                results.append(result)
            except Exception as e:
                log.debug("[youtube] failed to parse one item: %s", e)
                continue

        return results

    # ------------------------------------------------ ytInitialData fallback

    def _extract_from_initial_data(self, limit: int) -> list[SearchResult]:
        """Pull results out of ``ytInitialData`` embedded in the page HTML.

        This survives DOM hydration failures, age-gated cards, and most
        layout experiments.
        """
        try:
            data = self.page.evaluate(
                """
                () => {
                  try { return window.ytInitialData || null; } catch (_) { return null; }
                }
                """
            )
        except Exception as e:
            log.warning("[youtube] cannot read ytInitialData via JS: %s", e)
            data = None

        if not data:
            # Fallback path: scrape the raw script block from the HTML body.
            try:
                html = self.page.content()
            except Exception:
                html = ""
            m = re.search(r"ytInitialData\s*=\s*({.+?});\s*</script>", html, re.S)
            if not m:
                m = re.search(r"var\s+ytInitialData\s*=\s*({.+?});", html, re.S)
            if m:
                try:
                    data = json.loads(m.group(1))
                except json.JSONDecodeError as e:
                    log.warning("[youtube] ytInitialData JSON parse failed: %s", e)
                    data = None

        if not data:
            return []

        contents = self._dig_search_contents(data)
        results: list[SearchResult] = []
        for entry in contents:
            if len(results) >= limit:
                break
            video = entry.get("videoRenderer") if isinstance(entry, dict) else None
            if not video:
                continue
            res = self._build_from_video_renderer(video)
            if res is not None:
                results.append(res)

        log.info("[youtube] ytInitialData yielded %d results", len(results))
        return results

    @staticmethod
    def _dig_search_contents(data: dict) -> list:
        """Dig out the list of ``itemSection`` contents from ytInitialData."""
        try:
            sections = (
                data["contents"]["twoColumnSearchResultsRenderer"]["primaryContents"][
                    "sectionListRenderer"
                ]["contents"]
            )
        except (KeyError, TypeError):
            return []

        out: list = []
        for section in sections or []:
            if not isinstance(section, dict):
                continue
            item_section = section.get("itemSectionRenderer")
            if not item_section:
                continue
            for c in item_section.get("contents") or []:
                out.append(c)
        return out

    @staticmethod
    def _runs_text(node) -> str:
        """Flatten YouTube's ``{runs: [{text}, ...]}`` / ``simpleText`` shape."""
        if not node:
            return ""
        if isinstance(node, str):
            return node
        if isinstance(node, dict):
            if "simpleText" in node and isinstance(node["simpleText"], str):
                return node["simpleText"]
            runs = node.get("runs") or []
            return "".join(
                r.get("text", "") for r in runs if isinstance(r, dict)
            )
        return ""

    @classmethod
    def _build_from_video_renderer(cls, video: dict) -> SearchResult | None:
        try:
            video_id = video.get("videoId") or ""
            if not video_id:
                return None
            title = cls._runs_text(video.get("title"))
            if not title:
                return None
            video_url = f"https://www.youtube.com/watch?v={video_id}"

            owner = video.get("ownerText") or video.get("longBylineText")
            channel = cls._runs_text(owner)
            channel_url = ""
            try:
                runs = (owner or {}).get("runs") or []
                for run in runs:
                    nav = run.get("navigationEndpoint", {})
                    cmd = nav.get("commandMetadata", {}).get("webCommandMetadata", {})
                    href = cmd.get("url")
                    if href:
                        channel_url = _abs_url(href)
                        break
            except Exception:
                pass

            views_text = cls._runs_text(video.get("viewCountText")) or cls._runs_text(
                video.get("shortViewCountText")
            )
            views = _parse_views(views_text)

            upload_date = cls._runs_text(video.get("publishedTimeText"))

            duration_text = cls._runs_text(video.get("lengthText"))
            duration = _parse_duration(duration_text)

            snippet = ""
            for sn_key in ("detailedMetadataSnippets", "descriptionSnippet"):
                sn = video.get(sn_key)
                if isinstance(sn, list) and sn:
                    snippet = cls._runs_text(sn[0].get("snippetText"))
                    if snippet:
                        break
                elif isinstance(sn, dict):
                    snippet = cls._runs_text(sn)
                    if snippet:
                        break

            thumbnail = ""
            try:
                thumbs = video.get("thumbnail", {}).get("thumbnails") or []
                if thumbs:
                    thumbnail = thumbs[-1].get("url", "")
            except Exception:
                pass

            head_bits: list[str] = []
            if channel:
                head_bits.append(channel)
            if views_text:
                head_bits.append(views_text)
            if upload_date:
                head_bits.append(upload_date)
            if duration_text:
                head_bits.append(duration_text)
            head = " · ".join(head_bits)
            merged_snippet = (
                " — ".join(b for b in (head, snippet) if b) if head else snippet
            )

            result = SearchResult(
                title=title,
                url=video_url,
                snippet=merged_snippet,
            )
            result.channel = channel                  # type: ignore[attr-defined]
            result.channel_url = channel_url          # type: ignore[attr-defined]
            result.views = views                      # type: ignore[attr-defined]
            result.views_text = views_text            # type: ignore[attr-defined]
            result.duration = duration                # type: ignore[attr-defined]
            result.duration_text = duration_text      # type: ignore[attr-defined]
            result.upload_date = upload_date          # type: ignore[attr-defined]
            result.video_id = video_id                # type: ignore[attr-defined]
            result.thumbnail = thumbnail              # type: ignore[attr-defined]
            return result
        except Exception as e:
            log.debug("[youtube] failed to build from videoRenderer: %s", e)
            return None


    # ============================================================
    # Mode dispatchers
    # ============================================================

    def _mode_video(self, query: str) -> list[SearchResult]:
        vid = self._normalise_video_id(query)
        if not vid:
            self.last_status["error"] = "invalid_video_id"
            return []
        data = self.fetch_video(vid)
        if not data:
            return []
        return [self._video_data_to_result(data)]

    def _mode_transcript(
        self, query: str, lang: str = "en",
    ) -> list[SearchResult]:
        vid = self._normalise_video_id(query)
        if not vid:
            self.last_status["error"] = "invalid_video_id"
            return []
        data = self.fetch_video(vid, with_transcript=True, transcript_lang=lang)
        if not data:
            return []
        return [self._video_data_to_result(data)]

    def _mode_channel(self, query: str, limit: int) -> list[SearchResult]:
        data = self.fetch_channel(query)
        if not data:
            return []
        out: list[SearchResult] = []
        head = []
        if data.get("subscriber_count_text"):
            head.append(data["subscriber_count_text"])
        if data.get("video_count_text"):
            head.append(data["video_count_text"])
        if data.get("description"):
            head.append(data["description"][:120])
        snippet = " · ".join(head)
        ch = SearchResult(
            title=(data.get("title") or "YouTube channel")[:200],
            url=data.get("url") or "",
            snippet=snippet[:400],
        )
        ch.channel = data.get("title") or ""                      # type: ignore[attr-defined]
        ch.channel_url = data.get("url") or ""                    # type: ignore[attr-defined]
        ch.subscribers = data.get("subscriber_count")             # type: ignore[attr-defined]
        ch.subscribers_text = data.get("subscriber_count_text", "")  # type: ignore[attr-defined]
        ch.video_count = data.get("video_count")                  # type: ignore[attr-defined]
        ch.video_count_text = data.get("video_count_text", "")    # type: ignore[attr-defined]
        ch.video_id = ""                                          # type: ignore[attr-defined]
        ch.thumbnail = data.get("avatar_url") or ""               # type: ignore[attr-defined]
        ch.is_channel = True                                      # type: ignore[attr-defined]
        out.append(ch)

        for v in (data.get("recent") or [])[:limit - 1]:
            r = SearchResult(
                title=(v.get("title") or "")[:200],
                url=v.get("url") or "",
                snippet=" · ".join(
                    s for s in (v.get("views_text"), v.get("upload_date"), v.get("duration_text")) if s
                ),
            )
            r.channel = data.get("title") or ""                   # type: ignore[attr-defined]
            r.channel_url = data.get("url") or ""                 # type: ignore[attr-defined]
            r.video_id = v.get("video_id") or ""                  # type: ignore[attr-defined]
            r.views = v.get("views")                              # type: ignore[attr-defined]
            r.views_text = v.get("views_text", "")                # type: ignore[attr-defined]
            r.duration = v.get("duration")                        # type: ignore[attr-defined]
            r.duration_text = v.get("duration_text", "")          # type: ignore[attr-defined]
            r.upload_date = v.get("upload_date", "")              # type: ignore[attr-defined]
            r.thumbnail = v.get("thumbnail", "")                  # type: ignore[attr-defined]
            r.is_channel = False                                  # type: ignore[attr-defined]
            out.append(r)
        self.last_status["mode"] = "channel"
        return out

    # ============================================================
    # Public fetch helpers
    # ============================================================

    def fetch_video(
        self,
        url_or_id: str,
        *,
        with_transcript: bool = False,
        transcript_lang: str = "en",
    ) -> dict:
        """Fetch full metadata for a single video.

        Returns ``{}`` on failure. Otherwise a dict with keys including
        ``video_id`` ``url`` ``title`` ``length_seconds`` ``views`` ``views_text``
        ``likes`` ``likes_text`` ``channel`` ``channel_id`` ``channel_url``
        ``subscriber_count`` ``subscriber_count_text`` ``description``
        ``keywords`` ``upload_date`` ``publish_date`` ``relative_date``
        ``thumbnail`` ``thumbnails`` ``category`` ``is_live`` ``captions``
        (list of {language_code, name, kind, url}). When
        ``with_transcript=True``, also ``transcript`` and ``transcript_lang``
        when the timedtext endpoint serves up content (sometimes blocked
        by YouTube's 2024 ``pot`` token requirement).
        """
        vid = self._normalise_video_id(url_or_id)
        if not vid:
            return {}

        self._maybe_warmup_homepage()

        url = f"https://www.youtube.com/watch?v={vid}"
        if not safe_goto(self.page, url, timeout=30000):
            return {}
        human_delay(1.5, 2.5)
        self._handle_consent()
        if self._is_blocked():
            return {}

        try:
            blobs = self.page.evaluate(
                """
                () => ({
                    initial: window.ytInitialData || null,
                    player: window.ytInitialPlayerResponse || null,
                })
                """
            ) or {}
        except Exception as e:
            log.debug("[youtube] cannot read JSON blobs: %s", e)
            blobs = {}
        initial = blobs.get("initial")
        player = blobs.get("player")

        if not player:
            try:
                html = self.page.content() or ""
            except Exception:
                html = ""
            m = re.search(
                r"ytInitialPlayerResponse\s*=\s*({.+?});\s*(?:var|window|</script>)",
                html, re.S,
            )
            if m:
                try:
                    player = json.loads(m.group(1))
                except json.JSONDecodeError:
                    player = None
        if not initial:
            try:
                html = self.page.content() or ""
            except Exception:
                html = ""
            m = re.search(
                r"ytInitialData\s*=\s*({.+?});\s*(?:var|window|</script>)",
                html, re.S,
            )
            if m:
                try:
                    initial = json.loads(m.group(1))
                except json.JSONDecodeError:
                    initial = None

        if not player:
            log.warning("[youtube] missing ytInitialPlayerResponse for %s", vid)
            return {}

        out = self._parse_video(player, initial or {}, vid)
        if with_transcript:
            tr = self._fetch_transcript_text(out.get("captions") or [], transcript_lang)
            if tr:
                out["transcript"] = tr["text"]
                out["transcript_lang"] = tr["lang"]
        return out

    def fetch_channel(self, query: str) -> dict:
        """Fetch a channel page's metadata + recent-video grid.

        ``query`` may be an ``@handle``, a ``UC...`` channel id, or a
        ``youtube.com/...`` URL. Always lands on ``/videos`` so the grid
        is server-rendered into ``ytInitialData``.
        """
        url = self._channel_url_from_query(query)
        if not url:
            return {}
        self._maybe_warmup_homepage()
        if not safe_goto(self.page, url, timeout=30000):
            return {}
        human_delay(1.5, 2.5)
        self._handle_consent()
        if self._is_blocked():
            return {}

        try:
            initial = self.page.evaluate("() => window.ytInitialData || null")
        except Exception:
            initial = None
        if not initial:
            try:
                html = self.page.content() or ""
            except Exception:
                html = ""
            m = re.search(
                r"ytInitialData\s*=\s*({.+?});\s*(?:var|window|</script>)",
                html, re.S,
            )
            if m:
                try:
                    initial = json.loads(m.group(1))
                except json.JSONDecodeError:
                    initial = None
        if not initial:
            return {}
        return self._parse_channel(initial, url)

    # ============================================================
    # Parsing
    # ============================================================

    def _parse_video(self, player: dict, initial: dict, vid: str) -> dict:
        vd = player.get("videoDetails") or {}
        microformat = (
            (player.get("microformat") or {}).get("playerMicroformatRenderer") or {}
        )

        length_seconds = None
        try:
            length_seconds = int(vd.get("lengthSeconds") or 0) or None
        except (TypeError, ValueError):
            pass
        view_count = None
        try:
            view_count = int(vd.get("viewCount") or 0) or None
        except (TypeError, ValueError):
            pass

        thumbs = (vd.get("thumbnail") or {}).get("thumbnails") or []
        thumb_max = thumbs[-1] if thumbs else None
        thumbnail = (thumb_max or {}).get("url", "")

        captions: list[dict] = []
        for ct in (
            (player.get("captions") or {})
            .get("playerCaptionsTracklistRenderer", {})
            .get("captionTracks", [])
        ):
            captions.append({
                "language_code": ct.get("languageCode") or "",
                "kind": ct.get("kind") or "",
                "name": self._runs_text(ct.get("name")),
                "url": ct.get("baseUrl") or "",
            })

        likes_text = ""
        likes = None
        relative_date = ""
        date_text = ""
        full_view_count_text = ""
        subscriber_count_text = ""
        subscriber_count = None
        try:
            cnts = (
                initial.get("contents", {})
                .get("twoColumnWatchNextResults", {})
                .get("results", {})
                .get("results", {})
                .get("contents", [])
            )
            for c in cnts:
                if not isinstance(c, dict):
                    continue
                if "videoPrimaryInfoRenderer" in c:
                    vp = c["videoPrimaryInfoRenderer"] or {}
                    relative_date = self._runs_text(vp.get("relativeDateText"))
                    date_text = self._runs_text(vp.get("dateText"))
                    vcr = (vp.get("viewCount") or {}).get("videoViewCountRenderer") or {}
                    full_view_count_text = self._runs_text(vcr.get("viewCount"))
                    likes_text = self._dig_like_count(vp) or ""
                    likes = self._parse_count_loose(likes_text)
                if "videoSecondaryInfoRenderer" in c:
                    vs = c["videoSecondaryInfoRenderer"] or {}
                    owner = (vs.get("owner") or {}).get("videoOwnerRenderer") or {}
                    subscriber_count_text = self._runs_text(owner.get("subscriberCountText"))
                    subscriber_count = self._parse_count_loose(
                        re.sub(r"\s*subscribers?", "", subscriber_count_text or "", flags=re.I).strip()
                    )
        except Exception as e:
            log.debug("[youtube] watch-page extras parse failed: %s", e)

        return {
            "video_id": vid,
            "url": f"https://www.youtube.com/watch?v={vid}",
            "title": vd.get("title") or "",
            "length_seconds": length_seconds,
            "duration_text": _format_duration(length_seconds) if length_seconds else "",
            "views": view_count,
            "views_text": full_view_count_text or (
                f"{view_count:,} views" if view_count else ""
            ),
            "likes": likes,
            "likes_text": likes_text,
            "channel": vd.get("author") or "",
            "channel_id": vd.get("channelId") or "",
            "channel_url": (
                f"https://www.youtube.com/channel/{vd.get('channelId')}"
                if vd.get("channelId") else ""
            ),
            "subscriber_count": subscriber_count,
            "subscriber_count_text": subscriber_count_text,
            "description": vd.get("shortDescription") or "",
            "keywords": vd.get("keywords") or [],
            "upload_date": microformat.get("uploadDate") or "",
            "publish_date": microformat.get("publishDate") or "",
            "relative_date": relative_date,
            "date_text": date_text,
            "thumbnail": thumbnail,
            "thumbnails": thumbs,
            "category": microformat.get("category") or "",
            "is_live": bool(vd.get("isLiveContent")),
            "captions": captions,
        }

    def _parse_channel(self, initial: dict, url: str) -> dict:
        """Walk a channel page's ytInitialData. Tries both
        ``c4TabbedHeaderRenderer`` (legacy) and ``pageHeaderRenderer`` (new
        viewModel-style layout YT rolled out in 2024-2025).
        """
        header = (initial.get("header") or {})
        title = ""
        subscriber_count_text = ""
        video_count_text = ""
        avatar_url = ""
        description = ""
        channel_id = ""

        c4 = header.get("c4TabbedHeaderRenderer")
        if c4:
            title = c4.get("title") or ""
            subscriber_count_text = self._runs_text(c4.get("subscriberCountText"))
            video_count_text = self._runs_text(c4.get("videosCountText"))
            try:
                avatar_url = (c4.get("avatar") or {}).get("thumbnails", [{}])[-1].get("url", "")
            except Exception:
                pass
            channel_id = c4.get("channelId") or ""

        ph = header.get("pageHeaderRenderer")
        if ph and not title:
            title = ph.get("pageTitle") or ""
            try:
                meta = (
                    (ph.get("content") or {})
                    .get("pageHeaderViewModel", {})
                    .get("metadata", {})
                    .get("contentMetadataViewModel", {})
                    .get("metadataRows", [])
                )
                for row in meta or []:
                    parts = (row or {}).get("metadataParts") or []
                    for p in parts:
                        text_obj = (p or {}).get("text") or {}
                        c = text_obj.get("content") or ""
                        low = c.lower()
                        if "subscriber" in low and not subscriber_count_text:
                            subscriber_count_text = c
                        elif "video" in low and "subscriber" not in low and not video_count_text:
                            video_count_text = c
                        elif c.startswith("@") and not channel_id:
                            channel_id = c
                try:
                    img = (
                        (ph.get("content") or {})
                        .get("pageHeaderViewModel", {})
                        .get("image", {})
                        .get("decoratedAvatarViewModel", {})
                        .get("avatar", {})
                        .get("avatarViewModel", {})
                        .get("image", {})
                        .get("sources", [])
                    )
                    if img:
                        avatar_url = img[-1].get("url", "")
                except Exception:
                    pass
            except Exception as e:
                log.debug("[youtube] pageHeaderRenderer dig failed: %s", e)

        cmr = (initial.get("metadata") or {}).get("channelMetadataRenderer") or {}
        if cmr:
            if not description:
                description = cmr.get("description") or ""
            if not channel_id:
                channel_id = cmr.get("externalId") or channel_id or ""
            if not avatar_url:
                try:
                    avatar_url = (cmr.get("avatar") or {}).get("thumbnails", [{}])[-1].get("url", "")
                except Exception:
                    pass

        subscriber_count = self._parse_count_loose(
            re.sub(r"\s*subscribers?", "", subscriber_count_text or "", flags=re.I).strip()
        )
        video_count = self._parse_count_loose(
            re.sub(r"\s*videos?", "", video_count_text or "", flags=re.I).strip()
        )

        recent = self._extract_channel_recent(initial)

        return {
            "url": url,
            "title": title,
            "channel_id": channel_id,
            "subscriber_count": subscriber_count,
            "subscriber_count_text": subscriber_count_text,
            "video_count": video_count,
            "video_count_text": video_count_text,
            "avatar_url": avatar_url,
            "description": description,
            "recent": recent,
        }

    def _extract_channel_recent(self, initial: dict) -> list[dict]:
        """Walk ``ytInitialData`` for the recent video grid.

        Handles both shapes YT serves on channel pages:
          * ``richItemRenderer.content.lockupViewModel`` (2024+ default)
          * ``videoRenderer`` / ``gridVideoRenderer`` (legacy)
        """
        out: list[dict] = []
        try:
            tabs = (
                initial.get("contents", {})
                .get("twoColumnBrowseResultsRenderer", {})
                .get("tabs", [])
            )
        except AttributeError:
            tabs = []

        def _walk(node):
            if isinstance(node, dict):
                if "videoRenderer" in node:
                    out.append(self._video_renderer_to_dict(node["videoRenderer"]))
                    return
                if "gridVideoRenderer" in node:
                    out.append(self._video_renderer_to_dict(node["gridVideoRenderer"]))
                    return
                if "lockupViewModel" in node:
                    out.append(self._lockup_to_dict(node["lockupViewModel"]))
                    return
                for v in node.values():
                    _walk(v)
            elif isinstance(node, list):
                for v in node:
                    _walk(v)

        _walk(tabs)
        seen: set = set()
        deduped: list[dict] = []
        for v in out:
            vid = v.get("video_id")
            if vid and vid in seen:
                continue
            if vid:
                seen.add(vid)
            deduped.append(v)
        return deduped

    @staticmethod
    def _video_renderer_to_dict(vr: dict) -> dict:
        vid = vr.get("videoId") or ""
        title = YouTubeEngine._runs_text(vr.get("title"))
        views_text = YouTubeEngine._runs_text(
            vr.get("viewCountText") or vr.get("shortViewCountText")
        )
        upload_date = YouTubeEngine._runs_text(vr.get("publishedTimeText"))
        duration_text = YouTubeEngine._runs_text(vr.get("lengthText"))
        thumbs = (vr.get("thumbnail") or {}).get("thumbnails") or []
        thumb = thumbs[-1].get("url", "") if thumbs else ""
        return {
            "video_id": vid,
            "url": f"https://www.youtube.com/watch?v={vid}" if vid else "",
            "title": title,
            "views": _parse_views(views_text),
            "views_text": views_text,
            "duration": _parse_duration(duration_text),
            "duration_text": duration_text,
            "upload_date": upload_date,
            "thumbnail": thumb,
        }

    @staticmethod
    def _lockup_to_dict(lvm: dict) -> dict:
        """Compact a 2024+ ``lockupViewModel`` to our recent[] shape."""
        vid = lvm.get("contentId") or ""
        title = ""
        try:
            title = (
                ((lvm.get("metadata") or {}).get("lockupMetadataViewModel") or {})
                .get("title", {}).get("content", "")
            )
        except AttributeError:
            pass
        views_text = ""
        upload_date = ""
        try:
            rows = (
                ((lvm.get("metadata") or {}).get("lockupMetadataViewModel") or {})
                .get("metadata", {})
                .get("contentMetadataViewModel", {})
                .get("metadataRows", [])
            )
            for row in rows or []:
                for part in (row or {}).get("metadataParts") or []:
                    txt = ((part or {}).get("text") or {}).get("content", "")
                    low = txt.lower()
                    if "view" in low and not views_text:
                        views_text = txt
                    elif ("ago" in low or "premiered" in low or "streamed" in low) and not upload_date:
                        upload_date = txt
        except Exception:
            pass
        duration_text = ""
        try:
            overlays = (
                ((lvm.get("contentImage") or {}).get("thumbnailViewModel") or {})
                .get("overlays", [])
            )
            for o in overlays or []:
                tbo = (o or {}).get("thumbnailBottomOverlayViewModel") or {}
                for b in tbo.get("badges") or []:
                    txt = ((b or {}).get("thumbnailBadgeViewModel") or {}).get("text", "")
                    if txt and re.match(r"^\d+:\d{2}", txt):
                        duration_text = txt
                        break
                if duration_text:
                    break
        except Exception:
            pass
        thumb = ""
        try:
            sources = (
                ((lvm.get("contentImage") or {}).get("thumbnailViewModel") or {})
                .get("image", {}).get("sources", [])
            )
            if sources:
                thumb = sources[-1].get("url", "")
        except Exception:
            pass
        return {
            "video_id": vid,
            "url": f"https://www.youtube.com/watch?v={vid}" if vid else "",
            "title": title,
            "views": _parse_views(views_text),
            "views_text": views_text,
            "duration": _parse_duration(duration_text),
            "duration_text": duration_text,
            "upload_date": upload_date,
            "thumbnail": thumb,
        }

    # ============================================================
    # Transcript
    # ============================================================

    def _fetch_transcript_text(
        self, captions: list[dict], lang: str,
    ) -> dict:
        """Pick the best matching caption track and fetch its XML.

        Many YT videos now require a ``pot=`` (Proof of Token) param signed
        by the player binary that we can't replicate; for those tracks the
        endpoint returns 200 OK with empty body. Callers should treat
        missing transcript as best-effort and use the per-track URL on
        the result themselves if needed.
        """
        if not captions:
            return {}
        lang_l = (lang or "").lower()

        def _score(c: dict) -> tuple:
            lc = (c.get("language_code") or "").lower()
            kind = (c.get("kind") or "").lower()
            exact = 1 if lc == lang_l else 0
            prefix = 1 if lc.split("-", 1)[0] == lang_l else 0
            non_asr = 0 if kind == "asr" else 1
            return (exact, prefix, non_asr)

        ranked = sorted(captions, key=_score, reverse=True)
        chosen = ranked[0]
        url = chosen.get("url") or ""
        if not url:
            return {}
        if "fmt=" not in url:
            url = url + ("&" if "?" in url else "?") + "fmt=srv3"
        try:
            resp = self.page.request.get(
                url, headers={"Referer": "https://www.youtube.com/"},
                timeout=20000,
            )
            xml_text = resp.text() or ""
        except Exception as e:
            log.debug("[youtube] transcript fetch failed: %s", e)
            return {}
        text = self._timedtext_xml_to_plain(xml_text)
        if not text:
            return {}
        return {"text": text, "lang": chosen.get("language_code") or lang_l}

    @staticmethod
    def _timedtext_xml_to_plain(xml_text: str) -> str:
        if not xml_text:
            return ""
        import html as _html
        text_chunks: list[str] = []
        for m in re.finditer(r"<text[^>]*>(.*?)</text>", xml_text, re.S):
            chunk = m.group(1)
            chunk = re.sub(r"<[^>]+>", "", chunk)
            chunk = _html.unescape(chunk).strip()
            if chunk:
                text_chunks.append(chunk)
        if text_chunks:
            return "\n".join(text_chunks)
        for m in re.finditer(r"<p[^>]*>(.*?)</p>", xml_text, re.S):
            chunk = m.group(1)
            chunk = re.sub(r"<[^>]+>", "", chunk)
            chunk = _html.unescape(chunk).strip()
            if chunk:
                text_chunks.append(chunk)
        return "\n".join(text_chunks)

    # ============================================================
    # Conversion + helpers
    # ============================================================

    def _video_data_to_result(self, data: dict) -> SearchResult:
        head = []
        if data.get("channel"):
            head.append(data["channel"])
        if data.get("views_text"):
            head.append(data["views_text"])
        elif data.get("views"):
            head.append(f"{data['views']:,} views")
        if data.get("date_text"):
            head.append(data["date_text"])
        elif data.get("upload_date"):
            head.append(data["upload_date"])
        if data.get("duration_text"):
            head.append(data["duration_text"])
        if data.get("likes_text"):
            head.append(f"{data['likes_text']} likes")
        snippet = " · ".join(head)
        if data.get("description"):
            snippet = snippet + " — " + data["description"][:240]
        if data.get("transcript"):
            snippet = snippet + f" [transcript: {len(data['transcript'])} chars, {data.get('transcript_lang', '')}]"

        r = SearchResult(
            title=(data.get("title") or "YouTube video")[:200],
            url=data.get("url") or "",
            snippet=snippet[:400],
        )
        r.video_id = data.get("video_id") or ""           # type: ignore[attr-defined]
        r.channel = data.get("channel") or ""             # type: ignore[attr-defined]
        r.channel_id = data.get("channel_id") or ""       # type: ignore[attr-defined]
        r.channel_url = data.get("channel_url") or ""     # type: ignore[attr-defined]
        r.subscribers = data.get("subscriber_count")      # type: ignore[attr-defined]
        r.subscribers_text = data.get("subscriber_count_text", "")  # type: ignore[attr-defined]
        r.views = data.get("views")                       # type: ignore[attr-defined]
        r.views_text = data.get("views_text", "")         # type: ignore[attr-defined]
        r.likes = data.get("likes")                       # type: ignore[attr-defined]
        r.likes_text = data.get("likes_text", "")         # type: ignore[attr-defined]
        r.duration = data.get("length_seconds")           # type: ignore[attr-defined]
        r.duration_text = data.get("duration_text", "")   # type: ignore[attr-defined]
        r.upload_date = data.get("upload_date") or data.get("date_text", "")  # type: ignore[attr-defined]
        r.publish_date = data.get("publish_date", "")     # type: ignore[attr-defined]
        r.relative_date = data.get("relative_date", "")   # type: ignore[attr-defined]
        r.description = data.get("description", "")     # type: ignore[attr-defined]
        r.keywords = data.get("keywords") or []           # type: ignore[attr-defined]
        r.thumbnail = data.get("thumbnail", "")           # type: ignore[attr-defined]
        r.thumbnails = data.get("thumbnails") or []       # type: ignore[attr-defined]
        r.category = data.get("category", "")             # type: ignore[attr-defined]
        r.is_live = data.get("is_live", False)            # type: ignore[attr-defined]
        r.captions = data.get("captions") or []           # type: ignore[attr-defined]
        if "transcript" in data:
            r.transcript = data["transcript"]             # type: ignore[attr-defined]
            r.transcript_lang = data.get("transcript_lang", "")  # type: ignore[attr-defined]
        return r

    def _normalise_video_id(self, query: str) -> str:
        if not query:
            return ""
        q = query.strip()
        vid = _video_id_from_url(q)
        if vid and re.fullmatch(r"[A-Za-z0-9_-]{11}", vid):
            return vid
        if re.fullmatch(r"[A-Za-z0-9_-]{11}", q):
            return q
        return ""

    @staticmethod
    def _channel_url_from_query(query: str) -> str:
        """Resolve a channel query into a ``/videos`` tab URL.

        Always lands on the Videos tab so the recent grid is rendered into
        ``ytInitialData`` on first paint (the Home tab serves shelves /
        playlist mixes that don't carry the same lockupViewModel cards).
        """
        if not query:
            return ""
        q = query.strip()
        if q.startswith("http://") or q.startswith("https://"):
            base = q.rstrip("/")
            if not re.search(
                r"/(videos|shorts|streams|community|playlists|featured|about)/?$",
                base,
            ):
                base = base + "/videos"
            return base
        if q.startswith("@"):
            return f"https://www.youtube.com/{q}/videos"
        if q.startswith("UC") and len(q) > 20:
            return f"https://www.youtube.com/channel/{q}/videos"
        if re.fullmatch(r"[A-Za-z0-9_.-]+", q):
            return f"https://www.youtube.com/@{q}/videos"
        return ""

    def _maybe_warmup_homepage(self) -> None:
        try:
            cur = (self.page.url or "").lower()
        except Exception:
            cur = ""
        if "youtube.com" in cur:
            return
        if safe_goto(self.page, self.HOMEPAGE_URL, timeout=20000, retries=1):
            human_delay(0.8, 1.6)
            self._handle_consent()

    @staticmethod
    def _dig_like_count(vp: dict) -> str:
        """Walk the like-button viewModel chain and return its title text."""
        try:
            buttons = (
                (vp.get("videoActions") or {})
                .get("menuRenderer", {})
                .get("topLevelButtons", [])
            )
        except AttributeError:
            buttons = []
        for b in buttons or []:
            sm = (b or {}).get("segmentedLikeDislikeButtonViewModel")
            if sm:
                cur = sm
                # YT actually nests likeButtonViewModel + toggleButtonViewModel twice.
                for key in (
                    "likeButtonViewModel", "likeButtonViewModel",
                    "toggleButtonViewModel", "toggleButtonViewModel",
                    "defaultButtonViewModel", "buttonViewModel",
                ):
                    if not isinstance(cur, dict):
                        cur = {}
                        break
                    cur = cur.get(key) or {}
                if isinstance(cur, dict) and cur.get("title"):
                    return cur["title"]
            tbr = (b or {}).get("toggleButtonRenderer")
            if tbr:
                t = YouTubeEngine._runs_text(tbr.get("defaultText"))
                if t:
                    return t
        return ""

    @staticmethod
    def _parse_count_loose(text: str) -> int | None:
        """'19M' / '2.3K' / '1,234' / '492 million' -> int."""
        if not text:
            return None
        t = text.strip().replace(",", "")
        m = re.fullmatch(r"\s*([\d.]+)\s*([KMBkmb]?)\s*", t)
        if not m:
            return None
        try:
            num = float(m.group(1))
        except ValueError:
            return None
        mult = {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(
            m.group(2).lower(), 1,
        )
        return int(num * mult)


def _format_duration(seconds: int | None) -> str:
    """Inverse of _parse_duration: 213 -> '3:33', 3600 -> '1:00:00'."""
    if not seconds:
        return ""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"
