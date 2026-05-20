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
