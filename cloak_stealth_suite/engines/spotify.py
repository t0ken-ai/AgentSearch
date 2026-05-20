"""Spotify Web search adapter.

Spotify exposes a public, login-free search experience at:

    https://open.spotify.com/search/<query>

Quirks we have to handle:

1. **Heavy React SPA**. The page hydrates client-side; result cards don't
   appear in the initial HTML. We have to wait for at least one result link
   to attach to the DOM before extraction.
2. **OneTrust cookie banner** (EU geos). When CloakBrowser picks a EU
   timezone, Spotify presents the OneTrust consent dialog — we click
   "Accept All" / "Reject All" so it doesn't cover content.
3. **Mixed entity types in one page**. The /search/<q> page mixes Top Result,
   Songs, Artists, Albums, Playlists, Podcasts and Episodes. We classify
   each result purely from the URL path
   (``/track/``, ``/album/``, ``/artist/``, ``/playlist/``, ``/show/``,
   ``/episode/``), which is a stable signal across UI rewrites.
4. **Selector instability**. Spotify rewrites class names between
   deployments. We avoid `class*=` selectors and instead extract via
   ``page.evaluate(...)`` so we can walk the DOM with our own logic and
   stay robust as the markup changes.

Each :class:`SearchResult` carries:

* ``type``     – one of "song" / "album" / "artist" / "playlist" /
                 "show" / "episode"
* ``artist``   – primary artist string when applicable, "" otherwise
* ``album``    – album name when applicable (set for songs), "" otherwise
* ``entity_id`` – the Spotify id portion of the URL
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


SPOTIFY_BASE = "https://open.spotify.com"

# Spotify entity URL → result "type" string we expose to callers.
PATH_TO_TYPE = {
    "track": "song",
    "album": "album",
    "artist": "artist",
    "playlist": "playlist",
    "show": "show",
    "episode": "episode",
}

# Anchors with these href shapes are search hits.
RESULT_HREF_RE = re.compile(r"^/(track|album|artist|playlist|show|episode)/([A-Za-z0-9]+)")

# OneTrust / Spotify consent buttons (EU cookie banner).
CONSENT_BUTTON_SELECTORS = [
    "#onetrust-accept-btn-handler",
    "#onetrust-reject-all-handler",
    "button#onetrust-accept-btn-handler",
    "button[aria-label*='Accept' i]",
    "button[aria-label*='Reject' i]",
]

# Phrases that mean Spotify gated us behind login / blocked us.
BLOCK_PHRASES = [
    "log in to spotify",
    "sign up to start listening",
    "access denied",
    "403 forbidden",
    "page not found",
    "something went wrong",
    "couldn't load",
]


class SpotifyEngine(BaseEngine):
    """Search Spotify via ``open.spotify.com/search``."""

    name = "spotify"
    max_retries = 2

    SEARCH_URL = "https://open.spotify.com/search/{q}"

    def __init__(self, page):
        super().__init__(page)
        # Diagnostics surface for callers / tests.
        self.last_status: dict = {}

    # ------------------------------------------------------------ main flow

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        url = self.SEARCH_URL.format(q=urllib.parse.quote(query))
        log.info("[spotify] navigating to %s", url)

        if not safe_goto(self.page, url, timeout=30000):
            return []

        # Let the SPA start hydrating.
        human_delay(1.5, 3.0)

        # Dismiss the OneTrust cookie banner if it appeared.
        self._handle_consent()

        if self._is_blocked():
            return []

        # Wait for at least one result link to attach. 12s is generous —
        # search hydrates within ~2-3s on a warm browser.
        if not self._wait_for_results(timeout_ms=12000):
            log.info(
                "[spotify] no result anchors after wait; continuing to extraction"
            )

        # Small human-like nudge so any below-the-fold sections render.
        self._human_hints()

        return self._extract_results(limit)

    # ------------------------------------------------------------ helpers

    def _handle_consent(self) -> None:
        """Click any visible cookie / consent buttons."""
        for sel in CONSENT_BUTTON_SELECTORS:
            try:
                btn = self.page.query_selector(sel)
                if btn:
                    try:
                        btn.click(timeout=3000)
                    except Exception:
                        # Some banners need a JS click to bypass overlays.
                        try:
                            self.page.evaluate("(el) => el.click()", btn)
                        except Exception:
                            continue
                    log.info("[spotify] dismissed consent (%s)", sel)
                    human_delay(0.5, 1.2)
                    return
            except Exception:
                continue

    def _is_blocked(self) -> bool:
        """Detect a hard block / login wall."""
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

        # Forced redirect to the login page.
        if "accounts.spotify.com" in url and "login" in url:
            log.warning("[spotify] redirected to login: %s", url)
            self.last_status["block_reason"] = "login_required"
            return True

        # Empty / single-banner page where the only visible text is the
        # login prompt — treat as a block.
        if "log in to spotify" in body and "search" not in body:
            log.warning("[spotify] login wall (search content missing)")
            self.last_status["block_reason"] = "login_wall"
            return True

        for phrase in BLOCK_PHRASES:
            if phrase in title:
                log.warning("[spotify] block phrase in title: %r", phrase)
                self.last_status["block_reason"] = phrase
                return True

        return False

    def _human_hints(self) -> None:
        """Small mouse / scroll movement to encourage lazy hydration."""
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
                "() => window.scrollBy(0, Math.floor(Math.random()*400) + 200)"
            )
        except Exception:
            pass
        time.sleep(random.uniform(0.4, 0.9))

    def _wait_for_results(self, timeout_ms: int = 12000) -> bool:
        """Wait for at least one ``a[href^="/track/"]`` (or other entity) to attach."""
        # CSS doesn't support :has() across browsers, but we can chain
        # selectors. The most common entity is /track/ or /artist/ for any
        # query; wait_for_function lets us check for any of them.
        deadline = time.time() + timeout_ms / 1000.0
        try:
            self.page.wait_for_function(
                """
                () => {
                  const anchors = document.querySelectorAll('a[href]');
                  for (const a of anchors) {
                    const href = a.getAttribute('href') || '';
                    if (/^\\/(track|album|artist|playlist|show|episode)\\//.test(href)) {
                      return true;
                    }
                  }
                  return false;
                }
                """,
                timeout=timeout_ms,
            )
            return True
        except Exception as e:
            log.debug("[spotify] wait_for_function timeout: %s", e)
        # Manual poll fallback.
        while time.time() < deadline:
            try:
                anchors = self.page.query_selector_all("a[href^='/track/'], a[href^='/album/'], a[href^='/artist/'], a[href^='/playlist/'], a[href^='/show/'], a[href^='/episode/']")
                if anchors:
                    return True
            except Exception:
                pass
            time.sleep(0.5)
        return False

    def selector_counts(self) -> dict[str, int]:
        """Per-selector match counts on the current page (for diagnostics)."""
        counts: dict[str, int] = {}
        for sel in (
            "a[href^='/track/']",
            "a[href^='/album/']",
            "a[href^='/artist/']",
            "a[href^='/playlist/']",
            "a[href^='/show/']",
            "a[href^='/episode/']",
            "[data-testid='tracklist-row']",
            "[data-testid='card-click-handler']",
            "[data-testid='herocard-click-handler']",
        ):
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        return counts

    # ------------------------------------------------------------ extraction

    def _extract_results(self, limit: int) -> list[SearchResult]:
        """Pull entries from the rendered DOM via ``page.evaluate``.

        Doing the walk in JavaScript avoids dozens of round-trips for each
        sibling lookup and lets us deduplicate on Spotify entity ids.
        """
        try:
            raw: list[dict] = self.page.evaluate(_EXTRACT_JS) or []
        except Exception as e:
            log.warning("[spotify] extraction JS failed: %s", e)
            raw = []

        log.info("[spotify] raw extracted: %d", len(raw))

        results: list[SearchResult] = []
        seen: set[str] = set()
        for item in raw:
            entity_type = item.get("type") or ""
            entity_id = item.get("id") or ""
            title = (item.get("title") or "").strip()
            if not (entity_type and entity_id and title):
                continue
            key = f"{entity_type}:{entity_id}"
            if key in seen:
                continue
            seen.add(key)

            url = f"{SPOTIFY_BASE}/{entity_type}/{entity_id}"
            type_label = PATH_TO_TYPE.get(entity_type, entity_type)

            artist = (item.get("artist") or "").strip()
            album = (item.get("album") or "").strip()

            # Compose a compact human-readable snippet.
            head_bits: list[str] = [type_label.capitalize()]
            if artist:
                head_bits.append(f"by {artist}")
            if album and type_label == "song":
                head_bits.append(f"on {album}")
            snippet = " · ".join(head_bits)

            result = SearchResult(title=title, url=url, snippet=snippet)
            result.type = type_label                # type: ignore[attr-defined]
            result.artist = artist                  # type: ignore[attr-defined]
            result.album = album                    # type: ignore[attr-defined]
            result.entity_id = entity_id            # type: ignore[attr-defined]
            results.append(result)
            if len(results) >= limit:
                break

        return results


# ---------------------------------------------------------------- JS
#
# Walks every anchor whose href matches /(track|album|artist|playlist|show|
# episode)/<id>, derives the title from the anchor's text (or the closest
# heading), and tries to find the primary artist / album by inspecting
# sibling anchors and the `tracklist-row` / `card` containers.
#
# Spotify markup conventions used here:
#   * Track rows    : <div data-testid="tracklist-row">
#                       ... a[href^=/track/]  (title)
#                       ... a[href^=/artist/] (artists, potentially many)
#                       ... a[href^=/album/]  (album)
#   * Cards (album, : <div data-testid="card-click-handler">
#       artist,         contains a[href^=/<type>/<id>] and a subtitle <div>
#       playlist,       with the artist / owner / type label.
#       show, episode)
#   * Hero card     : <a data-testid="herocard-click-handler" href="/<type>/<id>">
#                     containing a heading and a subtitle row.
_EXTRACT_JS = r"""
() => {
  const HREF_RE = /^\/(track|album|artist|playlist|show|episode)\/([A-Za-z0-9]+)/;
  const TYPES_WITH_ARTIST = new Set(["track", "album"]);

  const text = (el) => (el ? (el.innerText || el.textContent || "").trim() : "");

  // Pull "title" out of an anchor — falls back to the nearest heading or
  // the anchor's own text.
  const titleFromAnchor = (a) => {
    let t = text(a);
    if (t && t.length < 200) return t;
    // Sometimes the anchor wraps an <img> only; look for a heading nearby.
    const card = a.closest('[data-testid="card-click-handler"], [data-testid="herocard-click-handler"], [data-testid="tracklist-row"]');
    if (card) {
      const h = card.querySelector('h1, h2, h3, h4, [data-testid$="-name"], [data-testid$="-title"]');
      if (h) {
        const ht = text(h);
        if (ht) return ht;
      }
    }
    return t;
  };

  // Given an anchor, find the artist string (best-effort).
  const artistFor = (a, entityType) => {
    if (entityType === "artist") {
      return text(a);
    }

    // Tracklist row: artist links live as siblings of the track link.
    const row = a.closest('[data-testid="tracklist-row"]');
    if (row) {
      const artists = Array.from(row.querySelectorAll('a[href^="/artist/"]'))
        .map(text).filter(Boolean);
      if (artists.length) return artists.join(", ");
    }

    // Card: artist anchors usually live alongside the title link.
    const card = a.closest('[data-testid="card-click-handler"], [data-testid="herocard-click-handler"]');
    if (card) {
      const artists = Array.from(card.querySelectorAll('a[href^="/artist/"]'))
        .map(text).filter(Boolean);
      if (artists.length) return artists.join(", ");

      // Some cards don't link the artist; the subtitle still contains the
      // artist / owner string. Pick a small text node that is not the title.
      const titleText = titleFromAnchor(a);
      const subtitleEls = card.querySelectorAll('span, p, div');
      for (const el of subtitleEls) {
        const t = text(el);
        if (!t) continue;
        if (t === titleText) continue;
        if (t.length > 200) continue;
        // Skip the type label ("Song", "Artist", ...).
        if (/^(song|artist|album|playlist|podcast|episode|show)$/i.test(t)) continue;
        // Skip pure metadata like "2023" / "PLAYLIST" / "Album · 2014".
        if (/^[\s·•|0-9-]+$/.test(t)) continue;
        return t;
      }
    }
    return "";
  };

  // Album for a track.
  const albumFor = (a, entityType) => {
    if (entityType !== "track") return "";
    const row = a.closest('[data-testid="tracklist-row"]');
    if (row) {
      const albumA = row.querySelector('a[href^="/album/"]');
      if (albumA) return text(albumA);
    }
    return "";
  };

  const out = [];
  const seen = new Set();
  const anchors = document.querySelectorAll('a[href]');
  for (const a of anchors) {
    const href = a.getAttribute('href') || '';
    const m = href.match(HREF_RE);
    if (!m) continue;
    const entityType = m[1];
    const entityId   = m[2];
    const key = entityType + ":" + entityId;
    if (seen.has(key)) continue;

    const title = titleFromAnchor(a);
    if (!title) continue;

    // Skip "more / show all" navigation anchors, which point at category
    // pages rather than entity pages — they don't have a title we want.
    if (/^(show all|see all|more)$/i.test(title)) continue;

    seen.add(key);

    const artist = TYPES_WITH_ARTIST.has(entityType) ? artistFor(a, entityType)
                                                    : (entityType === "artist" ? title : artistFor(a, entityType));
    const album  = albumFor(a, entityType);

    out.push({
      type:   entityType,
      id:     entityId,
      title:  title,
      artist: artist,
      album:  album,
    });
  }
  return out;
}
"""
