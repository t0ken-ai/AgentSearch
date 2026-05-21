"""Wolfram Alpha computational knowledge engine adapter.

Wolfram|Alpha (https://www.wolframalpha.com) returns *computed* answers to
natural-language and math queries. Its UI is very different from a normal
web search engine:

- Results aren't "links" — they're a sequence of **pods**, each a self-
  contained block of computed information (e.g. ``Input interpretation``,
  ``Result``, ``Population history``, ``Comparisons``...).
- Each pod has a heading (``h2``) and a body that is usually a server-
  rendered PNG of the math typesetting **plus** a hidden plaintext
  representation in the image's ``alt`` attribute (so screen readers and
  scrapers can read it).
- The page is a heavy single-page React/Webpack app — pods are lazy-
  loaded and stream in over a few seconds via XHR. So:
    * ``goto`` only fetches the JS shell.
    * We have to wait for the pod containers to mount.
    * Then we need to wait a bit longer for the plaintext alt-text to
      populate (the ``<img>`` swap-in lags the ``<section>`` mount).

We surface each pod as a :class:`SearchResult`:
    title   = pod heading (e.g. ``"Result"``)
    url     = the public Wolfram|Alpha permalink to this query
    snippet = the pod's plaintext content (image alt + text nodes,
              de-duplicated and trimmed)

The first pod is usually ``Input interpretation`` and the second is
typically the headline ``Result``; downstream callers get the original
ordering so they can pick the most relevant pod themselves.
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

WOLFRAM_BASE = "https://www.wolframalpha.com"

# Pod containers, in priority order. Wolfram has reshuffled the markup a few
# times; we try the most stable hooks first and fall back to the generic
# "section with an h2" rule.
POD_SELECTORS = [
    'section[data-pod-id]',
    'section[aria-labelledby]',
    'main section:has(h2)',
    'div[data-pod-id]',
    'section.pod',
    'section',
]

# Phrases that indicate Wolfram blocked / failed us.
BLOCK_PHRASES = [
    "access denied",
    "403 forbidden",
    "request blocked",
    "captcha",
    "unusual traffic",
    "verify you are human",
]

# Phrases shown when Wolfram could not understand the query.
NO_RESULT_PHRASES = [
    "wolfram|alpha doesn't understand your query",
    "wolfram|alpha doesn't know how to compute an answer",
    "no short answer available",
]

# The JS-disabled splash. If we still see this after waiting, our stealth
# preset somehow disabled JS, or the page never booted.
JS_DISABLED_PHRASES = [
    "wolfram|alpha doesn't run without javascript",
]

# Pod titles that are pure UI clutter — drop them from the result list so
# tests / agents see only the actual computed pods.
SKIP_POD_TITLES = {
    "step-by-step solution",
    "step-by-step solutions",
    "related queries",
    "related links",
    "share",
    "open code",
    "download page",
    "history",
    "examples",
    "favorites",
}

_WS_RE = re.compile(r"\s+")
_TRAILING_NOISE_RE = re.compile(
    r"\s*(?:\(|\[)?\s*(?:enlarge|copyable plaintext|sources|history|"
    r"open code|step-by-step solution[s]?)\b.*$",
    re.IGNORECASE,
)


def _normalize(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\xa0", " ")
    text = _WS_RE.sub(" ", text).strip()
    return text


class WolframEngine(BaseEngine):
    """Wolfram|Alpha computational adapter.

    A "search" here means: submit ``query`` to Wolfram, wait for pods to
    render, and return one :class:`SearchResult` per pod.
    """

    name = "wolfram"
    max_retries = 2

    # How long to wait for the React app to mount + pods to populate.
    POD_WAIT_TIMEOUT_MS = 25000
    # Small soft wait after pods appear so the lazy <img alt> attributes
    # have time to populate. Wolfram streams pods, so even after the first
    # section mounts the plaintext alt text may lag by a beat.
    POD_SETTLE_S = (2.0, 3.5)
    # Cap the number of polling cycles when waiting for content to stabilize.
    STABILIZATION_POLLS = 6
    STABILIZATION_INTERVAL_S = 0.6

    SNIPPET_MAX = 600

    def __init__(self, page):
        super().__init__(page)
        # Diagnostics for tests / callers.
        self.last_status: dict = {}

    # ---------------------------------------------------------------- search

    def _do_search(self, query: str, limit: int = 10) -> list[SearchResult]:
        q = urllib.parse.quote_plus(query)
        url = f"{WOLFRAM_BASE}/input?i={q}"
        log.info("[wolfram] navigating to %s", url)

        if not safe_goto(self.page, url, timeout=45000):
            return []

        # Wolfram needs a real human-ish pause for the React app to boot.
        human_delay(1.5, 2.5)

        # Wait for any pod-shaped element to mount.
        if not self._wait_for_pods():
            log.warning("[wolfram] no pod containers ever mounted")
            self._dump_status()
            return []

        # Wait for image alt text / pod content to settle (lazy image swap-in).
        self._wait_for_content_stable()

        self._human_hints()
        self._dump_status()

        if self._is_blocked():
            return []

        results = self._extract_pods(url, limit)
        log.info("[wolfram] extracted %d pods for %r", len(results), query)
        return results

    # ----------------------------------------------------------- diagnostics

    def selector_counts(self) -> dict[str, int]:
        """Return how many elements each selector matches (for tests)."""
        counts: dict[str, int] = {}
        for sel in POD_SELECTORS:
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        for sel in ("section h2", "main img[alt]", "main"):
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        return counts

    def _dump_status(self) -> None:
        try:
            url = self.page.url or ""
        except Exception:
            url = ""
        try:
            title = self.page.title() or ""
        except Exception:
            title = ""
        try:
            body = self.page.inner_text("body")
        except Exception:
            body = ""
        self.last_status = {
            "url": url,
            "title": title,
            "body_len": len(body or ""),
        }

    # ---------------------------------------------------------------- waits

    def _wait_for_pods(self) -> bool:
        """Wait until at least one pod-shaped container is in the DOM."""
        # Try the most specific selectors first (these only ever appear when
        # the React app has fully booted) and progressively relax.
        for sel in (
            'section[data-pod-id]',
            'section[aria-labelledby]',
            'main section:has(h2)',
        ):
            try:
                self.page.wait_for_selector(
                    sel, timeout=self.POD_WAIT_TIMEOUT_MS, state="attached"
                )
                log.info("[wolfram] pod container appeared via %r", sel)
                return True
            except Exception as e:
                log.debug("[wolfram] selector %r missed: %s", sel, e)
                continue

        # Last resort: any section in main.
        try:
            self.page.wait_for_selector(
                "main section", timeout=5000, state="attached"
            )
            log.info("[wolfram] pod container appeared via 'main section' fallback")
            return True
        except Exception:
            return False

    def _wait_for_content_stable(self) -> None:
        """Poll until pod count + total inner text length stops changing.

        Wolfram lazy-loads pods as the compute engine streams results in;
        we want to give that flush a chance to finish before we extract.
        """
        prev_sig = (-1, -1)
        same_count = 0
        for _ in range(self.STABILIZATION_POLLS):
            try:
                pods = self.page.query_selector_all('section[data-pod-id]')
                if not pods:
                    pods = self.page.query_selector_all('main section:has(h2)')
                npods = len(pods)
                try:
                    body_len = len(self.page.inner_text("main") or "")
                except Exception:
                    body_len = 0
                sig = (npods, body_len)
            except Exception:
                sig = (-1, -1)

            if sig == prev_sig:
                same_count += 1
                if same_count >= 2 and sig[0] > 0:
                    break
            else:
                same_count = 0
            prev_sig = sig
            time.sleep(self.STABILIZATION_INTERVAL_S)

        # Even after stabilization, give the alt-text image swap a beat.
        human_delay(*self.POD_SETTLE_S)

    # ---------------------------------------------------------------- humans

    def _human_hints(self) -> None:
        """Light human-like activity so the React app emits scroll-triggered pods."""
        try:
            self.page.mouse.move(
                random.randint(120, 600),
                random.randint(120, 400),
                steps=8,
            )
        except Exception:
            pass
        try:
            self.page.evaluate(
                "() => window.scrollBy(0, Math.floor(Math.random()*400) + 200)"
            )
        except Exception:
            pass
        time.sleep(random.uniform(0.4, 0.9))
        try:
            self.page.evaluate("() => window.scrollTo(0, 0)")
        except Exception:
            pass

    # --------------------------------------------------------------- detect

    def _is_blocked(self) -> bool:
        try:
            body = (self.page.inner_text("body") or "").lower()
        except Exception:
            body = ""
        try:
            title = (self.page.title() or "").lower()
        except Exception:
            title = ""

        for phrase in BLOCK_PHRASES:
            if phrase in body or phrase in title:
                log.warning("[wolfram] block phrase detected: %r", phrase)
                self.last_status["block_reason"] = phrase
                return True
        for phrase in JS_DISABLED_PHRASES:
            if phrase in body:
                log.warning("[wolfram] JS-disabled splash still showing")
                self.last_status["block_reason"] = "js_disabled_splash"
                return True
        for phrase in NO_RESULT_PHRASES:
            if phrase in body:
                log.info("[wolfram] no-results page: %r", phrase)
                self.last_status["block_reason"] = f"no_results:{phrase}"
                # No-results is not a block; just return [].
                return False
        return False

    # ------------------------------------------------------------ extraction

    def _extract_pods(self, query_url: str, limit: int) -> list[SearchResult]:
        """Walk the pod DOM and turn each pod into a SearchResult."""
        # Find the best pod-container selector that actually has results.
        items = []
        chosen = None
        for sel in POD_SELECTORS:
            try:
                items = self.page.query_selector_all(sel)
            except Exception:
                items = []
            # Filter to pods that contain at least one h2 — that distinguishes
            # real pods from layout sections (header, footer, sidebar).
            items = [el for el in items if self._has_heading(el)]
            if items:
                chosen = sel
                break

        if not items:
            log.info("[wolfram] no pod elements found by any selector")
            return []

        log.info("[wolfram] using selector %r (%d candidates)", chosen, len(items))

        results: list[SearchResult] = []
        seen_titles: set[str] = set()

        for el in items:
            title = self._pod_title(el)
            if not title:
                continue
            tlower = title.lower()
            if tlower in SKIP_POD_TITLES:
                continue
            # Wolfram sometimes renders the same pod twice (mobile / desktop
            # variants); de-dupe on the heading.
            if tlower in seen_titles:
                continue
            seen_titles.add(tlower)

            snippet = self._pod_snippet(el)
            if not snippet and tlower not in {"input interpretation", "input"}:
                # Skip pods with no extractable text (pure-image plots etc.)
                # unless it's the input echo, which is short anyway.
                continue

            results.append(
                SearchResult(
                    title=title,
                    url=query_url,
                    snippet=snippet,
                )
            )
            if len(results) >= limit:
                break

        return results

    @staticmethod
    def _has_heading(el) -> bool:
        try:
            return el.query_selector("h2") is not None
        except Exception:
            return False

    @staticmethod
    def _pod_title(el) -> str:
        # Prefer the first h2 (the pod name).
        try:
            h = el.query_selector("h2")
            if h:
                t = _normalize(h.inner_text() or "")
                # Some Wolfram h2s contain a trailing "Enlarge" / "Copy" affordance.
                t = _TRAILING_NOISE_RE.sub("", t)
                return t
        except Exception:
            pass
        # Fall back to aria-labelledby.
        try:
            aria_id = el.get_attribute("aria-labelledby")
            if aria_id:
                return _normalize(aria_id)
        except Exception:
            pass
        return ""

    def _pod_snippet(self, el) -> str:
        """Build a plaintext snippet for a pod.

        Strategy, in order:
        1. Concatenate ``alt`` attributes of every ``<img>`` inside the pod —
           Wolfram puts the textual representation of the rendered formula
           there (e.g. ``"China | population | 1.402 billion people (world rank: 1st) (2024 estimate)"``).
        2. Add the pod's plain text content (after stripping the heading)
           for pods that are pure HTML (like "Input interpretation" or
           textual answers).
        3. De-dupe and trim.
        """
        parts: list[str] = []
        seen: set[str] = set()

        # 1. Image alt text — the Wolfram plaintext render.
        try:
            imgs = el.query_selector_all("img[alt]")
        except Exception:
            imgs = []
        for img in imgs:
            try:
                alt = img.get_attribute("alt") or ""
            except Exception:
                alt = ""
            alt = _normalize(alt)
            if not alt:
                continue
            # Skip generic decorative alts.
            if alt.lower() in {"plot", "graph", "image", "wolfram|alpha"}:
                continue
            key = alt.lower()
            if key in seen:
                continue
            seen.add(key)
            parts.append(alt)

        # 2. Plain-text fallback for textual pods.
        try:
            text = el.inner_text() or ""
        except Exception:
            text = ""
        text = _normalize(text)
        if text:
            # Strip the heading text we already used as `title`.
            try:
                heading = _normalize(el.query_selector("h2").inner_text() or "")
            except Exception:
                heading = ""
            if heading and text.lower().startswith(heading.lower()):
                text = text[len(heading):].strip(" :|,-—\u2013")
            text = _TRAILING_NOISE_RE.sub("", text).strip()
            if text and text.lower() not in seen:
                seen.add(text.lower())
                parts.append(text)

        snippet = " | ".join(p for p in parts if p)
        if len(snippet) > self.SNIPPET_MAX:
            snippet = snippet[: self.SNIPPET_MAX].rstrip() + "…"
        return snippet
