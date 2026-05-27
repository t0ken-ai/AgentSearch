"""App Store URL → app metadata.

Two store front-ends are supported:

* **Apple App Store** (``apps.apple.com/.../id<NUM>``) — uses the
  public iTunes Search API at ``itunes.apple.com/lookup``. No key
  required, JSON in / JSON out.
* **Google Play** (``play.google.com/store/apps/details?id=<PKG>``) —
  scrapes the public details page. Google Play has no official
  unauthenticated API, so we lean on a small set of regexes against
  the SSR HTML.

Both helpers return the same :class:`AppMetadata` dataclass so
downstream callers don't care which store the URL came from.

The whole module is a thin glue layer — ~150 lines — so a competitive
research workflow can do "App Store URL → developer name + domain →
ad library queries" without each caller rewriting the lookup.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# Match free-text mailto / e-mail patterns embedded in app
# descriptions ("Contact: support@example.com" / "mailto:..." etc.).
_EMAIL_RE = re.compile(
    r"(?:mailto:)?([a-zA-Z0-9._+-]{2,}@[a-zA-Z0-9-]+\.[a-zA-Z0-9.-]{2,})"
)
# Best-effort privacy / terms URL extractor against descriptions and
# raw HTML alike. Captures URLs ending in or containing "privacy" /
# "privacy-policy" / "tos" / "terms".
_PRIVACY_URL_RE = re.compile(
    r"(https?://[^\s\"<>)]+?(?:privacy|privacy[-_]?policy|policies)[^\s\"<>)]*)",
    re.IGNORECASE,
)
_TERMS_URL_RE = re.compile(
    r"(https?://[^\s\"<>)]+?(?:terms|tos|eula)[^\s\"<>)]*)",
    re.IGNORECASE,
)


def _extract_contact_links(text: str) -> dict:
    """Pick the most plausible support email / privacy URL / terms URL
    out of arbitrary text (typically an app description or release-
    notes blob).

    Returns a dict with keys ``support_email``, ``privacy_url``,
    ``terms_url`` — empty strings when nothing matched.
    """
    if not text:
        return {"support_email": "", "privacy_url": "", "terms_url": ""}
    em = _EMAIL_RE.search(text)
    pr = _PRIVACY_URL_RE.search(text)
    tr = _TERMS_URL_RE.search(text)
    return {
        "support_email": em.group(1) if em else "",
        "privacy_url":   pr.group(1).rstrip(".,;)") if pr else "",
        "terms_url":     tr.group(1).rstrip(".,;)") if tr else "",
    }


def _request_with_retry(callable_fn, *, max_retries: int = 2,
                        backoff_base: float = 1.0):
    """Call ``callable_fn()`` with retries on transient transport errors.

    Distinguishes between:
      * Transient (retry):  Timeout, ConnectionError, ProxyError, 5xx
      * Final (no retry):   4xx, parse failures (these usually mean the
                            target really doesn't have what we want)

    Backoff: ``backoff_base * (2 ** attempt)`` plus a small jitter.

    Returns whatever ``callable_fn`` returns. Re-raises the last
    exception when all retries are exhausted.
    """
    import random
    import time as _t
    import requests
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            r = callable_fn()
            # If we got a real Response, treat 5xx as transient.
            if hasattr(r, "status_code") and 500 <= r.status_code < 600:
                last_exc = requests.exceptions.HTTPError(
                    f"HTTP {r.status_code}"
                )
                if attempt < max_retries:
                    delay = backoff_base * (2 ** attempt) + random.uniform(0, 0.5)
                    log.debug("[app_store] %s; retry in %.1fs", last_exc, delay)
                    _t.sleep(delay)
                    continue
                raise last_exc
            return r
        except (requests.exceptions.Timeout,
                requests.exceptions.ConnectionError,
                requests.exceptions.ProxyError) as e:
            last_exc = e
            if attempt < max_retries:
                delay = backoff_base * (2 ** attempt) + random.uniform(0, 0.5)
                log.debug("[app_store] transient %s; retry in %.1fs",
                          type(e).__name__, delay)
                _t.sleep(delay)
                continue
            raise
    if last_exc:
        raise last_exc


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------


@dataclass
class AppMetadata:
    """Cross-store unified metadata for a single app."""

    store: str            # "apple" or "google"
    app_id: str           # Apple track id or Google bundle id
    bundle_id: str = ""   # com.foo.bar — same as app_id on Google,
                          # different on Apple
    title: str = ""
    developer_name: str = ""    # Best human-readable name
    seller_name: str = ""       # Apple-only legal name (often differs)
    website: str = ""           # Developer's own URL — golden for ATC
    domain: str = ""            # eTLD+1 of the website (e.g. shopify.com)
    category: str = ""
    rating: float | None = None

    # ── Extended fields for analysis ──────────────────────────────
    description: str = ""
    short_description: str = ""    # First 200 chars / subtitle
    icon_url: str = ""             # Hi-res app icon
    screenshot_urls: list[str] = field(default_factory=list)
    price: float = 0.0             # Numeric price (0 = free)
    price_str: str = ""            # Localized formatted price
    currency: str = ""
    rating_count: int = 0          # Total user ratings
    version: str = ""              # Current version string
    release_date_iso: str = ""     # First-released date (YYYY-MM-DD)
    last_updated_iso: str = ""     # Current version release date
    size_bytes: int = 0
    min_os: str = ""               # Min OS version (e.g. "14.0")
    supported_devices: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    genres: list[str] = field(default_factory=list)
    content_rating: str = ""       # "4+", "Everyone", etc.
    in_app_purchases: bool = False
    release_notes: str = ""        # Latest version's "what's new"
    developer_website: str = ""    # Distinct from `website` for Apple
    privacy_url: str = ""

    # ── Contact / legal links (best-effort extraction) ───────────
    support_url: str = ""          # Developer support page
    support_email: str = ""        # mailto: address (often in
                                   # description / Google Play card)
    terms_url: str = ""            # ToS / EULA URL
    eu_dsa_contact: str = ""       # EU Digital Services Act trader info,
                                   # Google Play exposes for some apps

    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "store": self.store, "app_id": self.app_id,
            "bundle_id": self.bundle_id, "title": self.title,
            "developer_name": self.developer_name,
            "seller_name": self.seller_name, "website": self.website,
            "domain": self.domain, "category": self.category,
            "rating": self.rating,
            "description": self.description,
            "short_description": self.short_description,
            "icon_url": self.icon_url,
            "screenshot_urls": self.screenshot_urls,
            "price": self.price, "price_str": self.price_str,
            "currency": self.currency,
            "rating_count": self.rating_count,
            "version": self.version,
            "release_date_iso": self.release_date_iso,
            "last_updated_iso": self.last_updated_iso,
            "size_bytes": self.size_bytes,
            "min_os": self.min_os,
            "supported_devices": self.supported_devices,
            "languages": self.languages,
            "genres": self.genres,
            "content_rating": self.content_rating,
            "in_app_purchases": self.in_app_purchases,
            "release_notes": self.release_notes,
            "developer_website": self.developer_website,
            "privacy_url": self.privacy_url,
            "support_url": self.support_url,
            "support_email": self.support_email,
            "terms_url": self.terms_url,
            "eu_dsa_contact": self.eu_dsa_contact,
        }


# ---------------------------------------------------------------------------
# URL classification
# ---------------------------------------------------------------------------

_APPLE_HOSTS = {"apps.apple.com", "itunes.apple.com"}
_GOOGLE_HOSTS = {"play.google.com"}

_APPLE_ID_RE = re.compile(r"/id(\d{6,})", re.IGNORECASE)


def classify_app_url(url: str) -> tuple[str, Optional[str]]:
    """Return ``(store, app_id_or_bundle)`` for an App Store URL.

    Examples:
        >>> classify_app_url("https://apps.apple.com/us/app/instagram/id389801252")
        ('apple', '389801252')
        >>> classify_app_url("https://play.google.com/store/apps/details?id=com.instagram.android")
        ('google', 'com.instagram.android')

    Returns ``(store, None)`` on unparseable input.
    """
    if not url or not isinstance(url, str):
        return ("unknown", None)

    try:
        parsed = urllib.parse.urlparse(url.strip())
    except Exception:
        return ("unknown", None)
    host = (parsed.hostname or "").lower()

    if host in _APPLE_HOSTS:
        m = _APPLE_ID_RE.search(parsed.path)
        return ("apple", m.group(1) if m else None)
    if host in _GOOGLE_HOSTS:
        qs = urllib.parse.parse_qs(parsed.query)
        pkg = qs.get("id", [None])[0]
        return ("google", pkg)
    return ("unknown", None)


# ---------------------------------------------------------------------------
# Apple iTunes Search API
# ---------------------------------------------------------------------------


def lookup_apple(app_id: str, *, country: str = "us",
                 proxies: Optional[dict] = None,
                 timeout: int = 20,
                 max_retries: int = 2) -> Optional[AppMetadata]:
    """Look up an Apple App Store entry by numeric track id.

    No auth required. Backed by the public ``itunes.apple.com/lookup``
    endpoint which returns clean JSON. Optional ``country`` switches
    the storefront (US/GB/JP/KR/...). ``proxies`` is forwarded to
    ``requests`` so the call can be routed through a residential pool.

    On transient transport errors (proxy timeout / connection reset /
    5xx) the call retries up to ``max_retries`` times with exponential
    backoff. A successful 200 response with ``resultCount=0`` is NOT
    retried — that's a real "not found".
    """
    import requests

    session = requests.Session()
    if proxies:
        session.proxies.update(proxies)

    def _fetch():
        return session.get(
            "https://itunes.apple.com/lookup",
            params={"id": app_id, "country": country.lower()},
            timeout=timeout,
        )

    try:
        r = _request_with_retry(_fetch, max_retries=max_retries)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("[app_store] apple lookup id=%s failed: %s", app_id, e)
        return None

    if not data.get("resultCount"):
        return None
    app = data["results"][0]

    desc = str(app.get("description") or "")
    # Hi-res icon: Apple gives 60/100/512 — pick 512.
    icon = (app.get("artworkUrl512") or app.get("artworkUrl100")
            or app.get("artworkUrl60") or "")
    # Screenshots: prefer iPhone, fall back to iPad.
    screenshots = list(app.get("screenshotUrls") or [])
    if not screenshots:
        screenshots = list(app.get("ipadScreenshotUrls") or [])

    # Extract contact links from the free-text description (Apple
    # iTunes API doesn't expose support_email or privacyPolicyUrl as
    # structured fields, but developers commonly include them inline).
    contact = _extract_contact_links(desc)

    # Date helper: Apple gives ISO 8601 with time; trim to date.
    def _to_date(s: str) -> str:
        if not s:
            return ""
        return s.split("T", 1)[0]

    return AppMetadata(
        store="apple",
        app_id=str(app.get("trackId") or app_id),
        bundle_id=str(app.get("bundleId") or ""),
        title=str(app.get("trackName") or ""),
        developer_name=str(app.get("artistName") or ""),
        seller_name=str(app.get("sellerName") or ""),
        website=str(app.get("sellerUrl") or ""),
        domain=_domain_of(app.get("sellerUrl") or ""),
        category=str(app.get("primaryGenreName") or ""),
        rating=app.get("averageUserRatingForCurrentVersion") or
               app.get("averageUserRating"),
        description=desc,
        short_description=desc[:200].strip(),
        icon_url=icon,
        screenshot_urls=screenshots[:10],   # cap so JSON output stays sane
        price=float(app.get("price") or 0),
        price_str=str(app.get("formattedPrice") or ""),
        currency=str(app.get("currency") or ""),
        rating_count=int(app.get("userRatingCount") or 0),
        version=str(app.get("version") or ""),
        release_date_iso=_to_date(app.get("releaseDate") or ""),
        last_updated_iso=_to_date(app.get("currentVersionReleaseDate") or ""),
        size_bytes=int(app.get("fileSizeBytes") or 0) if str(app.get("fileSizeBytes") or "").isdigit() else 0,
        min_os=str(app.get("minimumOsVersion") or ""),
        supported_devices=list(app.get("supportedDevices") or [])[:20],
        languages=list(app.get("languageCodesISO2A") or []),
        genres=list(app.get("genres") or []),
        content_rating=str(app.get("contentAdvisoryRating") or app.get("trackContentRating") or ""),
        in_app_purchases=bool(app.get("isVppDeviceBasedLicensingEnabled") or
                              "in-app-purchases" in (app.get("features") or [])),
        release_notes=str(app.get("releaseNotes") or "")[:1000],
        developer_website=str(app.get("sellerUrl") or ""),
        privacy_url=contact["privacy_url"],
        support_url=str(app.get("supportUrl") or app.get("artistViewUrl") or ""),
        support_email=contact["support_email"],
        terms_url=contact["terms_url"],
        eu_dsa_contact="",
        raw=app,
    )


# ---------------------------------------------------------------------------
# Google Play HTML scrape
# ---------------------------------------------------------------------------


_GP_DEV_NAME = re.compile(
    r'"@type"\s*:\s*"Organization"\s*,\s*"name"\s*:\s*"([^"]+)"'
    r'|"name"\s*:\s*"([^"]+)"\s*,\s*"@type"\s*:\s*"Organization"'
)
_GP_DEV_URL = re.compile(
    r'href="(https?://[^"#]+?)"[^>]*\s+aria-label="[^"]*[Ww]ebsite'
)
_GP_TITLE = re.compile(r"<title[^>]*>([^<]+)</title>")
_GP_REDIRECT = re.compile(
    r'href="https://www\.google\.com/url\?q=(https?[^"&]+)'
)
_GP_DESCRIPTION = re.compile(
    r'<meta\s+name="description"\s+content="([^"]+)"',
    re.IGNORECASE,
)
_GP_ICON = re.compile(
    r'<meta\s+property="og:image"\s+content="([^"]+)"',
    re.IGNORECASE,
)
# Rating count: Google Play exposes this as a "ratingCount" field
# in JSON-LD or as visible text like "1,234,567 reviews".
_GP_RATING_COUNT = re.compile(r'"ratingCount"\s*:\s*(\d+)')
_GP_RATING_VALUE = re.compile(r'"ratingValue"\s*:\s*([\d.]+)')
_GP_PRICE = re.compile(r'"price"\s*:\s*"([^"]*)"')
_GP_CURRENCY = re.compile(r'"priceCurrency"\s*:\s*"([^"]*)"')


def lookup_google(package: str, *, hl: str = "en",
                  proxies: Optional[dict] = None,
                  timeout: int = 20,
                  max_retries: int = 2) -> Optional[AppMetadata]:
    """Look up a Google Play entry by package id (e.g. ``com.foo.bar``).

    Google Play has no public API for unauthenticated callers, so this
    fetches the SSR HTML at ``play.google.com/store/apps/details`` and
    runs three small regexes against the embedded JSON-LD blob.

    Retries on transient transport errors (proxy timeout / connection
    reset / 5xx). A 404 returns ``None`` immediately.
    """
    import requests

    session = requests.Session()
    if proxies:
        session.proxies.update(proxies)
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145 Safari/537.36"
        ),
        "Accept-Language": f"{hl},en;q=0.9",
    })

    def _fetch():
        return session.get(
            "https://play.google.com/store/apps/details",
            params={"id": package, "hl": hl},
            timeout=timeout,
        )

    try:
        r = _request_with_retry(_fetch, max_retries=max_retries)
        if r.status_code == 404:
            return None
        r.raise_for_status()
        html = r.text
    except Exception as e:
        log.warning("[app_store] google lookup pkg=%s failed: %s", package, e)
        return None

    dev_match = _GP_DEV_NAME.search(html)
    developer = ""
    if dev_match:
        developer = dev_match.group(1) or dev_match.group(2) or ""

    website = ""
    url_match = _GP_DEV_URL.search(html)
    if url_match:
        website = url_match.group(1)
    else:
        # fallback: first google.com/url?q= redirect on the page is
        # usually the developer's site
        m = _GP_REDIRECT.search(html)
        if m:
            website = urllib.parse.unquote(m.group(1))

    title_match = _GP_TITLE.search(html)
    title = title_match.group(1).strip() if title_match else ""
    # Strip the boilerplate suffix Google adds.
    title = re.sub(r"\s*-\s*Apps on Google Play\s*$", "", title).strip()

    # If the JSON-LD didn't match (Google rotates the markup), derive a
    # reasonable developer-name fallback from the title prefix
    # ("Shopify: Sell online" → "Shopify"). This is just used as a
    # query keyword for ad-library lookups, so close-enough is fine.
    if not developer and title:
        for sep in [":", " - ", " — ", " | "]:
            if sep in title:
                developer = title.split(sep, 1)[0].strip()
                break
        if not developer:
            developer = title

    # Extended fields
    desc_match = _GP_DESCRIPTION.search(html)
    description = desc_match.group(1).strip() if desc_match else ""
    icon_match = _GP_ICON.search(html)
    icon = icon_match.group(1) if icon_match else ""

    rc_match = _GP_RATING_COUNT.search(html)
    rating_count = int(rc_match.group(1)) if rc_match else 0
    rv_match = _GP_RATING_VALUE.search(html)
    rating = float(rv_match.group(1)) if rv_match else None

    price_match = _GP_PRICE.search(html)
    price_str = price_match.group(1) if price_match else ""
    cur_match = _GP_CURRENCY.search(html)
    currency = cur_match.group(1) if cur_match else ""
    # Numeric price: parse "$2.99" / "0" / ""
    price = 0.0
    if price_str:
        p_clean = re.sub(r"[^\d.]", "", price_str)
        if p_clean:
            try:
                price = float(p_clean)
            except ValueError:
                pass

    # Contact / legal — Google Play exposes these reliably in HTML.
    #   - mailto: appears in the "Developer contact" card
    #   - privacy URL appears as a labeled link to a Google redirect
    #     (we unwrap google.com/url?q=... to the real URL)
    em_match = re.search(r'mailto:([a-zA-Z0-9._+\-]+@[a-zA-Z0-9.\-]+)', html)
    support_email = em_match.group(1).strip() if em_match else ""

    # Privacy URL: Google wraps developer external URLs through a
    # google.com/url?q= redirect. Look for the unwrapped real URL.
    privacy_url = ""
    for m in re.finditer(
        r'href="(?:https?://www\.google\.com/url\?q=)?(https?://[^"&]+(?:privacy|policies|policy)[^"&]*)',
        html, re.IGNORECASE,
    ):
        cand = m.group(1)
        if "google.com/policies" in cand or "policies.google.com" in cand:
            continue   # Google's own footer link
        # Unescape \\u003d and similar Google-encoded entities.
        cand = cand.replace("\\u003d", "=").replace("\\u0026", "&")
        privacy_url = cand
        break

    # If we still don't have one, try inside description text as a
    # fallback (some apps just paste the URL into their listing).
    if not privacy_url and description:
        contact = _extract_contact_links(description)
        privacy_url = contact["privacy_url"]
        if not support_email:
            support_email = contact["support_email"]

    return AppMetadata(
        store="google",
        app_id=package,
        bundle_id=package,
        title=title,
        developer_name=developer,
        seller_name=developer,
        website=website,
        domain=_domain_of(website),
        category="",
        rating=rating,
        description=description,
        short_description=description[:200].strip(),
        icon_url=icon,
        screenshot_urls=[],   # Hard to extract reliably from Play HTML
        price=price,
        price_str=price_str,
        currency=currency,
        rating_count=rating_count,
        privacy_url=privacy_url,
        support_url=website,    # Play uses dev's own site as support URL
        support_email=support_email,
        terms_url="",
        eu_dsa_contact="",
        raw={"html_size": len(html)},
    )


# ---------------------------------------------------------------------------
# Combined entry point
# ---------------------------------------------------------------------------


def search_apple(query: str, *, country: str = "us",
                 limit: int = 25, entity: str = "software",
                 proxies: Optional[dict] = None,
                 timeout: int = 20,
                 max_retries: int = 2) -> list[AppMetadata]:
    """Keyword search across the Apple App Store.

    Backed by the public ``itunes.apple.com/search`` endpoint. Returns
    a list of :class:`AppMetadata` instances populated to the same
    depth as :func:`lookup_apple` (the search endpoint already returns
    the full app payload so no second round-trip is needed).

    Args:
        query:   Search terms (e.g. "shopify", "fitness tracker").
        country: Storefront ISO code (us / gb / jp / kr / ...).
        limit:   Max results (Apple caps at 200).
        entity:  ``software`` (iPhone) / ``iPadSoftware`` /
                 ``macSoftware`` / ``softwareDeveloper``.
        proxies: Optional ``requests`` proxy dict.
    """
    import requests

    if not query:
        return []
    session = requests.Session()
    if proxies:
        session.proxies.update(proxies)

    def _fetch():
        return session.get(
            "https://itunes.apple.com/search",
            params={
                "term": query,
                "country": country.lower(),
                "entity": entity,
                "limit": min(max(int(limit), 1), 200),
            },
            timeout=timeout,
        )

    try:
        r = _request_with_retry(_fetch, max_retries=max_retries)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        log.warning("[app_store] apple search %r failed: %s", query, e)
        return []

    out: list[AppMetadata] = []
    for app in data.get("results") or []:
        # Reuse the same field-extraction path as lookup_apple by
        # round-tripping through it via the cached app dict — but the
        # search endpoint already returns the same shape, so we can
        # build the AppMetadata directly to avoid an extra HTTP call.
        m = _apple_dict_to_metadata(app)
        if m:
            out.append(m)
    return out


def _apple_dict_to_metadata(app: dict) -> Optional[AppMetadata]:
    """Convert one Apple iTunes Search/Lookup result dict into
    :class:`AppMetadata`. Centralised so :func:`lookup_apple` and
    :func:`search_apple` stay in sync."""
    if not isinstance(app, dict) or not app.get("trackId"):
        return None

    desc = str(app.get("description") or "")
    icon = (app.get("artworkUrl512") or app.get("artworkUrl100")
            or app.get("artworkUrl60") or "")
    screenshots = list(app.get("screenshotUrls") or [])
    if not screenshots:
        screenshots = list(app.get("ipadScreenshotUrls") or [])
    contact = _extract_contact_links(desc)

    def _to_date(s: str) -> str:
        if not s:
            return ""
        return s.split("T", 1)[0]

    return AppMetadata(
        store="apple",
        app_id=str(app.get("trackId") or ""),
        bundle_id=str(app.get("bundleId") or ""),
        title=str(app.get("trackName") or ""),
        developer_name=str(app.get("artistName") or ""),
        seller_name=str(app.get("sellerName") or ""),
        website=str(app.get("sellerUrl") or ""),
        domain=_domain_of(app.get("sellerUrl") or ""),
        category=str(app.get("primaryGenreName") or ""),
        rating=app.get("averageUserRatingForCurrentVersion") or
               app.get("averageUserRating"),
        description=desc,
        short_description=desc[:200].strip(),
        icon_url=icon,
        screenshot_urls=screenshots[:10],
        price=float(app.get("price") or 0),
        price_str=str(app.get("formattedPrice") or ""),
        currency=str(app.get("currency") or ""),
        rating_count=int(app.get("userRatingCount") or 0),
        version=str(app.get("version") or ""),
        release_date_iso=_to_date(app.get("releaseDate") or ""),
        last_updated_iso=_to_date(app.get("currentVersionReleaseDate") or ""),
        size_bytes=int(app.get("fileSizeBytes") or 0)
                   if str(app.get("fileSizeBytes") or "").isdigit() else 0,
        min_os=str(app.get("minimumOsVersion") or ""),
        supported_devices=list(app.get("supportedDevices") or [])[:20],
        languages=list(app.get("languageCodesISO2A") or []),
        genres=list(app.get("genres") or []),
        content_rating=str(app.get("contentAdvisoryRating")
                           or app.get("trackContentRating") or ""),
        in_app_purchases=bool(app.get("isVppDeviceBasedLicensingEnabled") or
                              "in-app-purchases" in (app.get("features") or [])),
        release_notes=str(app.get("releaseNotes") or "")[:1000],
        developer_website=str(app.get("sellerUrl") or ""),
        privacy_url=contact["privacy_url"],
        support_url=str(app.get("supportUrl") or app.get("artistViewUrl") or ""),
        support_email=contact["support_email"],
        terms_url=contact["terms_url"],
        eu_dsa_contact="",
        raw=app,
    )


def search_google(query: str, *, hl: str = "en",
                  country: str = "us", limit: int = 25,
                  proxies: Optional[dict] = None,
                  timeout: int = 20,
                  max_retries: int = 2,
                  fetch_details: bool = True) -> list[AppMetadata]:
    """Keyword search across Google Play.

    Google Play has no public search API, so this scrapes the public
    search-results HTML at ``play.google.com/store/search`` and
    extracts the top-N package ids. By default each package is then
    looked up via :func:`lookup_google` so the returned objects have
    full metadata (description / email / privacy URL / icon / ...).

    Set ``fetch_details=False`` to skip the per-app round-trips when
    you only need the package list (much faster — about ~2s vs
    ~2s × N).
    """
    import requests

    if not query:
        return []
    session = requests.Session()
    if proxies:
        session.proxies.update(proxies)
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145 Safari/537.36"
        ),
        "Accept-Language": f"{hl},en;q=0.9",
    })

    def _fetch():
        return session.get(
            "https://play.google.com/store/search",
            params={"q": query, "c": "apps", "hl": hl, "gl": country.upper()},
            timeout=timeout,
        )

    try:
        r = _request_with_retry(_fetch, max_retries=max_retries)
        r.raise_for_status()
        html = r.text
    except Exception as e:
        log.warning("[app_store] google search %r failed: %s", query, e)
        return []

    # Pull package ids in order of appearance, dedup preserving order.
    pkgs: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"/store/apps/details\?id=([a-zA-Z0-9._]+)", html):
        pkg = m.group(1)
        if pkg in seen:
            continue
        seen.add(pkg)
        pkgs.append(pkg)
        if len(pkgs) >= limit:
            break

    if not fetch_details:
        # Build minimal AppMetadata stubs from just the package id.
        return [
            AppMetadata(store="google", app_id=p, bundle_id=p)
            for p in pkgs
        ]

    out: list[AppMetadata] = []
    for pkg in pkgs:
        m = lookup_google(pkg, hl=hl, proxies=proxies,
                          timeout=timeout, max_retries=max_retries)
        if m:
            out.append(m)
    return out


def search_app(query: str, *, store: str = "all",
               country: str = "us", limit: int = 25,
               proxies: Optional[dict] = None,
               fetch_details: bool = True) -> list[AppMetadata]:
    """Cross-store keyword search.

    ``store="all"`` returns Apple results first (full metadata) then
    Google Play (slower path; pass ``fetch_details=False`` to skip
    the per-app HTML round-trips).
    """
    s = store.lower()
    out: list[AppMetadata] = []
    if s in ("all", "apple", "ios"):
        out.extend(search_apple(query, country=country, limit=limit,
                                proxies=proxies))
    if s in ("all", "google", "android", "play"):
        out.extend(search_google(query, country=country, limit=limit,
                                 proxies=proxies,
                                 fetch_details=fetch_details))
    return out[:limit] if s == "all" else out


def lookup_app(url_or_id: str, *,
               proxies: Optional[dict] = None,
               country: str = "us") -> Optional[AppMetadata]:
    """One-shot: take any App Store URL (or a bare app id) and return
    metadata, picking the right backend.

    Bare numeric ids are treated as Apple track ids; bare ``com.x.y``
    package strings are treated as Google Play package ids.
    """
    if not url_or_id:
        return None
    s = url_or_id.strip()

    # Bare ids
    if s.isdigit():
        return lookup_apple(s, country=country, proxies=proxies)
    if "/" not in s and "." in s and not s.startswith("http"):
        return lookup_google(s, proxies=proxies)

    store, ident = classify_app_url(s)
    if not ident:
        return None
    if store == "apple":
        return lookup_apple(ident, country=country, proxies=proxies)
    if store == "google":
        return lookup_google(ident, proxies=proxies)
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _domain_of(url: str) -> str:
    """Strip a URL down to its eTLD+1 (best-effort, no PSL).

    Example: ``http://www.shopify.com/mobile`` → ``shopify.com``.
    """
    if not url:
        return ""
    try:
        host = urllib.parse.urlparse(url).hostname or ""
    except Exception:
        return ""
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host
