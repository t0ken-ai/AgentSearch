"""Instagram search adapter.

Instagram aggressively blocks unauthenticated automation: most public
endpoints render a login wall within a second of landing, and even when a
hashtag page does render, the post grid hydrates from XHR calls that the
guest token cannot complete. This engine therefore has two completely
separate code paths and the public ``search()`` method picks the first one
that returns anything useful:

1. **Direct path** — ``https://www.instagram.com/explore/tags/<tag>/``.
   On a "good" session the page does render anchors of the form
   ``/p/<shortcode>/`` (feed posts) or ``/reel/<shortcode>/`` (reels) even
   without login, but a few things commonly go wrong:

   * A login modal opens within ~1-2s. We try to dismiss it (close button,
     "Not now" link, or click outside) before extraction.
   * Some regions hard-redirect ``/explore/tags/<tag>/`` to
     ``/accounts/login/`` for guests.
   * The grid is hydrated client-side from a private XHR; we wait for at
     least one ``a[href*="/p/"]`` or ``a[href*="/reel/"]`` to attach
     before parsing.

2. **Google ``site:instagram.com`` fallback** — when the direct path
   returns nothing, we drive :class:`GoogleEngine` on the same page and
   keep only the hits whose URL points at an Instagram post or reel
   (``/p/<shortcode>/`` or ``/reel/<shortcode>/``). This is how we still
   produce results when Instagram itself blocks us, the same trick the
   TikTok adapter uses with its Google fallback.

Each :class:`SearchResult` carries:

* ``user``        – ``username`` of the uploader (no leading ``@``), or "" if unknown
* ``user_url``    – absolute URL to the author profile, or "" if unknown
* ``shortcode``   – Instagram's post shortcode from ``/p/<sc>/`` or ``/reel/<sc>/``
* ``post_type``   – ``"post"`` or ``"reel"``
* ``caption``     – original caption text (may be empty)
* ``likes``       – integer like count when shown, ``None`` otherwise
* ``likes_text``  – original "12.3K" / "1.2M" string (kept for display)
* ``comments``    – integer comment count when shown, ``None`` otherwise
* ``comments_text`` – original comments-count string
* ``source``      – ``"instagram"`` (direct) or ``"google"`` (fallback)
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


# Anchors with this href shape are Instagram post / reel pages. Both ``/p/``
# (feed posts) and ``/reel/`` (reels / video posts) are accepted; the
# shortcode is Instagram's URL-safe base64 id.
POST_HREF_RE = re.compile(r"^/(p|reel)/([A-Za-z0-9_-]+)/?")
ABS_POST_URL_RE = re.compile(
    r"https?://(?:www\.)?instagram\.com/(p|reel)/([A-Za-z0-9_-]+)/?"
)
# Profile / user link, e.g. ``/some_user/`` (excluding reserved prefixes).
USER_HREF_RE = re.compile(
    r"^/((?!explore|p/|reel/|stories|accounts|direct|reels|about|developer|"
    r"legal|press|api|emails|web|graphql|locations|tags|challenge|igtv|"
    r"_n|_u|_i)[A-Za-z0-9_.]+)/?$"
)

# Selectors used to detect whether the hashtag / search page has hydrated.
RESULT_PRESENCE_SELECTORS = [
    'a[href*="/p/"]',
    'a[href*="/reel/"]',
    "main article a",
    "article a",
]

# Login modal close buttons. Instagram's login modal varies between
# layouts; these all close it without signing in.
LOGIN_MODAL_CLOSE_SELECTORS = [
    'button[aria-label="Close"]',
    'svg[aria-label="Close"]',
    'div[role="dialog"] button[aria-label="Close"]',
    'div[role="presentation"] button[aria-label="Close"]',
    # The "Not Now" link on the "Save your login info?" prompt.
    "button:has-text('Not Now')",
    "button:has-text('Not now')",
]

# Cookie / "allow essential cookies" banner buttons (EU / UK).
COOKIE_BUTTON_SELECTORS = [
    "button:has-text('Allow all cookies')",
    "button:has-text('Allow essential')",
    "button:has-text('Decline optional')",
    "button:has-text('Accept All')",
    "button:has-text('Accept all')",
    "button:has-text('Only allow essential')",
    "button[aria-label*='Accept' i]",
    "button[aria-label*='Allow' i]",
]

# Phrases that indicate Instagram is gating us (login wall / 404 / ratelimit).
BLOCK_PHRASES = [
    "log in to instagram",
    "log into instagram",
    "sign up for instagram",
    "page not found",
    "sorry, this page",
    "please wait a few minutes",
    "try again later",
    "we restrict certain activity",
    "challenge_required",
    "checkpoint_required",
]


def _abs_url(href: str) -> str:
    """Make an Instagram href absolute against ``www.instagram.com``."""
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return "https://www.instagram.com" + href
    return "https://www.instagram.com/" + href


def _parse_count(text: str) -> int | None:
    """Parse "12.3K" / "1.2M" / "543" / "1,234" / "1 234" into an int.

    Returns ``None`` when the text doesn't look like a count.
    """
    if not text:
        return None
    t = text.strip().lower().replace(",", "").replace(" ", "")
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


def _hashtagify(query: str) -> str:
    """Turn a free-text query into the slug Instagram uses in its hashtag URL.

    Instagram's hashtag pages are at ``/explore/tags/<slug>/`` where the
    slug is the lowercased word with all non-alphanumeric characters
    removed (no spaces, no leading ``#``). Multi-word queries are
    concatenated into a single slug.
    """
    if not query:
        return ""
    q = query.strip().lstrip("#").lower()
    return re.sub(r"[^a-z0-9_\u00c0-\uffff]+", "", q)


class InstagramEngine(BaseEngine):
    """Search Instagram via the public hashtag page, with a Google fallback."""

    name = "instagram"
    max_retries = 2  # The Google fallback already adds resilience.

    TAG_URL = "https://www.instagram.com/explore/tags/{tag}/"
    SEARCH_URL = "https://www.instagram.com/explore/search/keyword/?q={q}"
    HOMEPAGE_URL = "https://www.instagram.com/"

    def __init__(self, page):
        super().__init__(page)
        # Diagnostics surface for callers / tests.
        self.last_status: dict = {}

    # ------------------------------------------------------------ main flow

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # 1) Try the direct Instagram hashtag page.
        direct = self._search_direct(query, limit)
        if direct:
            self.last_status["mode"] = "direct"
            return direct

        # 2) Fall back to Google `site:instagram.com`.
        log.info(
            "[instagram] direct path empty; falling back to Google "
            "site:instagram.com"
        )
        fallback = self._search_google_fallback(query, limit)
        if fallback:
            self.last_status["mode"] = "google"
        return fallback

    # ------------------------------------------------------------ direct path

    def _search_direct(self, query: str, limit: int) -> list[SearchResult]:
        """Hit Instagram's hashtag page and try to parse the rendered DOM."""
        # Warm-up: visit homepage so basic cookies get set before we ask
        # for a hashtag page (otherwise the redirect to /accounts/login/
        # fires before the hashtag HTML even loads).
        if safe_goto(self.page, self.HOMEPAGE_URL, timeout=20000, retries=1):
            human_delay(1.0, 2.5)
            self._dismiss_overlays()
            self._human_hints()

        tag = _hashtagify(query)
        if not tag:
            self.last_status = {"phase": "direct", "error": "empty_tag"}
            return []

        url = self.TAG_URL.format(tag=urllib.parse.quote(tag))
        log.info("[instagram] navigating to %s", url)
        if not safe_goto(self.page, url, timeout=30000):
            self.last_status = {"phase": "direct", "error": "goto_failed"}
            return []

        human_delay(2.0, 3.5)
        self._dismiss_overlays()
        self._human_hints()

        # Hard-redirect to login? Bail to fallback.
        cur = (self.page.url or "").lower()
        if "instagram.com/accounts/login" in cur:
            log.warning("[instagram] redirected to login: %s", cur)
            self.last_status = {"phase": "direct", "block_reason": "login_redirect"}
            return []

        if self._is_blocked():
            return []

        # Wait for at least one post / reel anchor to attach.
        if not self._wait_for_results(timeout_ms=8000):
            log.info(
                "[instagram] no result anchors after wait; "
                "trying extraction anyway"
            )

        # Extra overlay sweep in case the login modal slid up while we waited.
        self._dismiss_overlays()

        results = self._extract_direct(limit)
        log.info("[instagram] direct path extracted: %d", len(results))
        return results

    def _extract_direct(self, limit: int) -> list[SearchResult]:
        """Walk every Instagram post / reel anchor on the page."""
        try:
            raw: list[dict] = self.page.evaluate(_EXTRACT_JS) or []
        except Exception as e:
            log.warning("[instagram] extraction JS failed: %s", e)
            raw = []

        log.info("[instagram] direct raw extracted: %d", len(raw))

        results: list[SearchResult] = []
        seen: set[str] = set()
        for item in raw:
            shortcode = (item.get("shortcode") or "").strip()
            if not shortcode or shortcode in seen:
                continue
            href = (item.get("href") or "").strip()
            m = POST_HREF_RE.match(href)
            if not m:
                continue
            post_type = "reel" if m.group(1) == "reel" else "post"
            url = f"https://www.instagram.com/{m.group(1)}/{shortcode}/"
            seen.add(shortcode)

            user = (item.get("user") or "").strip().lstrip("@")
            user_url = (
                f"https://www.instagram.com/{user}/" if user else ""
            )
            caption = (item.get("caption") or "").strip()
            title = caption or (
                f"@{user} on Instagram" if user else f"Instagram {post_type}"
            )

            likes_text = (item.get("likes_text") or "").strip()
            likes = _parse_count(likes_text)
            comments_text = (item.get("comments_text") or "").strip()
            comments = _parse_count(comments_text)

            head_bits: list[str] = []
            if user:
                head_bits.append(f"@{user}")
            head_bits.append(post_type)
            if likes_text:
                head_bits.append(f"{likes_text} likes")
            if comments_text:
                head_bits.append(f"{comments_text} comments")
            snippet = " · ".join(head_bits)
            if caption and caption != title:
                snippet = snippet + " — " + caption

            r = SearchResult(title=title[:200], url=url, snippet=snippet[:400])
            r.user = user                                            # type: ignore[attr-defined]
            r.user_url = user_url                                    # type: ignore[attr-defined]
            r.shortcode = shortcode                                  # type: ignore[attr-defined]
            r.post_type = post_type                                  # type: ignore[attr-defined]
            r.caption = caption                                      # type: ignore[attr-defined]
            r.likes = likes                                          # type: ignore[attr-defined]
            r.likes_text = likes_text                                # type: ignore[attr-defined]
            r.comments = comments                                    # type: ignore[attr-defined]
            r.comments_text = comments_text                          # type: ignore[attr-defined]
            r.source = "instagram"                                   # type: ignore[attr-defined]
            results.append(r)
            if len(results) >= limit:
                break

        return results

    # ------------------------------------------------------------ Google fallback

    def _search_google_fallback(
        self, query: str, limit: int
    ) -> list[SearchResult]:
        """Use GoogleEngine for ``site:instagram.com <query>``.

        We use *two* strategies on the Google results page:

        1. ``GoogleEngine.search()`` returns the structured organic-results
           list. We keep any whose URL matches an Instagram post / reel URL.
        2. Right after, we walk the *full* DOM of the Google results page
           ourselves and collect every anchor whose href matches an
           Instagram post / reel URL. This catches results in the carousel,
           "Top stories" cards, and other layouts that GoogleEngine's
           ``.tF2Cxc``-based extraction misses.

        We narrow the query with ``(inurl:p OR inurl:reel)`` so Google
        biases toward actual post pages instead of profile pages, hashtag
        index pages, or ``/explore/`` landing pages, which can't be
        normalised to a ``/p/<sc>/`` or ``/reel/<sc>/`` URL.
        """
        try:
            google = GoogleEngine(self.page)
        except Exception as e:
            log.warning("[instagram] cannot construct GoogleEngine: %s", e)
            return []

        # Use the hashtag form of the query when possible — that's what
        # Google indexes for IG posts.
        tag = _hashtagify(query)
        google_query_terms: list[str] = ["site:instagram.com"]
        google_query_terms.append("(inurl:/p/ OR inurl:/reel/)")
        if tag:
            google_query_terms.append(f"#{tag}")
        else:
            google_query_terms.append(query)
        google_query = " ".join(google_query_terms)

        try:
            google_results = google.search(google_query, limit=max(limit * 3, 20))
        except Exception as e:
            log.warning("[instagram] google fallback raised: %s", e)
            google_results = []

        log.info(
            "[instagram] google returned %d organic results for %r",
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

        # Strategy 2: scan all anchors on the Google results page for
        # Instagram URLs the structured extractor might have skipped.
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
            log.debug("[instagram] DOM anchor scan raised: %s", e)
            anchors = []

        for a in anchors:
            entry = self._parse_google_url(
                a.get("href", ""), title=a.get("text", "") or "", snippet=""
            )
            if entry:
                candidates.append(entry)

        log.info(
            "[instagram] google fallback total candidates: %d", len(candidates)
        )

        results: list[SearchResult] = []
        seen: set[str] = set()
        for c in candidates:
            shortcode = c["shortcode"]
            if shortcode in seen:
                continue
            seen.add(shortcode)

            post_type = c["post_type"]
            canonical_url = (
                f"https://www.instagram.com/{c['path']}/{shortcode}/"
            )
            # Try to extract the username from the snippet ("@username on
            # Instagram: ..." is the Open Graph title format Google shows).
            snippet_in = c["snippet"]
            title_in = c["title"]
            user = ""
            for source in (title_in, snippet_in):
                m_user = re.search(r"@([A-Za-z0-9_.]{2,30})", source)
                if m_user:
                    user = m_user.group(1)
                    break
            user_url = (
                f"https://www.instagram.com/{user}/" if user else ""
            )

            title = title_in or (
                f"@{user} on Instagram" if user else f"Instagram {post_type}"
            )

            head_bits: list[str] = []
            if user:
                head_bits.append(f"@{user}")
            head_bits.append(post_type)
            merged_snippet = " · ".join(head_bits)
            if snippet_in:
                merged_snippet = merged_snippet + " — " + snippet_in

            new_r = SearchResult(
                title=title[:200], url=canonical_url, snippet=merged_snippet[:400]
            )
            new_r.user = user                                        # type: ignore[attr-defined]
            new_r.user_url = user_url                                # type: ignore[attr-defined]
            new_r.shortcode = shortcode                              # type: ignore[attr-defined]
            new_r.post_type = post_type                              # type: ignore[attr-defined]
            new_r.caption = snippet_in                               # type: ignore[attr-defined]
            new_r.likes = None                                       # type: ignore[attr-defined]
            new_r.likes_text = ""                                    # type: ignore[attr-defined]
            new_r.comments = None                                    # type: ignore[attr-defined]
            new_r.comments_text = ""                                 # type: ignore[attr-defined]
            new_r.source = "google"                                  # type: ignore[attr-defined]
            results.append(new_r)
            if len(results) >= limit:
                break

        return results

    @staticmethod
    def _parse_google_url(
        url: str, title: str = "", snippet: str = ""
    ) -> dict | None:
        """Match a Google-result URL against ``ABS_POST_URL_RE``.

        Also handles Google's ``/url?q=https://www.instagram.com/...``
        redirect wrapper that survives on some layouts.
        """
        if not url:
            return None
        # Some anchors on the Google results page are still wrapped with the
        # legacy /url?q= redirect.
        if "/url?" in url and "instagram.com" in url:
            try:
                qs = urllib.parse.urlparse(url).query
                target = urllib.parse.parse_qs(qs).get("q", [""])[0]
                if target:
                    url = target
            except Exception:
                pass
        m = ABS_POST_URL_RE.match(url)
        if not m:
            return None
        path = m.group(1)
        return {
            "path": path,
            "post_type": "reel" if path == "reel" else "post",
            "shortcode": m.group(2),
            "title": title,
            "snippet": snippet,
        }

    # ------------------------------------------------------------ helpers

    def _dismiss_overlays(self) -> None:
        """Close login modals, cookie banners, and any other guest overlays."""
        # 1) Cookie banner first — it's typically loaded outside the modal.
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
                    log.info("[instagram] dismissed cookie banner (%s)", sel)
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
                    log.info("[instagram] closed login modal (%s)", sel)
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
        """Detect login-wall / sorry-page / ratelimit interstitials."""
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

        # Hard redirects to login / challenge.
        if "instagram.com/accounts/login" in url:
            log.warning("[instagram] login redirect: %s", url)
            self.last_status["block_reason"] = "login_redirect"
            return True
        if "/challenge/" in url:
            log.warning("[instagram] challenge redirect: %s", url)
            self.last_status["block_reason"] = "challenge"
            return True

        # If the page has *no* post anchors and the body is dominated by
        # the login prompt, treat as blocked.
        try:
            has_post_anchor = bool(
                self.page.query_selector('a[href*="/p/"]')
                or self.page.query_selector('a[href*="/reel/"]')
            )
        except Exception:
            has_post_anchor = False
        if not has_post_anchor and (
            "log in to instagram" in body or "log into instagram" in body
        ):
            log.warning("[instagram] login wall (no post anchors)")
            self.last_status["block_reason"] = "login_wall"
            return True

        for phrase in BLOCK_PHRASES:
            if phrase in title:
                log.warning("[instagram] block phrase in title: %r", phrase)
                self.last_status["block_reason"] = phrase
                return True

        # Ratelimit / "please wait a few minutes" page.
        if "please wait a few minutes" in body and not has_post_anchor:
            log.warning("[instagram] ratelimited (please wait a few minutes)")
            self.last_status["block_reason"] = "ratelimit"
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

    def _wait_for_results(self, timeout_ms: int = 8000) -> bool:
        """Wait for at least one post / reel anchor to attach."""
        deadline = time.time() + timeout_ms / 1000.0
        try:
            self.page.wait_for_function(
                """
                () => {
                  const anchors = document.querySelectorAll('a[href]');
                  for (const a of anchors) {
                    const href = a.getAttribute('href') || '';
                    if (/^\\/(p|reel)\\/[A-Za-z0-9_-]+/.test(href)) return true;
                  }
                  return false;
                }
                """,
                timeout=timeout_ms,
            )
            return True
        except Exception as e:
            log.debug("[instagram] wait_for_function timeout: %s", e)
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
            'a[href*="/p/"]',
            'a[href*="/reel/"]',
            "main article a",
            "article a",
            'div[role="dialog"]',
            'svg[aria-label="Like"]',
            'svg[aria-label="Comment"]',
        ):
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        return counts


# ---------------------------------------------------------------- JS
#
# Walks every anchor whose href matches ``/p/<sc>/`` or ``/reel/<sc>/``,
# then resolves the surrounding card/article to dig out the caption,
# author handle, and like / comment counts. We do this in JS rather than
# chained Python selectors to avoid dozens of CDP round-trips per result.
#
# Instagram markup conventions used here (resilient to class-name churn):
#   * Card container : an ancestor ``<article>`` or ``<a>`` with
#                      ``role="link"`` is the closest reliable anchor in
#                      both the hashtag grid and the explore grid.
#   * Caption        : <img alt="..."> on the post image (Instagram puts
#                      the caption / description in the alt text), the
#                      anchor's own aria-label, or surrounding span text.
#   * Author handle  : a sibling <a href="/<user>/"> that is *not* the
#                      post anchor itself; or a nearby span with the
#                      handle text. On the hashtag grid the user is not
#                      always shown — that's fine, ``user`` will be "".
#   * Like / comment : on the post detail card these are inside spans
#                      labelled with svg[aria-label="Like"] /
#                      svg[aria-label="Comment"]; on the grid they are
#                      under a hover overlay with ♥ / 💬 SVGs and
#                      "<span>123</span>" siblings.
_EXTRACT_JS = r"""
() => {
  const POST_RE = /^\/(p|reel)\/([A-Za-z0-9_-]+)/;
  const USER_RE = /^\/([A-Za-z0-9_.]{2,30})\/?$/;
  const RESERVED = new Set([
    "explore", "p", "reel", "reels", "stories", "accounts", "direct",
    "about", "developer", "legal", "press", "api", "emails", "web",
    "graphql", "locations", "tags", "challenge", "igtv"
  ]);

  const text = (el) => (el ? (el.innerText || el.textContent || "").trim() : "");

  const findCard = (a) => {
    return (
      a.closest('article') ||
      a.closest('[role="link"]') ||
      a.closest('div[class*="Grid"]') ||
      a.parentElement
    );
  };

  const captionFromCard = (card, a) => {
    if (!card) return "";
    // Instagram puts the post description in <img alt="...">.
    const img = card.querySelector('img[alt]');
    if (img) {
      const alt = (img.getAttribute('alt') || '').trim();
      // Skip generic "Photo by X" / "Photo shared by X" alt text without
      // any caption content — those alts don't carry any useful info.
      if (alt && alt.length > 4) return alt;
    }
    // aria-label on the anchor (used on some layouts).
    const aria = (a.getAttribute('aria-label') || '').trim();
    if (aria && aria.length > 4) return aria;
    // Inner text of the anchor — typically empty on the grid but useful
    // on the explore landing page.
    const at = text(a);
    if (at && at.length > 4) return at;
    // Caption span on the post detail page.
    const cap = card.querySelector('h1, span[dir="auto"], div[dir="auto"]');
    if (cap) {
      const t = text(cap);
      if (t && t.length > 4) return t;
    }
    return "";
  };

  const userFromCard = (card, postAnchor) => {
    if (!card) return "";
    // Look for any anchor that points to a profile (one path segment) and
    // is not the post anchor itself.
    const candidates = card.querySelectorAll('a[href]');
    for (const ca of candidates) {
      if (ca === postAnchor) continue;
      const href = ca.getAttribute('href') || '';
      const m = href.match(USER_RE);
      if (!m) continue;
      const user = m[1];
      if (RESERVED.has(user.toLowerCase())) continue;
      // Many user anchors have the handle as their text too — prefer that
      // when available (cheaper than parsing the href slug).
      const t = text(ca);
      if (t && /^[@A-Za-z0-9_.]{2,32}$/.test(t)) {
        return t.replace(/^@/, '');
      }
      return user;
    }
    return "";
  };

  const countFromLabel = (card, label) => {
    if (!card) return "";
    // Hover overlay on grid cards — Like / Comment are <li> siblings of
    // the SVG with the relevant aria-label.
    const svgs = card.querySelectorAll(`svg[aria-label="${label}"]`);
    for (const svg of svgs) {
      // Walk up to find the sibling that holds the count.
      let p = svg.parentElement;
      while (p && p !== card) {
        const t = text(p);
        if (t) {
          // First numeric token wins.
          const m = t.match(/\d[\d.,KMBkmb]*/);
          if (m) return m[0];
        }
        p = p.parentElement;
      }
    }
    // Fallback: look for "<count> likes" / "<count> comments" runs.
    const allText = text(card);
    if (allText) {
      const re = new RegExp(
        `(\\d[\\d.,KMBkmb]*)\\s+${label.toLowerCase()}s?`, 'i'
      );
      const m = allText.match(re);
      if (m) return m[1];
    }
    return "";
  };

  const out = [];
  const seen = new Set();
  const anchors = document.querySelectorAll('a[href]');

  for (const a of anchors) {
    const href = a.getAttribute('href') || '';
    const m = href.match(POST_RE);
    if (!m) continue;
    const path = m[1];
    const shortcode = m[2];
    if (seen.has(shortcode)) continue;
    seen.add(shortcode);

    const card = findCard(a);
    const caption = captionFromCard(card, a);
    const user = userFromCard(card, a);
    const likes_text = countFromLabel(card, "Like");
    const comments_text = countFromLabel(card, "Comment");

    out.push({
      href: "/" + path + "/" + shortcode + "/",
      shortcode: shortcode,
      user: user,
      caption: caption,
      likes_text: likes_text,
      comments_text: comments_text,
    });
  }

  return out;
}
"""
