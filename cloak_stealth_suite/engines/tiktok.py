"""TikTok search adapter.

TikTok aggressively blocks unauthenticated automation, so this engine has
two completely separate code paths and the public ``search()`` method picks
the first one that returns anything useful:

1. **Direct path** — ``https://www.tiktok.com/search?q=<query>``.
   On a "good" session the page does render search anchors of the form
   ``/@<user>/video/<id>`` even without login, but a few things commonly
   go wrong:

   * A login modal slides up within ~1-2s and covers the page. We dismiss
     it (or click outside) before extraction.
   * EU / fresh-fingerprint sessions get a CAPTCHA / "verify-bar".
   * Some regions hard-redirect ``/search`` to ``/login`` for guests.
   * The result list is hydrated client-side from JSON; we wait for at
     least one ``a[href*="/video/"]`` to attach before parsing.

2. **Google ``site:tiktok.com`` fallback** — when the direct path returns
   nothing, we drive :class:`GoogleEngine` on the same page and keep only
   the hits whose URL points at a TikTok video page
   (``/@<user>/video/<id>``). This is how we still produce results when
   TikTok itself blocks us, and is conceptually the same trick the Twitter
   adapter uses with Nitter mirrors.

Each :class:`SearchResult` carries:

* ``author``      – ``@username`` of the uploader (or "" if unknown)
* ``author_url``  – absolute URL to the author profile
* ``video_id``    – numeric id from the ``/video/<id>`` path
* ``likes``       – integer like count when shown, ``None`` otherwise
* ``likes_text``  – original "12.3K" / "1.2M" string (kept for display)
* ``source``      – ``"tiktok"`` (direct) or ``"google"`` (fallback)
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


# Anchors with this href shape are TikTok video pages — the canonical form
# is ``/@<username>/video/<numeric_id>``. Username may include letters,
# digits, dots and underscores per TikTok's username rules.
VIDEO_HREF_RE = re.compile(r"^/(@[^/?#]+)/video/(\d+)")
# Same shape but absolute URL (used for Google fallback hits).
ABS_VIDEO_URL_RE = re.compile(
    r"https?://(?:www\.|m\.)?tiktok\.com/(@[^/?#]+)/video/(\d+)"
)

# Selectors used to detect whether the search page has hydrated.
RESULT_PRESENCE_SELECTORS = [
    'a[href*="/video/"]',
    '[data-e2e="search_video-item"]',
    '[data-e2e="search-card-item"]',
    '[data-e2e="search-common-link"]',
]

# Login modal / overlay close buttons. TikTok's login modal varies between
# layouts; these all close it without signing in.
LOGIN_MODAL_CLOSE_SELECTORS = [
    'div[data-e2e="modal-close-inner-button"]',
    'button[aria-label="Close"]',
    'div[aria-label="Close"]',
    'svg[data-e2e="modal-close-inner-button"]',
    "#login-modal button[type='button']",
]

# Cookie banner buttons (EU / UK).
COOKIE_BUTTON_SELECTORS = [
    "tiktok-cookie-banner button",
    "button[data-e2e='cookie-banner-accept']",
    "button[aria-label*='Accept' i]",
    "button[aria-label*='Allow' i]",
]

# Phrases that indicate TikTok is gating us (CAPTCHA / login wall / 403).
BLOCK_PHRASES = [
    "log in to tiktok",
    "sign up for tiktok",
    "please verify",
    "verify to continue",
    "secsdk-captcha",
    "verify-bar",
    "access denied",
    "403 forbidden",
    "page not available",
    "something went wrong",
    "couldn't find this account",
]


def _abs_url(href: str) -> str:
    """Make a TikTok href absolute against ``www.tiktok.com``."""
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://www.tiktok.com" + href
    return "https://www.tiktok.com/" + href


def _parse_count(text: str) -> int | None:
    """Parse "12.3K" / "1.2M" / "543" / "1,234" into an int.

    Returns ``None`` when the text doesn't look like a count.
    """
    if not text:
        return None
    t = text.strip().lower().replace(",", "")
    m = re.fullmatch(r"(\d+\.?\d*)\s*([kmb]?)", t)
    if not m:
        return None
    try:
        n = float(m.group(1))
    except ValueError:
        return None
    mult = {"": 1, "k": 1_000, "m": 1_000_000, "b": 1_000_000_000}.get(
        m.group(2), 1
    )
    return int(n * mult)


class TikTokEngine(BaseEngine):
    """Search TikTok via the public search page, with a Google fallback."""

    name = "tiktok"
    max_retries = 2  # The Google fallback already adds resilience.

    SEARCH_URL = "https://www.tiktok.com/search?q={q}"
    HOMEPAGE_URL = "https://www.tiktok.com/"

    def __init__(self, page):
        super().__init__(page)
        # Diagnostics surface for callers / tests.
        self.last_status: dict = {}

    # ------------------------------------------------------------ main flow

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # 1) Try the direct TikTok search page.
        direct = self._search_direct(query, limit)
        if direct:
            self.last_status["mode"] = "direct"
            return direct

        # 2) Fall back to Google `site:tiktok.com`.
        log.info("[tiktok] direct path empty; falling back to Google site:tiktok.com")
        fallback = self._search_google_fallback(query, limit)
        if fallback:
            self.last_status["mode"] = "google"
        return fallback

    # ------------------------------------------------------------ direct path

    def _search_direct(self, query: str, limit: int) -> list[SearchResult]:
        """Hit ``tiktok.com/search`` and try to parse the rendered DOM."""
        # Warm-up: visit homepage so basic cookies + the bot signal cookie
        # ``tt_csrf_token`` get set before issuing the search.
        if safe_goto(self.page, self.HOMEPAGE_URL, timeout=20000, retries=1):
            human_delay(1.0, 2.5)
            self._dismiss_overlays()
            self._human_hints()

        url = self.SEARCH_URL.format(q=urllib.parse.quote(query))
        log.info("[tiktok] navigating to %s", url)
        if not safe_goto(self.page, url, timeout=30000):
            self.last_status = {"phase": "direct", "error": "goto_failed"}
            return []

        human_delay(2.0, 3.5)
        self._dismiss_overlays()
        self._human_hints()

        # Hard-redirect to login? Bail to fallback.
        cur = (self.page.url or "").lower()
        if "tiktok.com/login" in cur or "tiktok.com/foryou/login" in cur:
            log.warning("[tiktok] redirected to login: %s", cur)
            self.last_status = {"phase": "direct", "block_reason": "login_redirect"}
            return []

        if self._is_blocked():
            return []

        # Wait for at least one video anchor to attach.
        if not self._wait_for_results(timeout_ms=10000):
            log.info("[tiktok] no result anchors after wait; trying extraction anyway")

        # One more overlay sweep in case the modal slid up during the wait.
        self._dismiss_overlays()

        results = self._extract_direct(limit)
        log.info("[tiktok] direct path extracted: %d", len(results))
        return results

    def _extract_direct(self, limit: int) -> list[SearchResult]:
        """Walk every ``a[href*="/video/"]`` on the page and build results."""
        try:
            raw: list[dict] = self.page.evaluate(_EXTRACT_JS) or []
        except Exception as e:
            log.warning("[tiktok] extraction JS failed: %s", e)
            raw = []

        log.info("[tiktok] direct raw extracted: %d", len(raw))

        results: list[SearchResult] = []
        seen: set[str] = set()
        for item in raw:
            video_id = (item.get("video_id") or "").strip()
            if not video_id or video_id in seen:
                continue
            href = (item.get("href") or "").strip()
            m = VIDEO_HREF_RE.match(href)
            if not m:
                continue
            author = m.group(1)  # already prefixed with '@'
            video_url = f"https://www.tiktok.com/{author}/video/{video_id}"
            seen.add(video_id)

            title = (item.get("title") or "").strip()
            if not title:
                # Some cards only show the author + thumbnail — synthesise a
                # title so the SearchResult is non-empty.
                title = f"{author} on TikTok"

            likes_text = (item.get("likes_text") or "").strip()
            likes = _parse_count(likes_text)

            head_bits: list[str] = [author]
            if likes_text:
                head_bits.append(f"{likes_text} likes")
            snippet = " · ".join(head_bits)

            r = SearchResult(title=title, url=video_url, snippet=snippet)
            r.author = author                                       # type: ignore[attr-defined]
            r.author_url = f"https://www.tiktok.com/{author}"        # type: ignore[attr-defined]
            r.video_id = video_id                                    # type: ignore[attr-defined]
            r.likes = likes                                          # type: ignore[attr-defined]
            r.likes_text = likes_text                                # type: ignore[attr-defined]
            r.source = "tiktok"                                      # type: ignore[attr-defined]
            results.append(r)
            if len(results) >= limit:
                break

        return results

    # ------------------------------------------------------------ Google fallback

    def _search_google_fallback(
        self, query: str, limit: int
    ) -> list[SearchResult]:
        """Use GoogleEngine for ``site:tiktok.com <query>``.

        We use *two* strategies on the Google results page:

        1. ``GoogleEngine.search()`` returns the structured organic-results
           list. We keep any whose URL matches a TikTok video URL.
        2. Right after, we walk the *full* DOM of the Google results page
           ourselves and collect every anchor whose href matches a TikTok
           video URL. This catches results in the "Videos" carousel, "Top
           stories" cards, sidebar panels, and other layouts that
           GoogleEngine's ``.tF2Cxc``-based extraction misses.

        We narrow the query with ``inurl:video`` so Google biases toward
        actual video pages instead of ``/discover/``, ``/tag/`` or channel
        pages, which can't be normalised to a ``/@user/video/<id>`` URL.
        """
        try:
            google = GoogleEngine(self.page)
        except Exception as e:
            log.warning("[tiktok] cannot construct GoogleEngine: %s", e)
            return []

        # ``inurl:video`` keeps us on actual video pages.
        google_query = f"site:tiktok.com inurl:video {query}"
        try:
            google_results = google.search(google_query, limit=max(limit * 3, 20))
        except Exception as e:
            log.warning("[tiktok] google fallback raised: %s", e)
            google_results = []

        log.info(
            "[tiktok] google returned %d organic results for %r",
            len(google_results), google_query,
        )
        self.last_status.setdefault("phase", "google")
        self.last_status["google_status"] = getattr(google, "last_status", {})

        # Strategy 1: filter Google's structured results.
        candidates: list[dict] = []
        for r in google_results:
            entry = self._parse_google_url(r.url or "", title=r.title or "",
                                           snippet=r.snippet or "")
            if entry:
                candidates.append(entry)

        # Strategy 2: scan all anchors on the Google results page for video
        # URLs the structured extractor might have skipped.
        try:
            anchors = self.page.evaluate(
                """
                () => Array.from(document.querySelectorAll('a[href]')).map(a => ({
                    href: a.href || '',
                    text: (a.innerText || a.textContent || '').trim()
                }))
                """
            ) or []
        except Exception as e:
            log.debug("[tiktok] DOM anchor scan raised: %s", e)
            anchors = []

        for a in anchors:
            entry = self._parse_google_url(
                a.get("href", ""), title=a.get("text", "") or "", snippet=""
            )
            if entry:
                candidates.append(entry)

        log.info("[tiktok] google fallback total candidates: %d", len(candidates))

        results: list[SearchResult] = []
        seen: set[str] = set()
        for c in candidates:
            video_id = c["video_id"]
            if video_id in seen:
                continue
            seen.add(video_id)

            author = c["author"]
            canonical_url = f"https://www.tiktok.com/{author}/video/{video_id}"
            title = c["title"] or f"{author} on TikTok"
            snippet_in = c["snippet"]
            head_bits: list[str] = [author]
            if snippet_in:
                merged_snippet = " · ".join(head_bits) + " — " + snippet_in
            else:
                merged_snippet = " · ".join(head_bits)

            new_r = SearchResult(
                title=title, url=canonical_url, snippet=merged_snippet
            )
            new_r.author = author                                    # type: ignore[attr-defined]
            new_r.author_url = f"https://www.tiktok.com/{author}"     # type: ignore[attr-defined]
            new_r.video_id = video_id                                 # type: ignore[attr-defined]
            new_r.likes = None                                        # type: ignore[attr-defined]
            new_r.likes_text = ""                                     # type: ignore[attr-defined]
            new_r.source = "google"                                   # type: ignore[attr-defined]
            results.append(new_r)
            if len(results) >= limit:
                break

        return results

    @staticmethod
    def _parse_google_url(
        url: str, title: str = "", snippet: str = ""
    ) -> dict | None:
        """Match a Google-result URL against ``ABS_VIDEO_URL_RE``.

        Also handles Google's ``/url?q=https://www.tiktok.com/...`` redirect
        wrapper that survives on some layouts.
        """
        if not url:
            return None
        # Some anchors on the Google results page are still wrapped with the
        # legacy /url?q= redirect.
        if "/url?" in url and "tiktok.com" in url:
            try:
                qs = urllib.parse.urlparse(url).query
                target = urllib.parse.parse_qs(qs).get("q", [""])[0]
                if target:
                    url = target
            except Exception:
                pass
        m = ABS_VIDEO_URL_RE.match(url)
        if not m:
            return None
        return {
            "author": m.group(1),
            "video_id": m.group(2),
            "title": title,
            "snippet": snippet,
        }

    # ------------------------------------------------------------ helpers

    def _dismiss_overlays(self) -> None:
        """Close login modals, cookie banners, and any other guest overlays."""
        # 1) Cookie banner first — it's at the bottom and generally cheaper.
        for sel in COOKIE_BUTTON_SELECTORS:
            try:
                btn = self.page.query_selector(sel)
                if btn:
                    try:
                        btn.click(timeout=2000)
                    except Exception:
                        try:
                            self.page.evaluate("(el) => el.click()", btn)
                        except Exception:
                            continue
                    log.info("[tiktok] dismissed cookie banner (%s)", sel)
                    human_delay(0.4, 0.9)
                    break
            except Exception:
                continue

        # 2) Login modal — click the X if present.
        for sel in LOGIN_MODAL_CLOSE_SELECTORS:
            try:
                btn = self.page.query_selector(sel)
                if btn:
                    try:
                        btn.click(timeout=2000)
                    except Exception:
                        try:
                            self.page.evaluate("(el) => el.click()", btn)
                        except Exception:
                            continue
                    log.info("[tiktok] closed login modal (%s)", sel)
                    human_delay(0.4, 0.9)
                    return
            except Exception:
                continue

        # 3) Last resort: press Escape (covers any focus-trapping modal).
        try:
            self.page.keyboard.press("Escape")
        except Exception:
            pass

    def _is_blocked(self) -> bool:
        """Detect CAPTCHA / login-wall / sorry-page interstitials."""
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

        # Hard redirects.
        if "/login" in url and "tiktok.com" in url:
            log.warning("[tiktok] login redirect: %s", url)
            self.last_status["block_reason"] = "login_redirect"
            return True

        # CAPTCHA / verify bars are usually rendered into specific containers.
        for sel in (
            ".captcha_verify_container",
            "#captcha-verify-container",
            "[id^='captcha_container']",
            "[class*='verify-bar']",
        ):
            try:
                if self.page.query_selector(sel):
                    log.warning("[tiktok] captcha element matched: %s", sel)
                    self.last_status["block_reason"] = "captcha"
                    return True
            except Exception:
                continue

        # If the *only* visible text is the login prompt, treat as blocked.
        if (
            "log in to tiktok" in body
            and "search" not in body
            and "video" not in body
        ):
            log.warning("[tiktok] login wall (search content missing)")
            self.last_status["block_reason"] = "login_wall"
            return True

        for phrase in BLOCK_PHRASES:
            if phrase in title:
                log.warning("[tiktok] block phrase in title: %r", phrase)
                self.last_status["block_reason"] = phrase
                return True

        return False

    def _human_hints(self) -> None:
        """Tiny mouse / scroll movement so lazy hydration commits."""
        try:
            self.page.mouse.move(
                random.randint(120, 500),
                random.randint(120, 400),
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
        """Wait for at least one video anchor / search card to attach."""
        deadline = time.time() + timeout_ms / 1000.0
        try:
            self.page.wait_for_function(
                """
                () => {
                  const anchors = document.querySelectorAll('a[href]');
                  for (const a of anchors) {
                    const href = a.getAttribute('href') || '';
                    if (/\\/video\\/\\d+/.test(href)) return true;
                  }
                  return !!document.querySelector(
                    '[data-e2e="search_video-item"], [data-e2e="search-card-item"]'
                  );
                }
                """,
                timeout=timeout_ms,
            )
            return True
        except Exception as e:
            log.debug("[tiktok] wait_for_function timeout: %s", e)
        # Manual poll fallback.
        while time.time() < deadline:
            for sel in RESULT_PRESENCE_SELECTORS:
                try:
                    if self.page.query_selector(sel):
                        return True
                except Exception:
                    continue
            time.sleep(0.5)
        return False

    def selector_counts(self) -> dict[str, int]:
        """Per-selector match counts on the current page (for diagnostics)."""
        counts: dict[str, int] = {}
        for sel in (
            'a[href*="/video/"]',
            '[data-e2e="search_video-item"]',
            '[data-e2e="search-card-item"]',
            '[data-e2e="search-card-user-link"]',
            '[data-e2e="search-card-video-caption"]',
            '[data-e2e="like-count"]',
        ):
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        return counts


# ---------------------------------------------------------------- JS
#
# Walks every anchor whose href contains ``/video/<id>`` (whether absolute or
# relative), then resolves the surrounding card to dig out the caption /
# like count / canonical author. We do this in JS rather than chained
# Python selectors to avoid dozens of CDP round-trips per result.
#
# TikTok markup conventions used here (resilient to class-name churn):
#   * Card container : an ancestor with [data-e2e^="search"] or
#                      [data-e2e^="search_video-item"].
#   * Caption        : [data-e2e="search-card-video-caption"] / "card-desc"
#                      / nearby <h3> / nearby <span>; we fall back to the
#                      anchor's own text or its image alt.
#   * Author link    : [data-e2e="search-card-user-link"] /
#                      a[href^="/@"] (excluding the video link itself).
#   * Like count     : [data-e2e="like-count"] / strong with "K"/"M" suffix.
_EXTRACT_JS = r"""
() => {
  const HREF_RE = /^\/(@[^\/?#]+)\/video\/(\d+)/;
  const ABS_RE  = /^https?:\/\/(?:www\.|m\.)?tiktok\.com\/(@[^\/?#]+)\/video\/(\d+)/;

  const text = (el) => (el ? (el.innerText || el.textContent || "").trim() : "");

  const findCard = (a) => {
    return (
      a.closest('[data-e2e^="search_video-item"]') ||
      a.closest('[data-e2e^="search-card-item"]') ||
      a.closest('[data-e2e="search-common-link"]') ||
      a.closest('[data-e2e^="search"]') ||
      a.closest('div[class*="DivItemContainer"]') ||
      a.parentElement
    );
  };

  const captionFromCard = (card, a) => {
    if (!card) return "";
    // Highest-confidence selectors first.
    const sels = [
      '[data-e2e="search-card-video-caption"]',
      '[data-e2e="search-card-desc"]',
      '[data-e2e="search-card-title"]',
      'h3',
      'h4',
    ];
    for (const sel of sels) {
      const el = card.querySelector(sel);
      const t = text(el);
      if (t) return t;
    }
    // Fall back to the anchor's text / image alt / aria-label.
    const at = text(a);
    if (at) return at;
    const img = card.querySelector('img[alt]');
    if (img) {
      const alt = (img.getAttribute('alt') || '').trim();
      if (alt) return alt;
    }
    const aria = a.getAttribute('aria-label') || '';
    if (aria) return aria.trim();
    return "";
  };

  const likesFromCard = (card) => {
    if (!card) return "";
    const direct = card.querySelector('[data-e2e="like-count"]');
    if (direct) {
      const t = text(direct);
      if (t) return t;
    }
    // Hunt for "Strong" / "span" containing a count followed by K/M/B or
    // pure digits — but skip the video id itself by capping length.
    const candidates = card.querySelectorAll('strong, span');
    for (const el of candidates) {
      const t = text(el);
      if (!t || t.length > 10) continue;
      if (/^\d+(\.\d+)?\s*[KMB]?$/i.test(t)) return t;
      if (/^\d{1,3}(,\d{3})+$/.test(t)) return t;
    }
    return "";
  };

  const out = [];
  const seen = new Set();
  const anchors = document.querySelectorAll('a[href]');

  for (const a of anchors) {
    const href = a.getAttribute('href') || '';
    let m = href.match(HREF_RE);
    let normalisedHref = href;
    if (!m) {
      m = href.match(ABS_RE);
      if (m) {
        normalisedHref = "/" + m[1] + "/video/" + m[2];
      }
    }
    if (!m) continue;

    const author  = m[1];   // e.g. "@gordonramsayofficial"
    const videoId = m[2];
    if (seen.has(videoId)) continue;

    const card = findCard(a);
    const title       = captionFromCard(card, a);
    const likes_text  = likesFromCard(card);

    seen.add(videoId);
    out.push({
      href:       normalisedHref,
      author:     author,
      video_id:   videoId,
      title:      title,
      likes_text: likes_text,
    });
  }

  return out;
}
"""
