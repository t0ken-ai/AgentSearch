"""Shared scaffolding for image-search engine adapters.

Image engines are a parallel hierarchy to text-search engines:

* :class:`ImageSearchEngine` inherits :class:`~.base.BaseEngine` so it
  gets picked up by ``cli._engine_registry`` automatically.
* :class:`ImageSearchResult` is the per-result dataclass — parallel to
  :class:`~.base.SearchResult` but exposes image-specific fields
  (image / thumbnail / source-page URLs, width / height).
* The class attribute ``is_image_engine = True`` lets the MCP server
  filter "image-capable" engines from "text-only" ones.

Common helpers:

* :func:`scrape_imgs_from_dom` — fallback last-resort scraper that
  collects every visible ``<img>`` from the page. Useful as a safety
  net when the engine-specific JSON / DOM parser fails.
* :func:`absolutize_url` — turn a relative ``//x.com/y.jpg`` or
  ``/y.jpg`` into an absolute one, given the page's base URL.
* :func:`looks_like_image_url` — heuristic check used to filter out
  tracking pixels, sprites, layout SVGs, and the like.

Engines should override ``_do_image_search`` (NOT ``_do_search``)
because :meth:`ImageSearchEngine.search` returns the wrong type for
the BaseEngine retry wrapper. We therefore also override ``search``
to call ``_do_image_search`` directly.
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from dataclasses import dataclass, field, asdict
from typing import Any

from .base import BaseEngine

log = logging.getLogger(__name__)


# Tracking pixels / sprites / data URIs we never want to surface.
_BLOCKED_IMG_PATTERNS = (
    "data:image/gif;base64,",
    "spacer.gif",
    "1x1.gif",
    "pixel.gif",
    "/_next/static/",   # Next.js layout assets
    "favicon",
)

# Minimum displayed dimensions (in px) to consider something a
# "result image". Anything smaller is almost certainly UI chrome.
_MIN_IMG_DIM = 60


@dataclass
class ImageSearchResult:
    """One image-search hit.

    Field semantics:

    * ``image_url`` — the URL we'd download to obtain the image bytes.
      For some engines (Google) this is the on-engine thumbnail rather
      than the original — see the engine's docstring for trade-offs.
    * ``thumbnail_url`` — explicitly a thumb (smaller). Often the same
      as ``image_url`` when the engine doesn't expose a separate full
      version in the DOM.
    * ``source_page_url`` — the host page of the image (so the agent
      can attribute / re-extract / open the source).
    * ``width`` / ``height`` — declared dimensions, when known.
    * ``title`` — image alt / caption.
    * ``source_engine`` — handle of the engine that surfaced this hit.
    """

    image_url: str = ""
    thumbnail_url: str = ""
    source_page_url: str = ""
    title: str = ""
    width: int | None = None
    height: int | None = None
    source_engine: str = ""
    # Engine-specific extras (mime type, file size hint, dominant color, …).
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def absolutize_url(url: str, base: str) -> str:
    """Return an absolute http(s) URL given a possibly-relative input.

    Drops obviously-bogus inputs (empty, ``data:``, ``javascript:``).
    """
    if not url:
        return ""
    u = url.strip()
    if not u or u.startswith(("data:", "javascript:", "blob:", "mailto:")):
        return ""
    if u.startswith("//"):
        return "https:" + u
    if u.startswith(("http://", "https://")):
        return u
    if u.startswith("/"):
        try:
            p = urllib.parse.urlparse(base)
            return f"{p.scheme}://{p.netloc}{u}"
        except Exception:
            return u
    # Relative-to-page paths — best effort.
    try:
        return urllib.parse.urljoin(base, u)
    except Exception:
        return u


def looks_like_image_url(url: str) -> bool:
    """Cheap heuristic: is this URL likely to point at a real image?

    True iff:
      * scheme is http(s),
      * not in the blocked-pattern list,
      * EITHER ends in a common image extension OR contains a known
        image-CDN host marker.
    """
    if not url or not url.startswith(("http://", "https://")):
        return False
    low = url.lower()
    for bad in _BLOCKED_IMG_PATTERNS:
        if bad in low:
            return False
    if re.search(r"\.(jpe?g|png|gif|webp|bmp|tiff?|svg|avif)(\?|#|$)", low):
        return True
    # Common CDN markers that don't expose a file extension in the URL
    cdn_markers = (
        "encrypted-tbn", "gstatic.com", "ggpht.com", "googleusercontent.com",
        "ytimg.com", "fbcdn.net", "twimg.com", "redditmedia.com",
        "cdn.bing.com", "th.bing.com", "tse4.mm.bing.net",
        "ddmcdn.com", "pinimg.com", "yandeximg.com", "bdimg.com",
        "bdstatic.com", "sogoucdn.com", "image.so.com", "qhimg.com",
        "naver.net", "pstatic.net", "yimg.jp", "kakaocdn.net",
        "daumcdn.net", "yandex.net", "yandex.com", "mail.ru",
        # Brave
        "imgs.search.brave.com", "imgs.search.brave",
        # External-content proxies
        "external-content.duckduckgo.com",
        # Generic image CDNs (catch-all)
        "/images/", "/img/", "/photo/", "/photos/", "/media/",
    )
    return any(marker in low for marker in cdn_markers)


# A safe-net DOM scraper: collect every visible <img>. Used when
# engine-specific selectors fail. Filters out small/tracking images.
_DOM_SCRAPE_JS = r"""
(minDim) => {
  function attr(el, k) { return el && el.getAttribute(k); }
  const out = [];
  const seen = new Set();
  for (const img of document.querySelectorAll('img')) {
    const candidates = [
      attr(img, 'src'),
      attr(img, 'data-src'),
      attr(img, 'data-original'),
      attr(img, 'data-lazy'),
      attr(img, 'data-iurl'),
      attr(img, 'data-imurl'),
      attr(img, 'data-url'),
    ];
    let url = '';
    for (const c of candidates) {
      if (c && (c.startsWith('http') || c.startsWith('//'))) { url = c; break; }
    }
    if (!url) continue;
    if (seen.has(url)) continue;
    // Skip tiny images (icons / pixels)
    const w = img.naturalWidth || img.width || 0;
    const h = img.naturalHeight || img.height || 0;
    if (w && w < minDim) continue;
    if (h && h < minDim) continue;
    seen.add(url);
    // Try to find the parent anchor for source-page url
    let pageUrl = '';
    let parent = img.parentElement;
    for (let i = 0; i < 6 && parent; i++) {
      if (parent.tagName === 'A' && parent.getAttribute('href')) {
        pageUrl = parent.getAttribute('href');
        break;
      }
      parent = parent.parentElement;
    }
    out.push({
      url: url,
      pageUrl: pageUrl,
      alt: img.getAttribute('alt') || '',
      w: w || null, h: h || null,
    });
  }
  return out;
}
"""


def scrape_imgs_from_dom(
    page,
    *,
    min_dim: int = _MIN_IMG_DIM,
    base_url: str | None = None,
) -> list[dict[str, Any]]:
    """Last-resort scraper — collect every visible <img> on the page.

    Returns a list of raw dicts ``{url, pageUrl, alt, w, h}``. Caller
    is responsible for filtering, deduplicating, and normalising.
    """
    try:
        raw = page.evaluate(_DOM_SCRAPE_JS, min_dim) or []
    except Exception as e:
        log.debug("[image-base] DOM scrape JS failed: %s", e)
        return []
    if base_url is None:
        try:
            base_url = page.url
        except Exception:
            base_url = ""
    out: list[dict[str, Any]] = []
    for r in raw:
        u = absolutize_url(r.get("url", ""), base_url or "")
        if not looks_like_image_url(u):
            continue
        page_u = absolutize_url(r.get("pageUrl", ""), base_url or "")
        out.append({
            "url": u,
            "pageUrl": page_u,
            "alt": (r.get("alt") or "").strip(),
            "w": r.get("w"),
            "h": r.get("h"),
        })
    return out


class ImageSearchEngine(BaseEngine):
    """Base class for image-search adapters.

    Subclasses implement :meth:`_do_image_search` instead of
    :meth:`_do_search`. We override :meth:`search` so the wrong return
    type doesn't trip up :class:`BaseEngine`'s retry wrapper.
    """

    name: str = "image-base"
    is_image_engine: bool = True

    def search(self, query: str, limit: int = 20) -> list[ImageSearchResult]:
        """Run the image search; never raises (returns [] on failure)."""
        try:
            results = self._do_image_search(query, limit)
        except Exception as e:
            log.warning("[%s] image search failed: %s", self.name, e)
            return []
        # Stamp source_engine + dedupe by image_url
        seen: set[str] = set()
        out: list[ImageSearchResult] = []
        for r in results or []:
            if not r.image_url or r.image_url in seen:
                continue
            seen.add(r.image_url)
            r.source_engine = r.source_engine or self.name
            out.append(r)
            if len(out) >= limit:
                break
        return out

    def _do_image_search(
        self, query: str, limit: int
    ) -> list[ImageSearchResult]:
        raise NotImplementedError
