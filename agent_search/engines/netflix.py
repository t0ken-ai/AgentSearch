"""Netflix search adapter.

Netflix's in-site search endpoint (``netflix.com/search?q=...``) is gated
behind authentication: hitting it as a guest redirects to the sign-up /
login page and renders no catalogue data. There is no public, unauthenticated
JSON endpoint for the catalogue either — every reliable scraping path goes
through the public *title* pages, which Google indexes.

This engine therefore has two completely separate code paths and the
public ``search()`` method picks the first one that returns anything useful:

1. **Direct path** — ``https://www.netflix.com/search?q=<query>``.
   Almost always redirects guests to ``/login`` (or in some regions
   ``/signup``). If by some chance the page does render title cards
   (e.g. when a Netflix login cookie has been imported into the browser
   profile), we parse them. In practice this path mostly serves as a
   "is this user logged in?" probe — when it fails, we drop straight to
   the Google fallback.

2. **Google ``site:netflix.com/title`` fallback** — when the direct path
   returns nothing, we drive :class:`GoogleEngine` on the same page and
   keep only the hits whose URL points at a Netflix title page
   (``/title/<numeric_id>``). This is the same trick the Instagram and
   TikTok adapters use.

Each :class:`SearchResult` carries:

* ``netflix_id``  – the numeric id from ``/title/<id>``
* ``type``        – ``"movie"``, ``"series"``, or ``""`` if unknown
* ``year``        – integer release year, or ``None``
* ``rating``      – maturity rating (``"TV-MA"``, ``"PG-13"``, ...) or ``""``
* ``source``      – ``"netflix"`` (direct) or ``"google"`` (fallback)
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


# Anchors / URLs that point at a Netflix title page.
TITLE_HREF_RE = re.compile(r"^/title/(\d+)/?")
ABS_TITLE_URL_RE = re.compile(
    r"https?://(?:www\.)?netflix\.com/(?:[a-z]{2}(?:-[a-z]{2})?/)?title/(\d+)"
)

# Phrases that strongly suggest "this is a TV series", in English / Spanish /
# French / German / Portuguese / Italian (Google snippet language varies by
# region even when the query is English).
SERIES_PHRASES = [
    "tv series", "tv show", "miniseries", "limited series",
    "tv mini-series", "tv mini series", "series", "season", "seasons",
    "episode", "episodes",
    "série", "séries",                          # fr / pt
    "serie", "serien",                          # de / es / it
    "temporada", "temporadas",                  # es / pt
    "saison", "saisons",                        # fr
    "staffel", "staffeln",                      # de
    "stagione", "stagioni",                     # it
]
MOVIE_PHRASES = [
    "movie", "film", "feature film", "película", "filme",
]

# Maturity ratings we recognise. Netflix surfaces a mix of US TV / MPAA /
# regional rating systems on its title pages; the snippet usually contains
# one of these tokens at the start.
RATING_RE = re.compile(
    r"\b("
    r"TV-Y|TV-Y7(?:-FV)?|TV-G|TV-PG|TV-14|TV-MA"     # US TV
    r"|G|PG|PG-13|R|NC-17|NR|UR"                     # US MPAA
    r"|U|UA|A"                                       # IN
    r"|7\+|13\+|16\+|18\+"                           # NL / NO / etc.
    r"|U/A 7\+|U/A 13\+|U/A 16\+"                    # IN combined
    r"|FSK\s*(?:0|6|12|16|18)"                       # DE
    r"|Maturity Rating:?\s*[A-Z0-9+\-]+"             # generic
    r")\b"
)
YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")

# Block / login-wall phrases on the direct path.
BLOCK_PHRASES = [
    "sign in",
    "create an account",
    "get started",
    "unlimited movies, tv shows",
    "ready to watch",
    "join netflix",
    "iniciar sesión",         # es
    "se connecter",           # fr
    "anmelden",               # de
]

# Selectors used to detect whether the search page actually rendered any
# catalogue cards (only happens when the user is logged in).
RESULT_PRESENCE_SELECTORS = [
    'a[href*="/watch/"]',
    'a[href*="/title/"]',
    "[data-uia*='title']",
    ".title-card",
]


def _slugify(query: str) -> str:
    """URL-encode a query for the Netflix /search endpoint."""
    return urllib.parse.quote(query.strip())


class NetflixEngine(BaseEngine):
    """Search the Netflix catalogue, with a Google fallback for guests."""

    name = "netflix"
    max_retries = 2  # Google fallback adds its own resilience.

    SEARCH_URL = "https://www.netflix.com/search?q={q}"
    HOMEPAGE_URL = "https://www.netflix.com/"

    def __init__(self, page):
        super().__init__(page)
        # Diagnostics surface for callers / tests.
        self.last_status: dict = {}

    # ------------------------------------------------------------ main flow

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # 1) Try the direct Netflix search page (only useful when logged in).
        direct = self._search_direct(query, limit)
        if direct:
            self.last_status["mode"] = "direct"
            return direct

        # 2) Fall back to Google `site:netflix.com/title`.
        log.info(
            "[netflix] direct path empty (likely login wall); "
            "falling back to Google site:netflix.com/title"
        )
        fallback = self._search_google_fallback(query, limit)
        if fallback:
            self.last_status["mode"] = "google"
        return fallback

    # ------------------------------------------------------------ direct path

    def _search_direct(self, query: str, limit: int) -> list[SearchResult]:
        """Hit Netflix's search page; only works when authenticated."""
        # Warm-up: visit homepage so cookies / locale routing settle.
        if safe_goto(self.page, self.HOMEPAGE_URL, timeout=20000, retries=1):
            human_delay(1.0, 2.5)
            self._human_hints()

        url = self.SEARCH_URL.format(q=_slugify(query))
        log.info("[netflix] navigating to %s", url)
        if not safe_goto(self.page, url, timeout=30000):
            self.last_status = {"phase": "direct", "error": "goto_failed"}
            return []

        human_delay(2.0, 3.5)
        self._human_hints()

        # Hard-redirect to login / signup -> not authenticated.
        cur = (self.page.url or "").lower()
        if any(seg in cur for seg in ("/login", "/signup", "/loginhelp",
                                      "/signupbasic", "/getstarted")):
            log.warning("[netflix] redirected to login/signup: %s", cur)
            self.last_status = {"phase": "direct",
                                "block_reason": "login_redirect",
                                "url": cur}
            return []

        if self._is_blocked():
            return []

        # Wait briefly for any catalogue cards to attach.
        if not self._wait_for_results(timeout_ms=5000):
            log.info("[netflix] no result cards rendered — likely guest view")

        results = self._extract_direct(limit)
        log.info("[netflix] direct path extracted: %d", len(results))
        return results

    def _extract_direct(self, limit: int) -> list[SearchResult]:
        """Walk every Netflix /title/<id> anchor on the page (post-login)."""
        try:
            raw: list[dict] = self.page.evaluate(_EXTRACT_JS) or []
        except Exception as e:
            log.warning("[netflix] extraction JS failed: %s", e)
            raw = []

        log.info("[netflix] direct raw extracted: %d", len(raw))

        results: list[SearchResult] = []
        seen: set[str] = set()
        for item in raw:
            netflix_id = (item.get("netflix_id") or "").strip()
            if not netflix_id or netflix_id in seen:
                continue
            seen.add(netflix_id)

            title = (item.get("title") or "").strip()
            if not title:
                continue
            url = f"https://www.netflix.com/title/{netflix_id}"
            snippet = (item.get("snippet") or "").strip()

            r = SearchResult(title=title[:200], url=url, snippet=snippet[:400])
            r.netflix_id = netflix_id                                # type: ignore[attr-defined]
            r.type = self._infer_type(title, snippet)               # type: ignore[attr-defined]
            r.year = self._infer_year(title + " " + snippet)        # type: ignore[attr-defined]
            r.rating = self._infer_rating(snippet)                  # type: ignore[attr-defined]
            r.source = "netflix"                                     # type: ignore[attr-defined]
            results.append(r)
            if len(results) >= limit:
                break

        return results

    # ------------------------------------------------------------ Google fallback

    def _search_google_fallback(
        self, query: str, limit: int
    ) -> list[SearchResult]:
        """Use GoogleEngine to find Netflix title pages for ``query``.

        We try a *sequence* of progressively broader queries and keep going
        until we have collected enough ``/title/<id>`` candidates. This
        matters because:

        * ``site:netflix.com/title <query>`` is the most targeted but Google
          sometimes returns just one or two results when an over-restrictive
          path-prefix is combined with site-restriction.
        * ``site:netflix.com <query>`` is a fallback that opens the door to
          all Netflix subpages, and we filter for ``/title/<id>`` ourselves.
        * ``<query> netflix`` is the final, most permissive form, used only
          when both site-restricted searches come up empty.

        For each query we use *two* strategies on the Google results page:

        1. ``GoogleEngine.search()`` returns the structured organic results.
        2. We walk the full DOM ourselves and collect every anchor whose
           href matches a Netflix title URL. This catches results in the
           carousel / "Top stories" cards that the structured extraction
           misses.
        """
        try:
            google = GoogleEngine(self.page)
        except Exception as e:
            log.warning("[netflix] cannot construct GoogleEngine: %s", e)
            return []

        # Most targeted first; the path-prefix syntax `site:netflix.com/title`
        # is more reliable than the `inurl:title` operator.
        query_attempts = [
            f'site:netflix.com/title "{query}"',
            f"site:netflix.com {query}",
            f"{query} netflix",
        ]

        all_candidates: list[dict] = []
        seen_ids: set[str] = set()
        attempt_log: list[dict] = []
        self.last_status.setdefault("phase", "google")

        for q_idx, gq in enumerate(query_attempts, start=1):
            try:
                google_results = google.search(gq, limit=max(limit * 3, 20))
            except Exception as e:
                log.warning("[netflix] google fallback raised on %r: %s", gq, e)
                google_results = []

            log.info(
                "[netflix] google attempt %d/%d returned %d organic results "
                "for %r",
                q_idx, len(query_attempts), len(google_results), gq,
            )

            # Strategy 1: filter Google's structured results.
            structured_hits = 0
            for r in google_results:
                entry = self._parse_google_url(
                    r.url or "",
                    title=r.title or "",
                    snippet=r.snippet or "",
                )
                if entry and entry["netflix_id"] not in seen_ids:
                    seen_ids.add(entry["netflix_id"])
                    all_candidates.append(entry)
                    structured_hits += 1

            # Strategy 2: scan every anchor on the Google results page for
            # Netflix title URLs the structured extractor might have missed.
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
                log.debug("[netflix] DOM anchor scan raised: %s", e)
                anchors = []

            anchor_hits = 0
            for a in anchors:
                entry = self._parse_google_url(
                    a.get("href", ""),
                    title=a.get("text", "") or "",
                    snippet="",
                )
                if entry and entry["netflix_id"] not in seen_ids:
                    seen_ids.add(entry["netflix_id"])
                    all_candidates.append(entry)
                    anchor_hits += 1

            attempt_log.append({
                "query": gq,
                "organic": len(google_results),
                "structured_hits": structured_hits,
                "anchor_hits": anchor_hits,
                "google_status": getattr(google, "last_status", {}),
            })
            log.info(
                "[netflix] attempt %d collected %d new candidates "
                "(structured=%d anchors=%d, total=%d)",
                q_idx, structured_hits + anchor_hits, structured_hits,
                anchor_hits, len(all_candidates),
            )

            # Stop early once we have enough.
            if len(all_candidates) >= limit:
                break

        self.last_status["google_attempts"] = attempt_log
        # Backwards compat: also surface the *last* google probe's status.
        if attempt_log:
            self.last_status["google_status"] = (
                attempt_log[-1].get("google_status") or {}
            )

        log.info(
            "[netflix] google fallback total candidates: %d",
            len(all_candidates),
        )
        candidates = all_candidates

        results: list[SearchResult] = []
        seen: set[str] = set()
        for c in candidates:
            netflix_id = c["netflix_id"]
            if netflix_id in seen:
                continue
            seen.add(netflix_id)

            canonical_url = f"https://www.netflix.com/title/{netflix_id}"
            title_in = c["title"]
            snippet_in = c["snippet"]
            # Google often returns titles like "Stranger Things | Netflix
            # Official Site" — clean those.
            display_title = self._clean_google_title(title_in) or (
                f"Netflix title {netflix_id}"
            )

            new_r = SearchResult(
                title=display_title[:200],
                url=canonical_url,
                snippet=snippet_in[:400],
            )
            new_r.netflix_id = netflix_id                            # type: ignore[attr-defined]
            new_r.type = self._infer_type(title_in, snippet_in)     # type: ignore[attr-defined]
            new_r.year = self._infer_year(                          # type: ignore[attr-defined]
                title_in + " " + snippet_in
            )
            new_r.rating = self._infer_rating(snippet_in)           # type: ignore[attr-defined]
            new_r.source = "google"                                  # type: ignore[attr-defined]
            results.append(new_r)
            if len(results) >= limit:
                break

        return results

    @staticmethod
    def _parse_google_url(
        url: str, title: str = "", snippet: str = ""
    ) -> dict | None:
        """Match a Google-result URL against ``ABS_TITLE_URL_RE``.

        Also handles Google's legacy ``/url?q=https://www.netflix.com/...``
        redirect wrapper.
        """
        if not url:
            return None
        if "/url?" in url and "netflix.com" in url:
            try:
                qs = urllib.parse.urlparse(url).query
                target = urllib.parse.parse_qs(qs).get("q", [""])[0]
                if target:
                    url = target
            except Exception:
                pass
        m = ABS_TITLE_URL_RE.match(url)
        if not m:
            return None
        return {
            "netflix_id": m.group(1),
            "title": title,
            "snippet": snippet,
        }

    # ------------------------------------------------------------ helpers

    @staticmethod
    def _clean_google_title(title: str) -> str:
        """Strip the boilerplate Google appends to Netflix title pages."""
        if not title:
            return ""
        t = title.strip()
        # Common suffixes Netflix uses in <title>.
        for sep in (" | Netflix Official Site", " | Netflix",
                    " - Netflix Official Site", " - Netflix",
                    " | Sitio oficial de Netflix",
                    " | Site officiel de Netflix"):
            if t.endswith(sep):
                t = t[: -len(sep)].strip()
                break
        # Some Google snippets prefix the URL — strip leading "https://".
        t = re.sub(r"^https?://\S+\s*[-—:|]\s*", "", t)
        # On Google's results page the visible anchor text for a Netflix
        # title page is typically rendered as "Watch <Title>" / "Ver
        # <Título>" / "Voir <Titre>". Drop the localized verb.
        t = re.sub(
            r"^(Watch|Ver|Voir|Sehen|Guarda|Assista|Assistir|Bekijk|"
            r"Vizionează|Áhorft|Pogledaj|Schauen)\s+",
            "",
            t,
            flags=re.IGNORECASE,
        )
        return t.strip()

    @staticmethod
    def _infer_type(title: str, snippet: str) -> str:
        """Best-effort movie/series classification from title + snippet."""
        blob = f"{title} {snippet}".lower()
        # Series wins ties: "TV Series · 2016" is a stronger signal than the
        # generic word "film" appearing in a description.
        for phrase in SERIES_PHRASES:
            if phrase in blob:
                return "series"
        for phrase in MOVIE_PHRASES:
            if phrase in blob:
                return "movie"
        return ""

    @staticmethod
    def _infer_year(blob: str) -> int | None:
        """Pull the first 4-digit year out of ``blob``, if any."""
        if not blob:
            return None
        m = YEAR_RE.search(blob)
        if not m:
            return None
        try:
            y = int(m.group(1))
        except ValueError:
            return None
        # Sanity: Netflix titles are >= 1900 and <= current year + 2.
        if 1900 <= y <= 2100:
            return y
        return None

    @staticmethod
    def _infer_rating(snippet: str) -> str:
        """Pull a maturity rating token out of the snippet, if any."""
        if not snippet:
            return ""
        m = RATING_RE.search(snippet)
        if not m:
            return ""
        return m.group(1).strip()

    def _is_blocked(self) -> bool:
        """Detect login-wall / sorry-page interstitials."""
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

        if any(seg in url for seg in ("/login", "/signup", "/loginhelp",
                                      "/signupbasic", "/getstarted")):
            log.warning("[netflix] login redirect: %s", url)
            self.last_status["block_reason"] = "login_redirect"
            return True

        # Heuristic: no result anchors AND the body is dominated by the
        # join / sign-in copy = login wall.
        try:
            has_title_anchor = bool(
                self.page.query_selector('a[href*="/title/"]')
                or self.page.query_selector('a[href*="/watch/"]')
            )
        except Exception:
            has_title_anchor = False
        if not has_title_anchor:
            join_hits = sum(1 for p in BLOCK_PHRASES if p in body)
            if join_hits >= 2:
                log.warning(
                    "[netflix] login wall detected (no title anchors, "
                    "%d join-phrases)", join_hits,
                )
                self.last_status["block_reason"] = "login_wall"
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

    def _wait_for_results(self, timeout_ms: int = 5000) -> bool:
        """Wait for at least one /title/ or /watch/ anchor to attach."""
        deadline = time.time() + timeout_ms / 1000.0
        try:
            self.page.wait_for_function(
                """
                () => {
                  const anchors = document.querySelectorAll('a[href]');
                  for (const a of anchors) {
                    const href = a.getAttribute('href') || '';
                    if (/^\\/(title|watch)\\/\\d+/.test(href)) return true;
                  }
                  return false;
                }
                """,
                timeout=timeout_ms,
            )
            return True
        except Exception as e:
            log.debug("[netflix] wait_for_function timeout: %s", e)
        while time.time() < deadline:
            for sel in RESULT_PRESENCE_SELECTORS:
                try:
                    if self.page.query_selector(sel):
                        return True
                except Exception:
                    continue
            time.sleep(0.4)
        return False

    def selector_counts(self) -> dict[str, int]:
        """Per-selector match counts on the current page (for diagnostics)."""
        counts: dict[str, int] = {}
        for sel in (
            'a[href*="/title/"]',
            'a[href*="/watch/"]',
            "[data-uia*='title']",
            ".title-card",
            "main",
        ):
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        return counts


# ---------------------------------------------------------------- JS
#
# Walks every anchor whose href matches ``/title/<id>``, then resolves the
# surrounding card to dig out the visible title and a short snippet
# (synopsis / metadata row). We do this in JS rather than chained Python
# selectors to avoid dozens of CDP round-trips per result.
_EXTRACT_JS = r"""
() => {
  const TITLE_RE = /^\/title\/(\d+)/;
  const text = (el) => (el ? (el.innerText || el.textContent || "").trim() : "");

  const findCard = (a) => {
    return (
      a.closest('[data-uia*="title"]') ||
      a.closest('.title-card') ||
      a.closest('[class*="title-card"]') ||
      a.closest('article') ||
      a.parentElement
    );
  };

  const out = [];
  const seen = new Set();
  const anchors = document.querySelectorAll('a[href]');
  for (const a of anchors) {
    const href = a.getAttribute('href') || '';
    const m = href.match(TITLE_RE);
    if (!m) continue;
    const netflix_id = m[1];
    if (seen.has(netflix_id)) continue;
    seen.add(netflix_id);

    const card = findCard(a);

    // Title: aria-label on the anchor, or fallback strategies.
    let title = (a.getAttribute('aria-label') || '').trim();
    if (!title) {
      const img = card && card.querySelector('img[alt]');
      if (img) title = (img.getAttribute('alt') || '').trim();
    }
    if (!title) {
      title = text(a);
    }
    if (!title && card) {
      const h = card.querySelector('h1, h2, h3, h4, [class*="title"]');
      if (h) title = text(h);
    }

    // Snippet: any descriptive text on the card other than the title.
    let snippet = "";
    if (card) {
      const desc = card.querySelector(
        '[class*="synopsis"], [class*="description"], p, [data-uia*="synopsis"]'
      );
      if (desc) snippet = text(desc);
    }

    out.push({
      netflix_id: netflix_id,
      title: title,
      snippet: snippet,
    });
  }
  return out;
}
"""
