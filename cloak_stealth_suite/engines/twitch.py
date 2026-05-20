"""Twitch search adapter.

Twitch exposes a public, login-free search experience at:

    https://www.twitch.tv/search?term=<query>

Quirks we have to handle:

1. **Heavy React SPA**. The whole page is hydrated client-side and result
   cards don't appear in the initial HTML — we must wait for at least one
   ``a[href]`` matching a Twitch entity URL to attach to the DOM before
   extraction.
2. **Mature-content / age gate**. For some category landings Twitch shows a
   "Start Watching" / "Mature content" interstitial. The /search page
   itself doesn't usually trigger it, but we accept it if it appears so we
   don't get stuck.
3. **Cookie / consent banner**. EU geos see the OneTrust-style banner; we
   click "Accept" / "Reject" so it stops covering content.
4. **Mixed entity types in one page**. The /search page mixes:

   * **Channels**       — link out to ``/<username>``
   * **Live channels**  — same URL, but the card includes a viewer count
     (e.g. ``"12.3K viewers"``) and a current game
   * **Categories**     — link out to ``/directory/category/<slug>`` or
     ``/directory/game/<slug>``
   * **Videos / VODs**  — link out to ``/videos/<id>`` (sometimes
     ``/<channel>/video/<id>`` after a click-through)

   We classify each result purely from the URL shape, which is stable
   across Twitch's frequent CSS rewrites.
5. **Selector instability**. Twitch rewrites class names between
   deployments. We avoid ``class*=`` selectors and instead extract via
   ``page.evaluate(...)`` so we can walk the DOM with our own logic and
   stay robust as the markup changes.

Each :class:`SearchResult` carries:

* ``type``        – one of ``"channel"`` / ``"live"`` / ``"video"`` /
                    ``"category"``
* ``viewers``     – integer viewer count when shown live, ``None`` otherwise
* ``viewers_text`` – original "12.3K viewers" / "543 viewers" string (kept
                    for display)
* ``game``        – currently-playing category for live channels (e.g.
                    ``"League of Legends"``), ``""`` otherwise
* ``channel``     – channel display name where it differs from the URL
                    slug; defaults to the slug
* ``slug``        – the URL slug for channels (``twitch.tv/<slug>``)
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


TWITCH_BASE = "https://www.twitch.tv"

# Slugs under twitch.tv/ that are reserved app routes — they look like
# channel URLs but aren't. Used to filter false positives when we walk
# every anchor on the page.
RESERVED_TOP_LEVEL_PATHS = {
    "directory", "videos", "search", "login", "signup", "logout",
    "p", "popout", "subs", "team", "turbo", "products", "downloads",
    "broadcast", "store", "drops", "creatorcamp", "jobs", "press",
    "about", "tos", "privacy", "legal", "security", "tags",
    "settings", "subscriptions", "wallet", "inventory", "friends",
    "following", "browse", "events",
    # locale prefixes
    "en", "en-gb", "de", "fr", "es", "es-mx", "ja", "ko", "ru",
    "zh-cn", "zh-tw", "pt", "pt-br", "it", "tr", "pl", "nl",
    # marketing-only
    "creator-camp", "esports",
}

# OneTrust / Twitch consent buttons (EU cookie banner).
CONSENT_BUTTON_SELECTORS = [
    "#onetrust-accept-btn-handler",
    "#onetrust-reject-all-handler",
    "button#onetrust-accept-btn-handler",
    "button[data-a-target='consent-banner-accept']",
    "button[aria-label*='Accept' i]",
    "button[aria-label*='Reject' i]",
]

# Mature-content / "Start Watching" gate (sometimes appears mid-page).
MATURE_GATE_SELECTORS = [
    "button[data-a-target='content-classification-gate-overlay-start-watching-button']",
    "button[data-a-target='player-overlay-mature-accept']",
]

# Phrases that mean Twitch gated us behind login / blocked us.
BLOCK_PHRASES = [
    "access denied",
    "403 forbidden",
    "page not found",
    "this content isn",
    "verify it's you",
    "unusual traffic",
    "automated queries",
]

# Selectors used to detect whether the search page has hydrated (we accept
# any of them — Twitch reshuffles them frequently).
RESULT_PRESENCE_SELECTORS = [
    "a[data-a-target='preview-card-image-link']",
    "a[data-a-target='search-result-channel']",
    "a[data-a-target='search-result-live-channel']",
    "a[href^='/directory/category/']",
    "a[href^='/directory/game/']",
    "a[href^='/videos/']",
    "main a[href^='/']",
]


# ---------------------------------------------------------------- helpers


def _parse_viewers(text: str) -> int | None:
    """Parse '12.3K viewers' / '543 viewers' / '1.2M viewers' into int.

    Returns ``None`` when the text doesn't look like a viewer count.
    """
    if not text:
        return None
    t = text.strip().lower().replace(",", "")
    # Some locales render "12.3K watching" or "12.3K spectateurs", but the
    # numeric portion is the same shape, so we accept any trailing word.
    m = re.search(r"(\d+\.?\d*)\s*([kmb]?)\b", t)
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


def _abs_url(href: str) -> str:
    """Make a Twitch href absolute against ``www.twitch.tv``."""
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return TWITCH_BASE + href
    return TWITCH_BASE + "/" + href


def _classify_href(href: str) -> tuple[str, str] | None:
    """Classify a Twitch URL into ``(type, slug_or_id)`` or return None.

    Returns:
        ("category", slug)  -- /directory/category/<slug> or /directory/game/<slug>
        ("video",    id)    -- /videos/<numeric>  or  /<channel>/video/<id>
        ("channel",  slug)  -- /<slug>            (single non-reserved segment)
        None                -- not an entity URL we want.
    """
    if not href:
        return None

    # Drop any query string / fragment.
    href = href.split("?", 1)[0].split("#", 1)[0]

    # Absolute -> path.
    if href.startswith("http://") or href.startswith("https://"):
        try:
            parsed = urllib.parse.urlparse(href)
        except Exception:
            return None
        if not parsed.netloc.endswith("twitch.tv"):
            return None
        path = parsed.path
    elif href.startswith("/"):
        path = href
    else:
        return None

    if not path or path == "/":
        return None

    parts = [p for p in path.split("/") if p]
    if not parts:
        return None

    # /directory/category/<slug>  or  /directory/game/<slug>
    if parts[0] == "directory" and len(parts) >= 3 and parts[1] in ("category", "game"):
        return ("category", parts[2])

    # /videos/<id>
    if parts[0] == "videos" and len(parts) >= 2 and parts[1].isdigit():
        return ("video", parts[1])

    # /<channel>/video/<id>
    if len(parts) >= 3 and parts[1] == "video" and parts[2].isdigit():
        return ("video", parts[2])

    # /<channel>/clip/<id> -- treat as video
    if len(parts) >= 3 and parts[1] == "clip":
        return ("video", parts[2])

    # /<channel>  (single segment, not reserved)
    if len(parts) == 1:
        slug = parts[0]
        slug_low = slug.lower()
        if slug_low in RESERVED_TOP_LEVEL_PATHS:
            return None
        # Twitch usernames are 4-25 chars, ASCII letters/digits/underscore.
        if not re.fullmatch(r"[A-Za-z0-9_]{3,25}", slug):
            return None
        return ("channel", slug)

    return None


# ---------------------------------------------------------------- engine


class TwitchEngine(BaseEngine):
    """Search Twitch via ``twitch.tv/search?term=<q>``."""

    name = "twitch"
    max_retries = 2

    SEARCH_URL = "https://www.twitch.tv/search?term={q}"
    HOMEPAGE_URL = "https://www.twitch.tv/"

    def __init__(self, page):
        super().__init__(page)
        # Diagnostics surface for callers / tests.
        self.last_status: dict = {}

    # ------------------------------------------------------------ main flow

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # 1) Warm up the homepage so cookies / consent settle before search.
        if safe_goto(self.page, self.HOMEPAGE_URL, timeout=20000, retries=1):
            human_delay(1.0, 2.5)
            self._handle_consent()
            self._human_hints()

        # 2) Issue the actual search.
        url = self.SEARCH_URL.format(q=urllib.parse.quote(query))
        log.info("[twitch] navigating to %s", url)
        if not safe_goto(self.page, url, timeout=30000):
            return []

        # Let the SPA hydrate.
        human_delay(2.0, 3.5)
        self._handle_consent()
        self._dismiss_mature_gate()

        if self._is_blocked():
            return []

        # 3) Wait for at least one entity anchor to attach. 12s is generous —
        # search hydrates within ~2-3s on a warm browser.
        if not self._wait_for_results(timeout_ms=12000):
            log.info(
                "[twitch] no result anchors after wait; continuing to extraction"
            )

        # 4) A small human-like nudge so below-the-fold sections render.
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
                    log.info("[twitch] dismissed consent (%s)", sel)
                    human_delay(0.5, 1.2)
                    return
            except Exception:
                continue

    def _dismiss_mature_gate(self) -> None:
        """Click through any mature-content / start-watching interstitial."""
        for sel in MATURE_GATE_SELECTORS:
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
                    log.info("[twitch] dismissed mature gate (%s)", sel)
                    human_delay(0.5, 1.0)
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

        # Forced redirect to a login flow (Twitch sometimes does this for
        # specific category links, but the search page itself shouldn't).
        if "twitch.tv/login" in url:
            log.warning("[twitch] redirected to login: %s", url)
            self.last_status["block_reason"] = "login_redirect"
            return True

        for phrase in BLOCK_PHRASES:
            if phrase in title:
                log.warning("[twitch] block phrase in title: %r", phrase)
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
                "() => window.scrollBy(0, Math.floor(Math.random()*500) + 200)"
            )
        except Exception:
            pass
        time.sleep(random.uniform(0.4, 0.9))

    def _wait_for_results(self, timeout_ms: int = 12000) -> bool:
        """Wait for at least one entity anchor to attach to the page."""
        deadline = time.time() + timeout_ms / 1000.0

        # Page-native waiter is the fast path.
        for sel in RESULT_PRESENCE_SELECTORS[:4]:
            try:
                self.page.wait_for_selector(sel, timeout=int(timeout_ms / 2))
                return True
            except Exception:
                continue

        # Manual poll fallback — anything from RESULT_PRESENCE_SELECTORS works.
        while time.time() < deadline:
            for sel in RESULT_PRESENCE_SELECTORS:
                try:
                    if self.page.query_selector(sel) is not None:
                        return True
                except Exception:
                    continue
            time.sleep(0.5)
        return False

    def selector_counts(self) -> dict[str, int]:
        """Per-selector match counts on the current page (for diagnostics)."""
        counts: dict[str, int] = {}
        for sel in RESULT_PRESENCE_SELECTORS:
            try:
                counts[sel] = len(self.page.query_selector_all(sel))
            except Exception:
                counts[sel] = -1
        return counts

    # ------------------------------------------------------------ extraction

    def _extract_results(self, limit: int) -> list[SearchResult]:
        """Pull entries from the rendered DOM via ``page.evaluate``.

        Doing the walk in JavaScript avoids dozens of round-trips for each
        sibling lookup and lets us deduplicate on Twitch entity ids.
        """
        try:
            raw: list[dict] = self.page.evaluate(_EXTRACT_JS) or []
        except Exception as e:
            log.warning("[twitch] extraction JS failed: %s", e)
            raw = []

        log.info("[twitch] raw extracted: %d", len(raw))

        results: list[SearchResult] = []
        seen: set[str] = set()
        for item in raw:
            entity_type = item.get("type") or ""
            entity_key = item.get("key") or ""
            title = (item.get("title") or "").strip()
            href = item.get("href") or ""
            if not (entity_type and entity_key and title and href):
                continue
            dedupe_key = f"{entity_type}:{entity_key}"
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            url = _abs_url(href)

            viewers_text = (item.get("viewers_text") or "").strip()
            viewers = _parse_viewers(viewers_text) if viewers_text else None
            game = (item.get("game") or "").strip()
            channel_name = (item.get("channel") or "").strip()
            slug = entity_key if entity_type in ("channel", "live") else ""

            # When we don't have an explicit channel display name, prefer the
            # title (which is typically the user's display name in proper case)
            # over the bare URL slug for live/channel results.
            if not channel_name and entity_type in ("channel", "live") and slug:
                if title and title.lower() == slug.lower():
                    channel_name = title

            # Compose a compact human-readable snippet.
            head_bits: list[str] = []
            if entity_type == "live":
                head_bits.append("Live")
            elif entity_type == "channel":
                head_bits.append("Channel")
            elif entity_type == "video":
                head_bits.append("Video")
            elif entity_type == "category":
                head_bits.append("Category")

            if channel_name and channel_name.lower() != title.lower():
                head_bits.append(f"by {channel_name}")
            if game:
                head_bits.append(f"playing {game}")
            if viewers_text:
                head_bits.append(viewers_text)

            snippet = " · ".join(head_bits)

            # Normalize 'live' vs 'channel' label: a live channel is still
            # a channel, but callers asked for an explicit ``live`` type.
            type_label = entity_type

            result = SearchResult(title=title, url=url, snippet=snippet)
            result.type = type_label                  # type: ignore[attr-defined]
            result.viewers = viewers                  # type: ignore[attr-defined]
            result.viewers_text = viewers_text        # type: ignore[attr-defined]
            result.game = game                        # type: ignore[attr-defined]
            result.channel = channel_name or slug     # type: ignore[attr-defined]
            result.slug = slug                        # type: ignore[attr-defined]
            results.append(result)
            if len(results) >= limit:
                break

        return results


# ---------------------------------------------------------------- JS
#
# Walks every anchor whose href matches a Twitch entity URL pattern,
# classifies it (channel / live / video / category), and tries to find the
# associated viewer count + current game by inspecting siblings within the
# nearest "card" container.
#
# Twitch markup conventions used here (kept loose because they change):
#
# * Search result cards live under ``main`` and contain a single primary
#   anchor whose href is the entity URL.
# * Live channel cards include a viewer count element with text like
#   "12.3K viewers" — usually rendered with a class containing
#   ``viewers-count`` or via ``data-a-target="viewer-count"``.
# * The current game on a live channel card appears as a sibling anchor
#   pointing at ``/directory/category/<slug>`` or
#   ``/directory/game/<slug>``.
# * The channel display name (which can differ from the URL slug, e.g.
#   ``"Riot Games"`` vs ``riotgames``) shows up either as a heading inside
#   the card or as a separate ``a[href="/<slug>"]`` whose text is the
#   display name.
_EXTRACT_JS = r"""
() => {
  const RESERVED = new Set([
    "directory","videos","search","login","signup","logout","p","popout",
    "subs","team","turbo","products","downloads","broadcast","store",
    "drops","creatorcamp","jobs","press","about","tos","privacy","legal",
    "security","tags","settings","subscriptions","wallet","inventory",
    "friends","following","browse","events",
    "en","en-gb","de","fr","es","es-mx","ja","ko","ru",
    "zh-cn","zh-tw","pt","pt-br","it","tr","pl","nl",
    "creator-camp","esports"
  ]);
  const USERNAME_RE = /^[A-Za-z0-9_]{3,25}$/;

  const text = (el) => (el ? (el.innerText || el.textContent || "").trim() : "");

  // Returns {type, key} | null
  const classify = (href) => {
    if (!href) return null;
    href = href.split("?")[0].split("#")[0];
    let path;
    if (/^https?:\/\//i.test(href)) {
      try {
        const u = new URL(href);
        if (!u.hostname.endsWith("twitch.tv")) return null;
        path = u.pathname;
      } catch (_) { return null; }
    } else if (href.startsWith("/")) {
      path = href;
    } else {
      return null;
    }
    if (!path || path === "/") return null;
    const parts = path.split("/").filter(Boolean);
    if (!parts.length) return null;

    if (parts[0] === "directory" && parts.length >= 3 &&
        (parts[1] === "category" || parts[1] === "game")) {
      return { type: "category", key: parts[2] };
    }
    if (parts[0] === "videos" && parts.length >= 2 && /^\d+$/.test(parts[1])) {
      return { type: "video", key: parts[1] };
    }
    if (parts.length >= 3 && parts[1] === "video" && /^\d+$/.test(parts[2])) {
      return { type: "video", key: parts[2] };
    }
    if (parts.length >= 3 && parts[1] === "clip") {
      return { type: "video", key: parts[2] };
    }
    if (parts.length === 1) {
      const slug = parts[0];
      if (RESERVED.has(slug.toLowerCase())) return null;
      if (!USERNAME_RE.test(slug)) return null;
      return { type: "channel", key: slug };
    }
    return null;
  };

  // Find the nearest "card" container around an anchor — used to scope the
  // sibling lookups for viewer count / game / channel name.
  const cardOf = (a) => {
    return a.closest('article, [data-a-target$="-card"], [data-a-target*="result"], [class*="search-result"], [class*="preview-card"], [class*="tw-card"], li, div[role="link"], div[role="article"]') || a.parentElement;
  };

  // Recognises the LIVE / RERUN badge that we never want to mistake for a
  // viewer count or a title.
  const isLiveBadge = (s) => /^(live|rerun|en\s*direct|en\s*vivo|directo|ライブ|生放送|直播)\s*$/i.test((s || "").trim());

  // Strict viewer-count match: "<num> [KMB] [viewers/watching/...]". We
  // require the trailing keyword so we don't pick up the LIVE badge.
  const VIEWER_RE = /(\d[\d,.\s]*\s*[KMB]?)\s*(viewers?|watching|spectateurs?|zuschauer|spettatori|spectadores|espectadores|시청|视聴|观众|觀眾)/i;

  // Pull a viewer-count string out of a card. Returns "" if none found.
  // Always returns a *clean* string (just "<num>[KMB] viewers"), even when
  // the matching DOM node also contains unrelated card chrome.
  const viewersFor = (card) => {
    if (!card) return "";

    // Preferred: explicit data-a-target. We still validate the text.
    const el = card.querySelector('[data-a-target="viewer-count"], [data-a-target="search-result-live-channel-viewer-count"], p[data-test-selector="stream-card-default-viewers"]');
    if (el) {
      const t = text(el);
      if (t && !isLiveBadge(t)) {
        if (/^\d[\d,.\s]*\s*[KMB]?$/i.test(t)) return t + " viewers";
        const m = t.match(VIEWER_RE);
        if (m) return m[0].replace(/\s+/g, " ").trim();
      }
    }

    // Fallback: scan card text for "<num>[KMB] viewers" / "<num> watching"
    // and return *just the matching substring*, not the whole element text.
    // We prefer leaf-most nodes (those whose own text closely matches the
    // viewer-count pattern), but accept any node otherwise.
    const all = card.querySelectorAll("p, span, div, strong, b, em");
    let leafHit = "";
    let anyHit = "";
    for (const e of all) {
      const t = text(e);
      if (!t) continue;
      if (isLiveBadge(t)) continue;
      const m = t.match(VIEWER_RE);
      if (!m) continue;
      const captured = m[0].replace(/\s+/g, " ").trim();
      if (!anyHit) anyHit = captured;
      // "Leaf-ish" = node whose own text is short and is the match itself.
      if (t.length <= 24) {
        leafHit = captured;
        break;
      }
    }
    return leafHit || anyHit;
  };

  // Pull the current game / category for a live channel card.
  const gameFor = (card, anchor) => {
    if (!card) return "";
    const cands = card.querySelectorAll('a[href^="/directory/category/"], a[href^="/directory/game/"]');
    for (const c of cands) {
      if (c === anchor) continue;
      const t = text(c);
      if (t) return t;
    }
    return "";
  };

  // Pull the display name for a channel card.
  const channelNameFor = (card, slug) => {
    if (!card) return "";
    // A heading inside the card is the most reliable signal.
    const headings = card.querySelectorAll('h1, h2, h3, h4, [data-a-target="search-result-channel-name"], [data-a-target*="title"]');
    for (const h of headings) {
      const t = text(h);
      if (t && !isLiveBadge(t)) return t;
    }
    // Fallback: an anchor pointing at the same slug whose text is non-empty.
    const cands = card.querySelectorAll(`a[href="/${slug}"], a[href="/${slug}/"]`);
    for (const c of cands) {
      const t = text(c);
      if (t && !isLiveBadge(t) && t.toLowerCase() !== slug.toLowerCase()) {
        return t;
      }
    }
    return "";
  };

  // Title for the entity (per type).
  const titleFor = (a, card, info) => {
    let t = text(a);
    if (t && t.length < 200 && !isLiveBadge(t) && !/^(viewers?|watching)$/i.test(t)) {
      // For an anchor wrapping a thumbnail image only, the text might be a
      // bare numeric viewer count -- discard those.
      if (!/^\d[\d.,\s]*[KMB]?$/i.test(t)) {
        return t;
      }
    }
    if (card) {
      const headings = card.querySelectorAll('h1, h2, h3, h4, [data-a-target="search-result-channel-name"], [data-a-target*="title"]');
      for (const h of headings) {
        const ht = text(h);
        if (ht && !isLiveBadge(ht)) return ht;
      }
    }
    if (a.querySelector("img")) {
      const img = a.querySelector("img");
      const alt = (img && img.getAttribute("alt")) || "";
      if (alt && !isLiveBadge(alt)) return alt.trim();
    }
    // Last-resort: derive the title from the slug / id.
    return info.key || "";
  };

  // We restrict our walk to the main content area when possible to avoid
  // sidebar / navigation noise.
  const root = document.querySelector("main") || document.body;
  const out = [];
  const seen = new Set();
  const anchors = root.querySelectorAll("a[href]");

  for (const a of anchors) {
    const href = a.getAttribute("href") || "";
    const info = classify(href);
    if (!info) continue;
    const key = info.type + ":" + info.key;
    if (seen.has(key)) continue;

    const card = cardOf(a);
    const viewersText = (info.type === "channel" || info.type === "live")
      ? viewersFor(card)
      : (info.type === "video" ? viewersFor(card) : "");

    // Promote a "channel" to "live" when its card includes a viewer count.
    let entityType = info.type;
    if (entityType === "channel" && viewersText) entityType = "live";

    const title = titleFor(a, card, info);
    if (!title) continue;
    // Skip tiny navigation pills like "Browse" / "More".
    if (/^(browse|more|see all|show all)$/i.test(title)) continue;

    const game = (entityType === "live" || entityType === "video")
      ? gameFor(card, a)
      : "";
    const channelName = (entityType === "channel" || entityType === "live")
      ? channelNameFor(card, info.key)
      : "";

    seen.add(key);

    out.push({
      type:          entityType,
      key:           info.key,
      href:          href,
      title:         title,
      viewers_text:  viewersText,
      game:          game,
      channel:       channelName,
    });
  }
  return out;
}
"""
