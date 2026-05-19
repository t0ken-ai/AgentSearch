"""Quora search adapter with login-wall dismissal and site: fallbacks.

Quora's web search at ``https://www.quora.com/search?q=<q>`` aggressively
wraps the page in a forced-login modal (``div[role="dialog"]``) that
covers the result list within a couple of seconds of page load. The page
itself still renders a usable Q&A list under that overlay, so we just
need to clear / hide the dialog before scraping. To stay robust we layer
three modes (mirroring ``blackhatworld.py``):

1. **quora_direct** — Navigate to ``quora.com/search?q=<q>``. Repeatedly
   try to dismiss the login modal (Escape key + close buttons + DOM
   evaluate to hide overlay), then scrape rendered Q&A cards. Selectors
   vary across Quora's React rewrites so we probe a list of known
   candidates and record which one matched on ``last_status['selector']``.

2. **google_site** — Fallback through Google with
   ``site:quora.com <query>``. Handles the consent dialog and
   ``/sorry/`` CAPTCHA detection identically to ``blackhatworld.py``.

3. **ddg_site** — Last-resort fallback through the HTML-only
   DuckDuckGo endpoint with ``site:quora.com <query>``. We can pull
   title + URL + a coarse snippet, but no upvote count or answer text
   beyond what Google chose to surface.

Each mode short-circuits on success.

``SearchResult`` (see ``base.py``) carries ``title`` / ``url`` /
``snippet`` / ``score``. To preserve Quora-specific metadata:

* ``score`` holds the integer upvote count when extractable, ``None``
  otherwise (Quora often hides upvotes for non-logged-in users).
* ``snippet`` is the answer preview text Quora exposes on the search
  card (or the answer text Google / DDG surfaces in the snippet
  position).
* Every returned ``SearchResult`` has the following attributes set
  dynamically:

  - ``question``  (str) — same as ``title``; kept for callers that want
    to differentiate question vs answer text.
  - ``answer``    (str) — answer body preview, may be empty.
  - ``author``    (str) — answer author display name.
  - ``upvotes``   (int | None) — same as ``score``.
  - ``kind``      (str) — ``"question"`` or ``"answer"`` based on URL
    pattern (``/answer/`` segment present → ``answer``).

Diagnostics
-----------

* ``engine.last_status`` — ``mode``, ``url``, ``title``, ``body_len``,
  optional ``selector`` / ``block_reason`` / ``count`` /
  ``modal_dismissed`` (bool flag set after we run the dismissal step).
* ``engine.selector_counts()`` — per-selector counts useful across
  all three modes so test scripts can show why parsing missed.
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

QUORA_HOME = "https://www.quora.com"

# ---- quora_direct ----------------------------------------------------------

# Q&A card containers on quora.com/search, in priority order. Quora's
# React app has gone through several rewrites; the current default is
# the ``q-box`` system but we keep older variants as fallbacks.
DIRECT_RESULT_SELECTORS = [
    'div.q-box.dom_annotate_search_result',          # current
    'div.q-box.qu-borderAll.qu-borderRadius--small', # card variant
    'div.q-box.spacing_log_question',                # legacy
    'div.q-box.dom_annotate_question_answer_item',   # answer-card variant
    'div.pagedlist_item',                            # very old layout
    'div[class*="SearchResult"]',                    # generic class probe
    'div[role="article"]',                           # ARIA fallback
]

# Login-modal close button selectors. Quora rotates the exact
# data-attributes on these buttons, so we keep a generous list.
MODAL_CLOSE_SELECTORS = [
    'button[aria-label="Close"]',
    'button[aria-label="close"]',
    'div[role="dialog"] button[aria-label*="Close" i]',
    'div[role="dialog"] button[aria-label*="Dismiss" i]',
    'div[role="dialog"] svg[aria-label*="Close" i]',
    'button.qu-borderNone[aria-label*="close" i]',
    'div.q-box.qu-zIndex--modal button[aria-label*="close" i]',
    # Last-resort: any button inside the topmost dialog whose text is
    # an "x" glyph or "Close".
    'div[role="dialog"] button',
]

# Phrases that indicate Quora / Cloudflare blocked us.
BLOCK_PHRASES = [
    "verify you are human",
    "verifying you are human",
    "checking your browser",
    "just a moment",
    "cf-browser-verification",
    "attention required",
    "access denied",
    "rate limit",
    "too many requests",
    "human verification",
    "enable javascript and cookies",
    "request unsuccessful",
    "are you a robot",
    "unusual activity",
]

# ---- Google site: ----------------------------------------------------------

GOOGLE_DOMAINS = [
    "https://www.google.com",
    "https://www.google.co.uk",
    "https://www.google.ca",
]

GOOGLE_RESULT_SELECTORS = [
    "div.g",
    ".tF2Cxc",
    "div[data-sokoban-container]",
    "div.MjjYud",
]

GOOGLE_CONSENT_BUTTON_SELECTORS = [
    "button#L2AGLb",
    "button[aria-label*='Accept all' i]",
    "button[aria-label*='Accept All' i]",
    "button[aria-label*='Akzeptieren' i]",
    "button[aria-label*='Accepter' i]",
    "button[aria-label*='Aceptar' i]",
    "form[action*='consent'] button",
    "div[role='dialog'] button",
]

GOOGLE_BLOCK_PHRASES = [
    "unusual traffic",
    "our systems have detected",
    "before you continue",
    "to continue, please type",
    "captcha",
    "i'm not a robot",
    "automated queries",
    "sending automated requests",
]

# ---- DuckDuckGo HTML fallback ---------------------------------------------

DDG_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"

# Rough cap on how many DOM cards we'll inspect per page.
MAX_CARDS_TO_SCAN = 80


# ----------------------------------------------------------------------------


def _abs_quora(href: str) -> str:
    """Normalize a relative Quora URL to an absolute one."""
    if not href:
        return href
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return QUORA_HOME + href
    return QUORA_HOME + "/" + href


def _strip_url_query(href: str) -> str:
    """Drop tracking params (``?ch=...``, ``?share=...``) for de-duping."""
    if not href:
        return href
    return href.split("?", 1)[0].split("#", 1)[0]


def _parse_int(text: str) -> int | None:
    """Parse an upvote count string ('123', '1.2K', '4.5M', '1,234')."""
    if not text:
        return None
    t = text.strip().lower().replace(",", "")
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*([km])?", t)
    if not m:
        return None
    num = float(m.group(1))
    suffix = m.group(2)
    if suffix == "k":
        num *= 1_000
    elif suffix == "m":
        num *= 1_000_000
    try:
        return int(num)
    except (ValueError, OverflowError):
        return None


def _clean_google_redirect(href: str) -> str:
    """Strip Google's /url?q=... redirect wrapper if present."""
    if not href:
        return href
    if href.startswith("/url?"):
        try:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            return qs.get("q", [href])[0]
        except Exception:
            return href
    return href


def _clean_ddg_redirect(href: str) -> str:
    """Decode DuckDuckGo's /l/?uddg=<encoded> wrapper."""
    if not href:
        return href
    if "uddg=" in href:
        try:
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            return qs.get("uddg", [href])[0]
        except Exception:
            return href
    return href


_UPVOTE_RE = re.compile(
    r"(\d[\d,.]*\s*[KMkm]?)\s*(?:upvote|up\s*vote)s?",
    re.I,
)


def _extract_upvotes(text: str) -> int | None:
    """Pull an upvote count out of free-form card text."""
    if not text:
        return None
    m = _UPVOTE_RE.search(text)
    if not m:
        return None
    return _parse_int(m.group(1))


def _looks_like_quora_question_url(href: str) -> bool:
    """Heuristic: is this a Quora question / answer URL?"""
    if not href:
        return False
    path = urllib.parse.urlparse(href).path or ""
    if not path or path == "/":
        return False
    # Reject obvious non-question paths.
    bad_prefixes = (
        "/search",
        "/login",
        "/signup",
        "/topic/",
        "/profile/",
        "/about",
        "/career",
        "/help",
        "/spaces",
        "/q/",  # space short-link, not a question
    )
    for bad in bad_prefixes:
        if path.startswith(bad):
            return False
    # A real Quora question slug has multiple words separated by hyphens
    # and usually a "How-do-I" / "What-is" / etc. shape. A pure single
    # short token is more likely a topic. Require at least one hyphen.
    first_seg = path.lstrip("/").split("/")[0]
    if "-" not in first_seg:
        return False
    return True


def _classify_kind(href: str) -> str:
    """Return ``"answer"`` if the URL has a /answer/ segment, else ``"question"``."""
    if not href:
        return "question"
    path = urllib.parse.urlparse(href).path or ""
    return "answer" if "/answer/" in path else "question"


def _compose_snippet(answer: str, author: str, upvotes: int | None) -> str:
    """Render snippet combining answer text + author + upvote count."""
    parts: list[str] = []
    if author:
        parts.append(f"by {author}")
    if upvotes is not None:
        parts.append(f"{upvotes:,} upvotes")
    head = " · ".join(parts)
    if head and answer:
        return f"{head} — {answer}"
    return head or answer


def _attach_extras(
    r: SearchResult,
    *,
    question: str,
    answer: str,
    author: str,
    upvotes: int | None,
    kind: str,
) -> SearchResult:
    r.question = question
    r.answer = answer
    r.author = author
    r.upvotes = upvotes
    r.kind = kind
    return r


# ----------------------------------------------------------------------------


class QuoraSearchEngine(BaseEngine):
    """Search Quora for questions and answers."""

    name = "quora"
    max_retries = 3

    _MODE_ORDER: tuple[str, ...] = ("quora_direct", "google_site", "ddg_site")

    def __init__(self, page):
        super().__init__(page)
        # Diagnostics for callers / tests.
        self.last_status: dict = {}
        self._last_mode: str = self._MODE_ORDER[0]
        self._pages_fetched: int = 0

    # ------------------------------------------------------------------ search

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        for mode in self._MODE_ORDER:
            try:
                if mode == "quora_direct":
                    results = self._try_quora_direct(query, limit)
                elif mode == "google_site":
                    results = self._try_google_site(query, limit)
                elif mode == "ddg_site":
                    results = self._try_ddg_site(query, limit)
                else:  # pragma: no cover — _MODE_ORDER guards this
                    results = []
            except Exception as e:
                log.warning("[quora] %s raised: %s", mode, e)
                results = []
            if results:
                self._last_mode = mode
                return results
        return []

    # ----------------------------------------------------- quora_direct mode

    def _try_quora_direct(
        self, query: str, limit: int
    ) -> list[SearchResult]:
        """Navigate to quora.com/search?q=... and scrape rendered cards.

        The page wraps itself in a forced-login modal we have to clear
        before parsing. Several techniques are used in sequence:
        Escape key, close-button click, JS removal of overlay nodes.
        """
        # Warm up on the homepage so cookies settle. The home page
        # *also* shows the login modal but our dismissal step handles
        # both.
        if safe_goto(self.page, QUORA_HOME + "/", timeout=20000, retries=1):
            human_delay(1.0, 2.0)
            self._dismiss_login_modal()
            self._human_hints()

        q = urllib.parse.quote(query)
        url = f"{QUORA_HOME}/search?q={q}"
        log.info("[quora] direct search: %s", url)
        if not safe_goto(self.page, url, timeout=30000, retries=1):
            self.last_status = {
                "mode": "quora_direct",
                "error": "goto_failed",
            }
            return []

        self._pages_fetched = 1

        # Wait for at least one candidate card selector to appear; the
        # modal often appears first so we tolerate a wait timeout and
        # try to dismiss the modal anyway.
        for sel in DIRECT_RESULT_SELECTORS:
            try:
                self.page.wait_for_selector(sel, timeout=5000)
                break
            except Exception:
                continue

        # Dismiss modal as soon as it's visible (it appears within ~1-2s).
        human_delay(1.0, 2.0)
        self._dismiss_login_modal()
        self._human_hints()

        # Scroll once to encourage lazy-loaded cards to render. Quora's
        # search renders 5-6 cards initially and lazy-loads more.
        try:
            self.page.evaluate(
                "() => window.scrollBy(0, document.body.scrollHeight * 0.5)"
            )
        except Exception:
            pass
        human_delay(1.0, 2.0)
        # Modal may re-appear after scroll; dismiss again for safety.
        self._dismiss_login_modal()

        if self._is_blocked("quora_direct"):
            return []

        items = []
        used = None
        for sel in DIRECT_RESULT_SELECTORS:
            try:
                items = self.page.query_selector_all(sel)
            except Exception:
                items = []
            if items:
                used = sel
                break
        if not items:
            self.last_status.setdefault("mode", "quora_direct")
            self.last_status["count"] = 0
            return []

        log.info("[quora] direct via %s (%d items)", used, len(items))
        self.last_status["selector"] = used

        results: list[SearchResult] = []
        seen: set[str] = set()
        for r in items[:MAX_CARDS_TO_SCAN]:
            sr = self._extract_direct_item(r)
            if sr is None:
                continue
            key = _strip_url_query(sr.url)
            if key in seen:
                continue
            seen.add(key)
            results.append(sr)
            if len(results) >= limit:
                break

        if results:
            self.last_status["mode"] = "quora_direct"
            self.last_status["count"] = len(results)
        return results

    def _extract_direct_item(self, r) -> SearchResult | None:
        """Pull a single SearchResult out of one DOM container."""
        try:
            full_text = (r.inner_text() or "").strip()
        except Exception:
            full_text = ""

        # Find the question link. Quora puts a primary link wrapping
        # the question title; secondary links point to the author and
        # to the topic. We pick the first <a> whose href looks like a
        # question URL.
        href = ""
        title = ""
        try:
            anchors = r.query_selector_all("a[href]")
        except Exception:
            anchors = []

        anchor_info: list[tuple[str, str]] = []
        for a in anchors:
            try:
                h = a.get_attribute("href") or ""
                t = (a.inner_text() or "").strip()
            except Exception:
                continue
            if not h:
                continue
            h_abs = _abs_quora(h)
            anchor_info.append((h_abs, t))
            if not href and _looks_like_quora_question_url(h_abs):
                href = h_abs
                title = t

        if not href:
            return None

        # Promote a richer title from inside the card if the link
        # captured only a short fragment (or wraps an icon).
        if not title or len(title) < 6:
            try:
                # Quora question titles render in spans with a question
                # mark suffix; pick the longest text that ends in '?'.
                spans = r.query_selector_all("span")
                best = ""
                for s in spans:
                    try:
                        st = (s.inner_text() or "").strip()
                    except Exception:
                        continue
                    if "?" in st and len(st) > len(best) and len(st) < 400:
                        best = st
                if best:
                    title = best
            except Exception:
                pass

        if not title:
            return None

        # Author: many search cards include the answerer's name and
        # link to /profile/<handle>. Pick the first /profile/ anchor.
        author = ""
        for h_abs, t in anchor_info:
            path = urllib.parse.urlparse(h_abs).path or ""
            if path.startswith("/profile/"):
                if t and t.lower() not in {"follow", "following", "..."}:
                    author = t
                    break

        # Answer body preview: try a few Quora-specific selectors that
        # have surfaced across rewrites, then fall back to the card's
        # full text minus the title line.
        answer = ""
        try:
            ans_el = (
                r.query_selector('div.q-text.qu-truncateLines')
                or r.query_selector('div[class*="AnswerText"]')
                or r.query_selector('div.q-box.qu-mt--small')
                or r.query_selector('span.q-text')
            )
            if ans_el:
                ans_text = (ans_el.inner_text() or "").strip()
                # Don't reuse the title as the answer body.
                if ans_text and ans_text != title:
                    answer = ans_text
        except Exception:
            answer = ""
        # Heuristic fallback: remove the title from the card's full
        # text and use the remainder.
        if not answer and full_text and title:
            tail = full_text.replace(title, "", 1).strip()
            # Trim to first 240 chars to keep the snippet bounded.
            if tail:
                answer = tail[:240]

        upvotes = _extract_upvotes(full_text)
        kind = _classify_kind(href)

        sr = SearchResult(
            title=title,
            url=href,
            snippet=_compose_snippet(answer, author, upvotes),
            score=upvotes,
        )
        return _attach_extras(
            sr,
            question=title,
            answer=answer,
            author=author,
            upvotes=upvotes,
            kind=kind,
        )

    # ---------------------------------------------------------- modal handling

    def _dismiss_login_modal(self) -> bool:
        """Try multiple techniques to clear Quora's forced-login modal.

        Returns True if any technique appeared to succeed (reported on
        ``last_status['modal_dismissed']`` for diagnostics).
        """
        dismissed = False

        # 1) Press Escape — works for many React modal libraries.
        try:
            self.page.keyboard.press("Escape")
            human_delay(0.3, 0.6)
            dismissed = True
        except Exception:
            pass

        # 2) Click any close button we can find.
        for sel in MODAL_CLOSE_SELECTORS:
            try:
                btn = self.page.query_selector(sel)
                if btn:
                    try:
                        btn.click(timeout=2000, force=True)
                        log.info("[quora] modal close clicked (%s)", sel)
                        human_delay(0.3, 0.7)
                        dismissed = True
                        break
                    except Exception:
                        continue
            except Exception:
                continue

        # 3) Last resort: forcibly remove any [role=dialog] overlay and
        # restore body scroll. Quora's modal locks scroll via
        # ``overflow: hidden`` on <html>/<body>, so we reset both.
        try:
            removed = self.page.evaluate(
                """
                () => {
                    let n = 0;
                    document.querySelectorAll('div[role="dialog"]').forEach(el => {
                        try { el.remove(); n++; } catch(e) {}
                    });
                    // Some Quora variants render the modal as a
                    // fixed-position div with a known z-index instead
                    // of [role=dialog].
                    document.querySelectorAll('div.qu-zIndex--modal, div.qu-zIndex--overlay').forEach(el => {
                        try { el.remove(); n++; } catch(e) {}
                    });
                    document.documentElement.style.overflow = '';
                    document.body.style.overflow = '';
                    return n;
                }
                """
            )
            if removed and removed > 0:
                log.info("[quora] modal forcibly removed (%d nodes)", removed)
                dismissed = True
        except Exception:
            pass

        if dismissed:
            self.last_status["modal_dismissed"] = True
        return dismissed

    # -------------------------------------------------------- google_site mode

    def _try_google_site(
        self, query: str, limit: int
    ) -> list[SearchResult]:
        domain = random.choice(GOOGLE_DOMAINS)

        if safe_goto(self.page, domain + "/", timeout=20000, retries=1):
            human_delay(1.5, 3.0)
            self._handle_google_consent()
            self._human_hints()

        site_query = f"site:quora.com {query}"
        q = urllib.parse.quote(site_query)
        url = f"{domain}/search?q={q}&hl=en&num={max(limit, 10)}"
        log.info("[quora] google site search: %s", url)
        if not safe_goto(self.page, url, timeout=30000):
            self.last_status = {"mode": "google_site", "error": "goto_failed"}
            return []

        human_delay(2.0, 3.5)
        self._handle_google_consent()
        self._human_hints()

        if self._is_google_blocked():
            return []

        items = []
        used = None
        for sel in GOOGLE_RESULT_SELECTORS:
            try:
                items = self.page.query_selector_all(sel)
            except Exception:
                items = []
            if items:
                used = sel
                break

        if not items:
            log.info("[quora] google: no result selector matched, h3 fallback")
            return self._extract_google_h3(limit)

        log.info(
            "[quora] google using selector %s (%d items)", used, len(items)
        )
        self.last_status["selector"] = used

        results: list[SearchResult] = []
        seen: set[str] = set()
        for r in items[: limit * 4]:
            title_el = r.query_selector("h3")
            link_el = (
                r.query_selector("a[href^='http']")
                or r.query_selector("a[href^='/url']")
            )
            snippet_el = r.query_selector(
                ".VwiC3b, [data-sncf], .lEBKkf, span.aCOpRe"
            )

            try:
                title = (
                    (title_el.inner_text() or "").strip() if title_el else ""
                )
            except Exception:
                title = ""
            try:
                href = link_el.get_attribute("href") if link_el else ""
            except Exception:
                href = ""
            try:
                snippet = (
                    (snippet_el.inner_text() or "").strip()
                    if snippet_el
                    else ""
                )
            except Exception:
                snippet = ""

            href = _clean_google_redirect(href or "")

            if not href or "quora.com" not in href.lower():
                continue
            if not title:
                continue
            if not _looks_like_quora_question_url(href):
                continue

            key = _strip_url_query(href)
            if key in seen:
                continue
            seen.add(key)

            # Try to surface upvote count from the snippet text.
            upvotes = _extract_upvotes(snippet)
            kind = _classify_kind(href)

            sr = SearchResult(
                title=title,
                url=href,
                snippet=snippet,
                score=upvotes,
            )
            _attach_extras(
                sr,
                question=title,
                answer=snippet,
                author="",
                upvotes=upvotes,
                kind=kind,
            )
            results.append(sr)
            if len(results) >= limit:
                break

        if results:
            self.last_status["mode"] = "google_site"
            self.last_status["count"] = len(results)
        return results

    def _extract_google_h3(self, limit: int) -> list[SearchResult]:
        """Fallback when no top-level result container matched: walk h3s."""
        results: list[SearchResult] = []
        seen: set[str] = set()
        try:
            h3s = self.page.query_selector_all("#search h3, #rso h3")
        except Exception:
            h3s = []
        for h3 in h3s[: limit * 3]:
            try:
                title = (h3.inner_text() or "").strip()
            except Exception:
                continue
            try:
                href = self.page.evaluate(
                    "(el) => { let p = el; while(p) { if(p.tagName === 'A' && p.href) return p.href; p = p.parentElement; } return ''; }",
                    h3,
                )
            except Exception:
                href = ""
            href = _clean_google_redirect(href or "")
            if not (
                title
                and href
                and "quora.com" in href.lower()
                and _looks_like_quora_question_url(href)
            ):
                continue
            key = _strip_url_query(href)
            if key in seen:
                continue
            seen.add(key)
            sr = SearchResult(title=title, url=href)
            _attach_extras(
                sr,
                question=title,
                answer="",
                author="",
                upvotes=None,
                kind=_classify_kind(href),
            )
            results.append(sr)
            if len(results) >= limit:
                break
        if results:
            self.last_status["mode"] = "google_site"
            self.last_status["count"] = len(results)
        return results

    def _handle_google_consent(self):
        for sel in GOOGLE_CONSENT_BUTTON_SELECTORS:
            try:
                btn = self.page.query_selector(sel)
                if btn:
                    btn.click(timeout=3000)
                    log.info("[quora] google consent clicked (%s)", sel)
                    human_delay(1, 2)
                    return
            except Exception:
                continue
        try:
            for frame in self.page.frames:
                furl = (frame.url or "").lower()
                if "consent" not in furl:
                    continue
                for sel in GOOGLE_CONSENT_BUTTON_SELECTORS:
                    try:
                        btn = frame.query_selector(sel)
                        if btn:
                            btn.click(timeout=3000)
                            log.info(
                                "[quora] google consent (frame %s) %s",
                                furl,
                                sel,
                            )
                            human_delay(1, 2)
                            return
                    except Exception:
                        continue
        except Exception:
            pass

    def _is_google_blocked(self) -> bool:
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
            "mode": "google_site",
            "url": url,
            "title": title,
            "body_len": len(body),
        }

        if "/sorry/" in url or "sorry" in title:
            self.last_status["block_reason"] = "sorry"
            log.warning("[quora] google sorry page: %r", title)
            return True
        for phrase in GOOGLE_BLOCK_PHRASES:
            if phrase in body[:3000] or phrase in title:
                self.last_status["block_reason"] = phrase
                log.warning("[quora] google block phrase: %r", phrase)
                return True
        return False

    # -------------------------------------------------------- ddg_site mode

    def _try_ddg_site(self, query: str, limit: int) -> list[SearchResult]:
        site_query = f"site:quora.com {query}"
        q = urllib.parse.quote(site_query)
        url = f"{DDG_HTML_ENDPOINT}?q={q}"
        log.info("[quora] ddg site search: %s", url)
        if not safe_goto(self.page, url, timeout=25000, retries=1):
            self.last_status = {"mode": "ddg_site", "error": "goto_failed"}
            return []

        human_delay(1.0, 2.0)
        self._human_hints()

        try:
            url_now = (self.page.url or "").lower()
            title_now = (self.page.title() or "").lower()
            body_now = self.page.inner_text("body").lower()
        except Exception:
            url_now = title_now = body_now = ""

        self.last_status = {
            "mode": "ddg_site",
            "url": url_now,
            "title": title_now,
            "body_len": len(body_now),
            "selector": ".result",
        }

        results: list[SearchResult] = []
        seen: set[str] = set()
        try:
            items = self.page.query_selector_all(".result")
        except Exception:
            items = []
        log.info("[quora] ddg got %d .result items", len(items))

        for r in items[: limit * 4]:
            title_el = r.query_selector(".result__a")
            snippet_el = r.query_selector(".result__snippet")
            try:
                title = (
                    (title_el.inner_text() or "").strip() if title_el else ""
                )
                href = (
                    (title_el.get_attribute("href") or "")
                    if title_el
                    else ""
                )
                snippet = (
                    (snippet_el.inner_text() or "").strip()
                    if snippet_el
                    else ""
                )
            except Exception:
                continue
            href = _clean_ddg_redirect(href)
            if not title or not href:
                continue
            if "quora.com" not in href.lower():
                continue
            if not _looks_like_quora_question_url(href):
                continue

            key = _strip_url_query(href)
            if key in seen:
                continue
            seen.add(key)

            upvotes = _extract_upvotes(snippet)
            kind = _classify_kind(href)
            sr = SearchResult(
                title=title,
                url=href,
                snippet=snippet,
                score=upvotes,
            )
            _attach_extras(
                sr,
                question=title,
                answer=snippet,
                author="",
                upvotes=upvotes,
                kind=kind,
            )
            results.append(sr)
            if len(results) >= limit:
                break

        if results:
            self.last_status["count"] = len(results)
        return results

    # -------------------------------------------------------- block detection

    def _is_blocked(self, mode: str) -> bool:
        """Detect Cloudflare / Quora interstitials and rate-limits."""
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

        # Preserve modal_dismissed flag if present, since this method
        # rebuilds last_status.
        prev_modal = self.last_status.get("modal_dismissed")
        self.last_status = {
            "mode": mode,
            "url": url,
            "title": title,
            "body_len": len(body),
        }
        if prev_modal is not None:
            self.last_status["modal_dismissed"] = prev_modal

        head = body[:3000]
        for phrase in BLOCK_PHRASES:
            if phrase in head or phrase in title:
                self.last_status["block_reason"] = phrase
                log.warning("[quora] block phrase detected: %r", phrase)
                return True
        return False

    # -------------------------------------------------------------- diagnostics

    def selector_counts(self) -> dict[str, int]:
        """Per-selector counts; safe to call regardless of last_mode."""
        counts: dict[str, int] = {}
        probe = [
            'div.q-box.dom_annotate_search_result',
            'div.q-box.qu-borderAll.qu-borderRadius--small',
            'div.q-box.spacing_log_question',
            'div.q-box.dom_annotate_question_answer_item',
            'div.pagedlist_item',
            'div[role="article"]',
            'div[role="dialog"]',
            'a[href*="/profile/"]',
            'span.q-text',
            "#search h3",
            "#rso h3",
            ".result__a",
            ".result__snippet",
        ]
        for sel in probe:
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        return counts

    # ------------------------------------------------------------------ helpers

    def _human_hints(self):
        """Light human-like activity: mouse move + small scroll."""
        try:
            self.page.mouse.move(
                random.randint(100, 400),
                random.randint(100, 400),
                steps=10,
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
