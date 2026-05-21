"""Product Hunt search adapter.

Product Hunt's web search at ``https://www.producthunt.com/search?q=<q>``
is a heavily JS-rendered Next.js application. The page renders product
cards asynchronously, but unlike Medium it does not gate plain
``GET /search?q=…`` behind a Cloudflare interstitial in our testing —
it does, however, occasionally show a "Sign in" overlay or rate-limit
unauthenticated visitors. To stay robust we layer two modes
(mirroring ``medium.py``):

1. **producthunt_direct** — Navigate to
   ``producthunt.com/search?q=<q>`` and scrape the rendered product
   cards. Selectors vary between layout revisions, so we probe a list
   of known candidates and record which one matched on
   ``last_status['selector']``.

2. **ddg_site** — Last-resort fallback through the HTML-only
   DuckDuckGo endpoint with ``site:producthunt.com/products <query>``.
   We can pull product name + URL + a coarse tagline snippet, but no
   upvote / review count.

Each mode short-circuits on success. If a mode returns 0 parseable
results (or is rate-limited / blocked) the adapter falls through.

``SearchResult`` (see ``base.py``) carries ``title`` / ``url`` /
``snippet`` / ``score``. To preserve Product Hunt-specific metadata:

* ``title``        — product name (e.g. ``"Lovable"``).
* ``url``          — absolute Product Hunt URL
  (``https://www.producthunt.com/products/<slug>``).
* ``snippet``      — composed as
  ``"<tagline> · <N> upvotes · <M> reviews · [cat1, cat2]"`` with
  missing parts dropped.
* ``score``        — integer upvote count when extractable, ``None``
  otherwise. Product Hunt's search cards usually surface either
  upvotes or review count depending on the row layout; we prefer
  upvotes and fall back to ``None``.

Every returned ``SearchResult`` has the following attributes set
dynamically (the dataclass has no ``__slots__`` so this is supported):

  - ``name``        (str) — same as ``title``; kept for callers that
    want a product-specific accessor.
  - ``tagline``     (str) — short tagline / one-liner under the name.
  - ``upvotes``     (int | None) — same as ``score``.
  - ``reviews``     (int | None) — review count when extractable.
  - ``categories``  (list[str]) — category chip labels found on the
    card. Often empty on the search-results page (categories live on
    the product detail page). DDG fallback always returns ``[]``.

Diagnostics
-----------

* ``engine.last_status`` — ``mode``, ``url``, ``title``, ``body_len``,
  optional ``selector`` / ``block_reason`` / ``count``.
* ``engine.selector_counts()`` — per-selector counts useful across
  both modes so test scripts can show why parsing missed.
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

PRODUCTHUNT_HOME = "https://www.producthunt.com"

# ---- producthunt_direct ----------------------------------------------------

# Product card container selectors for producthunt.com/search?q=…, in
# priority order. Product Hunt's Next.js app revises this markup
# regularly; we list the variants we've observed so the engine
# survives layout flips. The first entry that produces hits is
# recorded on ``last_status['selector']``.
DIRECT_RESULT_SELECTORS = [
    'div[data-test^="search-result"]',          # explicit search test attr
    'section[data-test^="post-item"]',          # post-item rows (legacy)
    'div[data-test^="post-item"]',              # post-item rows variant
    'div[data-test^="product-item"]',           # product-item rows
    'li[data-test^="product-item"]',            # product-item list variant
    'div[class*="styles_item"]',                # CSS-modules class probe
    'div[class*="searchResult"]',               # CSS-modules class probe
]

# Phrases that indicate Product Hunt / Cloudflare blocked us.
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
]

# ---- ddg_site --------------------------------------------------------------

DDG_HTML_ENDPOINT = "https://html.duckduckgo.com/html/"

# Rough cap on how many DOM cards we'll inspect per page; PH's search
# lazy-loads more on scroll, but we don't need a deep page.
MAX_CARDS_TO_SCAN = 80


# ----------------------------------------------------------------------------


def _abs_ph(href: str) -> str:
    """Normalize a relative Product Hunt URL to an absolute one."""
    if not href:
        return href
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("//"):
        return "https:" + href
    if href.startswith("/"):
        return PRODUCTHUNT_HOME + href
    return PRODUCTHUNT_HOME + "/" + href


def _strip_url_query(href: str) -> str:
    """Drop tracking params (``?ref=…``) for stable de-duping."""
    if not href:
        return href
    return href.split("?", 1)[0].split("#", 1)[0]


def _parse_int(text: str) -> int | None:
    """Parse a count string ('123', '1.2K', '4.5M', '1,234')."""
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


_UPVOTES_RE = re.compile(r"(\d[\d,.]*\s*[KMkm]?)\s*(?:up\s*votes?|upvotes?)", re.I)
_REVIEWS_RE = re.compile(r"(\d[\d,.]*\s*[KMkm]?)\s*reviews?", re.I)


def _extract_upvotes(text: str) -> int | None:
    if not text:
        return None
    m = _UPVOTES_RE.search(text)
    if not m:
        return None
    return _parse_int(m.group(1))


def _extract_reviews(text: str) -> int | None:
    if not text:
        return None
    m = _REVIEWS_RE.search(text)
    if not m:
        return None
    return _parse_int(m.group(1))


def _looks_like_product_url(href: str) -> bool:
    """Heuristic: is this a Product Hunt product URL (not a forum / category)?

    Real product detail pages live under ``/products/<slug>``. The
    older ``/posts/<slug>`` namespace also still resolves and is used
    on launch / leaderboard pages. Anything under ``/categories/``,
    ``/p/``, ``/forums``, ``/leaderboard`` etc. is rejected. We also
    reject sub-pages such as ``/products/<slug>/reviews`` which are
    typically sidebar links rather than real search results.
    """
    if not href:
        return False
    path = urllib.parse.urlparse(href).path or ""
    if not path or path == "/":
        return False
    # Product detail pages.
    if path.startswith("/products/") or path.startswith("/posts/"):
        segs = [s for s in path.split("/") if s]
        # Canonical /products/<slug> only — sub-pages like /reviews,
        # /awards, /alternatives, /launches are sidebar/nav links and
        # not real search results.
        if len(segs) == 2 and segs[0] in ("products", "posts"):
            return True
    return False


# ``?ref=`` query values that appear on sidebar / nav / footer links
# we want to filter out of the anchor walk. PH stamps these on every
# link so we can discriminate sidebar from actual search results.
SIDEBAR_REF_VALUES = {
    "footer",
    "header_nav",
    "header",
    "nav",
    "homepage",
    "trending",
    "trending_categories",
    "top_reviewed",
    "trending_products",
    "top_forum_threads",
    "sidebar",
}


def _is_sidebar_ref(href: str) -> bool:
    """True if the URL has a ``?ref=`` value that marks it as a
    sidebar / nav / footer link."""
    if not href or "?" not in href:
        return False
    try:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
    except Exception:
        return False
    refs = qs.get("ref", [])
    for r in refs:
        if r.lower() in SIDEBAR_REF_VALUES:
            return True
    return False


def _anchor_in_chrome(anchor) -> bool:
    """Return True if the anchor element sits inside an <aside>,
    <nav>, <footer>, or <header> ancestor (page chrome rather than
    main content)."""
    try:
        return bool(
            anchor.evaluate(
                """el => {
                    let p = el.parentElement;
                    while (p) {
                        const tag = (p.tagName || '').toLowerCase();
                        if (tag === 'aside' || tag === 'nav'
                            || tag === 'footer' || tag === 'header') {
                            return true;
                        }
                        p = p.parentElement;
                    }
                    return false;
                }"""
            )
        )
    except Exception:
        return False


def _canonicalize_product_url(href: str) -> str:
    """Reduce ``/products/<slug>/<sub>`` to the canonical
    ``/products/<slug>`` URL so ``/products/foo`` and
    ``/products/foo/reviews`` dedupe to the same product."""
    if not href:
        return href
    parsed = urllib.parse.urlparse(href)
    segs = [s for s in (parsed.path or "").split("/") if s]
    if len(segs) >= 2 and segs[0] in ("products", "posts"):
        new_path = "/" + "/".join(segs[:2])
        return urllib.parse.urlunparse(
            (parsed.scheme, parsed.netloc, new_path, "", "", "")
        )
    return _strip_url_query(href)


def _compose_snippet(
    tagline: str,
    upvotes: int | None,
    reviews: int | None,
    categories: list[str],
) -> str:
    """Render tagline + upvote/review counts + categories;
    parts omitted when empty."""
    parts: list[str] = []
    if tagline:
        parts.append(tagline)
    meta: list[str] = []
    if upvotes is not None:
        meta.append(f"{upvotes:,} upvotes")
    if reviews is not None:
        meta.append(f"{reviews:,} reviews")
    if categories:
        meta.append("[" + ", ".join(categories) + "]")
    if meta:
        parts.append(" · ".join(meta))
    return " — ".join(parts) if len(parts) > 1 else (parts[0] if parts else "")


def _attach_extras(
    r: SearchResult,
    *,
    name: str,
    tagline: str,
    upvotes: int | None,
    reviews: int | None,
    categories: list[str],
) -> SearchResult:
    r.name = name
    r.tagline = tagline
    r.upvotes = upvotes
    r.reviews = reviews
    r.categories = categories
    return r


# ----------------------------------------------------------------------------


class ProductHuntSearchEngine(BaseEngine):
    """Search Product Hunt for products."""

    name = "producthunt"
    max_retries = 3

    _MODE_ORDER: tuple[str, ...] = ("producthunt_direct", "ddg_site")

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
                if mode == "producthunt_direct":
                    results = self._try_producthunt_direct(query, limit)
                elif mode == "ddg_site":
                    results = self._try_ddg_site(query, limit)
                else:  # pragma: no cover — _MODE_ORDER guards this.
                    results = []
            except Exception as e:
                log.warning("[producthunt] %s raised: %s", mode, e)
                results = []
            if results:
                self._last_mode = mode
                return results
        return []

    # ----------------------------------------------- producthunt_direct mode

    def _try_producthunt_direct(
        self, query: str, limit: int
    ) -> list[SearchResult]:
        """Navigate to producthunt.com/search?q=… and scrape rendered cards."""
        # Warm up on the homepage so cookies settle before /search.
        if safe_goto(self.page, PRODUCTHUNT_HOME + "/", timeout=20000, retries=1):
            human_delay(1.0, 2.0)
            self._human_hints()

        q = urllib.parse.quote(query)
        url = f"{PRODUCTHUNT_HOME}/search?q={q}"
        log.info("[producthunt] direct search: %s", url)
        if not safe_goto(self.page, url, timeout=30000, retries=1):
            self.last_status = {
                "mode": "producthunt_direct",
                "error": "goto_failed",
            }
            return []

        self._pages_fetched = 1

        # Wait for at least one candidate selector to appear; tolerate
        # timeout (we'll fall through if nothing appears).
        for sel in DIRECT_RESULT_SELECTORS + ['a[href^="/products/"]']:
            try:
                self.page.wait_for_selector(sel, timeout=6000)
                break
            except Exception:
                continue

        # Wait for the SPA to finish its async data fetch — PH renders
        # the static page shell first and then hydrates the search
        # results from a client-side query, so we need to wait until
        # the network settles (or time out at 20s) before scraping.
        try:
            self.page.wait_for_load_state("networkidle", timeout=20000)
        except Exception:
            pass

        human_delay(1.5, 3.0)
        self._human_hints()

        # Multiple scrolls to encourage lazy-loaded cards to render.
        # PH's search lazy-loads more cards on scroll past 60% of the
        # viewport, so we step through the page rather than jumping.
        for frac in (0.4, 0.7, 1.0):
            try:
                self.page.evaluate(
                    "(f) => window.scrollTo(0, document.body.scrollHeight * f)",
                    frac,
                )
            except Exception:
                pass
            human_delay(0.8, 1.5)

        # Settle once more after scrolling.
        try:
            self.page.wait_for_load_state("networkidle", timeout=10000)
        except Exception:
            pass

        if self._is_blocked("producthunt_direct"):
            return []

        # Try the structured selectors first.
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

        results: list[SearchResult] = []
        seen: set[str] = set()

        if items:
            log.info(
                "[producthunt] direct via %s (%d items)", used, len(items)
            )
            self.last_status["selector"] = used
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

        # Fallback: if no structured selector matched (or it produced
        # nothing parseable), walk every ``a[href^="/products/"]``
        # anchor on the page and synthesize cards from each anchor's
        # nearest containing parent.
        if not results:
            log.info(
                "[producthunt] no structured items — falling back to "
                "anchor walk"
            )
            results = self._extract_via_anchor_walk(limit)
            if results:
                self.last_status["selector"] = 'a[href^="/products/"]'

        if not results:
            self.last_status.setdefault("mode", "producthunt_direct")
            self.last_status["count"] = 0
            return []

        self.last_status["mode"] = "producthunt_direct"
        self.last_status["count"] = len(results)
        return results

    def _extract_direct_item(self, r) -> SearchResult | None:
        """Pull a single SearchResult out of one structured DOM card."""
        try:
            full_text = (r.inner_text() or "").strip()
        except Exception:
            full_text = ""

        # Find the product link. PH puts multiple <a> tags inside each
        # card (image link, name link, category chips). We pick the
        # first /products/<slug> anchor and treat it as canonical.
        href = ""
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
            h_abs = _abs_ph(h)
            anchor_info.append((h_abs, t))
            if not href and _looks_like_product_url(h_abs):
                href = h_abs

        if not href:
            return None

        href = _canonicalize_product_url(href)

        return self._build_result_from_card(
            full_text=full_text,
            href=href,
            anchor_info=anchor_info,
            container=r,
        )

    def _extract_via_anchor_walk(self, limit: int) -> list[SearchResult]:
        """Fallback: walk every ``a[href^="/products/"]`` anchor.

        For each unique product URL found on the page, reach up to a
        reasonable parent container and synthesize a card from its
        text. This handles layouts where the structured ``data-test``
        wrapper has been renamed without warning.

        Anchors that sit inside <aside>/<nav>/<footer>/<header>
        ancestors (sidebar / nav / page chrome) are skipped, as are
        anchors with ``?ref=footer`` / ``?ref=top_reviewed`` etc. and
        sub-page paths like ``/products/<slug>/reviews``.
        """
        try:
            anchors = self.page.query_selector_all('a[href^="/products/"]')
        except Exception:
            anchors = []
        log.info(
            "[producthunt] anchor walk: %d /products/ anchors", len(anchors)
        )

        results: list[SearchResult] = []
        seen: set[str] = set()
        rejected_chrome = 0
        rejected_subpath = 0
        rejected_ref = 0
        for a in anchors[: limit * 12]:
            try:
                h = a.get_attribute("href") or ""
            except Exception:
                continue
            if not h:
                continue
            h_abs = _abs_ph(h)
            if not _looks_like_product_url(h_abs):
                rejected_subpath += 1
                continue
            if _is_sidebar_ref(h_abs):
                rejected_ref += 1
                continue
            if _anchor_in_chrome(a):
                rejected_chrome += 1
                continue
            canonical = _canonicalize_product_url(h_abs)
            if canonical in seen:
                continue
            seen.add(canonical)

            # Walk up to the nearest container that wraps both the
            # name link and the tagline. We try the parent chain up to
            # 5 levels and pick the first parent whose text is
            # noticeably longer than the anchor's own text and whose
            # contents reference only this single product (not a
            # sidebar list of multiple products).
            container = a
            try:
                anchor_text = (a.inner_text() or "").strip()
            except Exception:
                anchor_text = ""
            for _ in range(5):
                try:
                    parent = container.evaluate_handle(
                        "el => el.parentElement"
                    ).as_element()
                except Exception:
                    parent = None
                if parent is None:
                    break
                container = parent
                try:
                    parent_text = (container.inner_text() or "").strip()
                except Exception:
                    parent_text = ""
                # If the parent has at least some context beyond the
                # anchor's text (tagline / metadata), use it.
                if len(parent_text) >= len(anchor_text) + 20:
                    break

            # Sanity check: if the chosen container references multiple
            # product slugs, it's a sidebar list — skip this anchor.
            try:
                inner_product_anchors = container.query_selector_all(
                    'a[href^="/products/"]'
                )
            except Exception:
                inner_product_anchors = []
            distinct_slugs: set[str] = set()
            for ia in inner_product_anchors:
                try:
                    ih = ia.get_attribute("href") or ""
                except Exception:
                    ih = ""
                if not ih:
                    continue
                cp = urllib.parse.urlparse(_abs_ph(ih)).path or ""
                segs = [s for s in cp.split("/") if s]
                if len(segs) >= 2 and segs[0] == "products":
                    distinct_slugs.add(segs[1])
                if len(distinct_slugs) > 1:
                    break
            if len(distinct_slugs) > 1:
                rejected_chrome += 1
                continue

            try:
                full_text = (container.inner_text() or "").strip()
            except Exception:
                full_text = ""

            # Re-collect anchors inside this container so we can spot
            # category chips and product-detail links.
            try:
                inner_anchors = container.query_selector_all("a[href]")
            except Exception:
                inner_anchors = []
            anchor_info: list[tuple[str, str]] = []
            for ia in inner_anchors:
                try:
                    ih = ia.get_attribute("href") or ""
                    it = (ia.inner_text() or "").strip()
                except Exception:
                    continue
                if not ih:
                    continue
                anchor_info.append((_abs_ph(ih), it))

            sr = self._build_result_from_card(
                full_text=full_text,
                href=canonical,
                anchor_info=anchor_info,
                container=container,
            )
            if sr is None:
                continue
            results.append(sr)
            if len(results) >= limit:
                break
        log.info(
            "[producthunt] anchor walk filtered: subpath=%d ref=%d chrome=%d kept=%d",
            rejected_subpath, rejected_ref, rejected_chrome, len(results),
        )
        return results

    def _build_result_from_card(
        self,
        *,
        full_text: str,
        href: str,
        anchor_info: list[tuple[str, str]],
        container,
    ) -> SearchResult | None:
        """Common card-to-SearchResult construction shared by both
        the structured and anchor-walk paths."""
        # Product name: prefer the first non-empty inner_text of an
        # anchor that points to the product URL itself. We canonicalize
        # both sides so ``/products/lovable/reviews`` matches
        # ``/products/lovable``.
        name = ""
        product_path = urllib.parse.urlparse(href).path or ""
        for h_abs, t in anchor_info:
            ap_canon = urllib.parse.urlparse(
                _canonicalize_product_url(h_abs)
            ).path or ""
            if ap_canon == product_path and t and t.strip():
                # Skip generic labels like image alts ("Featured", "AD").
                cand = t.strip()
                # Reject single-character / pure-number / boilerplate.
                if len(cand) >= 2 and cand.lower() not in {
                    "ad", "ads", "featured", "sponsored", "new",
                    "view", "visit", "open", "details",
                }:
                    name = cand
                    break

        if not name:
            try:
                h_el = (
                    container.query_selector("h3")
                    or container.query_selector("h4")
                    or container.query_selector('[data-test*="title" i]')
                )
                if h_el:
                    h_text = (h_el.inner_text() or "").strip()
                    if h_text:
                        name = h_text
            except Exception:
                pass

        if not name:
            # Last-resort fallback: derive a name from the URL slug.
            # We deliberately avoid taking the first line of full_text
            # because the parent walk may have reached a sidebar list.
            segs = [s for s in product_path.split("/") if s]
            if len(segs) >= 2:
                name = segs[1].replace("-", " ").title()

        if not name:
            return None

        # Tagline: PH usually renders the tagline directly after the
        # product name on the same row. Try a couple of approaches:
        #   1) Look for an explicit tagline element by data-test.
        #   2) Take the line of full_text that comes immediately after
        #      the name and isn't a metadata token (reviews / upvotes /
        #      category).
        tagline = ""
        try:
            tl_el = (
                container.query_selector('[data-test*="tagline" i]')
                or container.query_selector('[class*="tagline" i]')
                or container.query_selector('[class*="Tagline" i]')
            )
            if tl_el:
                t_text = (tl_el.inner_text() or "").strip()
                if t_text and t_text != name:
                    tagline = t_text
        except Exception:
            pass

        if not tagline and full_text:
            lines = [
                ln.strip()
                for ln in full_text.splitlines()
                if ln.strip()
            ]
            try:
                idx = lines.index(name)
            except ValueError:
                idx = -1
            if idx >= 0 and idx + 1 < len(lines):
                cand = lines[idx + 1]
                # Reject metadata tokens.
                if not _UPVOTES_RE.search(cand) and not _REVIEWS_RE.search(
                    cand
                ):
                    tagline = cand
            # Fallback: first line after name that doesn't match
            # metadata.
            if not tagline:
                for ln in lines:
                    if (
                        ln
                        and ln != name
                        and not _UPVOTES_RE.search(ln)
                        and not _REVIEWS_RE.search(ln)
                        and len(ln) > 8
                    ):
                        tagline = ln
                        break

        # Trim very long taglines (PH cards can include the entire
        # description if our parent walk reached too high).
        if tagline and len(tagline) > 240:
            tagline = tagline[:240].rstrip() + "…"

        # Categories: look for /categories/ anchors inside the card.
        categories: list[str] = []
        seen_cats: set[str] = set()
        for h_abs, t in anchor_info:
            ap = urllib.parse.urlparse(h_abs).path or ""
            if not ap.startswith("/categories/"):
                continue
            label = (t or "").strip()
            # Use the slug as a fallback label if the link has no text.
            if not label:
                segs = [s for s in ap.split("/") if s]
                if len(segs) >= 2:
                    label = segs[1].replace("-", " ").title()
            if not label:
                continue
            key = label.lower()
            if key in seen_cats:
                continue
            seen_cats.add(key)
            categories.append(label)

        upvotes = _extract_upvotes(full_text)
        reviews = _extract_reviews(full_text)

        sr = SearchResult(
            title=name,
            url=href,
            snippet=_compose_snippet(tagline, upvotes, reviews, categories),
            score=upvotes,
        )
        return _attach_extras(
            sr,
            name=name,
            tagline=tagline,
            upvotes=upvotes,
            reviews=reviews,
            categories=categories,
        )

    # -------------------------------------------------------- ddg_site mode

    def _try_ddg_site(self, query: str, limit: int) -> list[SearchResult]:
        site_query = f"site:producthunt.com/products {query}"
        q = urllib.parse.quote(site_query)
        url = f"{DDG_HTML_ENDPOINT}?q={q}"
        log.info("[producthunt] ddg site search: %s", url)
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
        log.info("[producthunt] ddg got %d .result items", len(items))

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
            if "producthunt.com" not in href.lower():
                continue
            if not _looks_like_product_url(href):
                continue

            href = _canonicalize_product_url(href)
            key = _strip_url_query(href)
            if key in seen:
                continue
            seen.add(key)

            # Title sometimes carries " | Product Hunt" suffix; trim it.
            name = re.sub(
                r"\s*[\|\u2013\-]\s*Product\s*Hunt\s*$", "", title, flags=re.I
            ).strip()
            if not name:
                name = title

            upvotes = _extract_upvotes(snippet)
            reviews = _extract_reviews(snippet)

            sr = SearchResult(
                title=name,
                url=href,
                snippet=snippet,
                score=upvotes,
            )
            _attach_extras(
                sr,
                name=name,
                tagline=snippet,
                upvotes=upvotes,
                reviews=reviews,
                categories=[],
            )
            results.append(sr)
            if len(results) >= limit:
                break

        if results:
            self.last_status["count"] = len(results)
        return results

    # -------------------------------------------------------- block detection

    def _is_blocked(self, mode: str) -> bool:
        """Detect Cloudflare / Product Hunt interstitials and rate-limits."""
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
            "mode": mode,
            "url": url,
            "title": title,
            "body_len": len(body),
        }

        # PH occasionally redirects to /sign-up if rate-limited; treat
        # as a soft block so we fall through to ddg_site.
        if "/sign-up" in url or "/sign_in" in url or "/login" in url:
            self.last_status["block_reason"] = "auth_wall"
            log.warning("[producthunt] auth wall: %s", url)
            return True

        head = body[:3000]
        for phrase in BLOCK_PHRASES:
            if phrase in head or phrase in title:
                self.last_status["block_reason"] = phrase
                log.warning("[producthunt] block phrase detected: %r", phrase)
                return True
        return False

    # -------------------------------------------------------------- diagnostics

    def selector_counts(self) -> dict[str, int]:
        """Per-selector counts; safe to call regardless of last_mode."""
        counts: dict[str, int] = {}
        probe = [
            'div[data-test^="search-result"]',
            'section[data-test^="post-item"]',
            'div[data-test^="post-item"]',
            'div[data-test^="product-item"]',
            'li[data-test^="product-item"]',
            'a[href^="/products/"]',
            'a[href^="/posts/"]',
            'a[href^="/categories/"]',
            "h3",
            "h4",
            ".result",
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
