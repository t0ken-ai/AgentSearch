"""Google Ads Transparency Center engine.

Background
----------
Google's Ads Transparency Center (``adstransparency.google.com``) is the
public portal Google launched in 2023 (mandated by the EU DSA) that
exposes every active ad on Google Search / Shopping / Display / YouTube /
Maps with verified-advertiser status, first/last-seen dates, geographic
targeting, and the original creative.

The frontend is a Java/Closure SPA (``anji`` framework) that calls
internal RPC endpoints under ``/anji/_/rpc/<Service>/<Method>``. Those
RPCs use Google's protobuf-style JSON encoding where field names are
**positional integers** (``{"1": ..., "2": ...}``) instead of human
names — this is why scraping it manually is annoying.

Strategy
--------
Same pattern as ``meta_ad_library``: navigate the relevant page, intercept
the RPC response, decode the integer field positions back to human field
names. Three RPC endpoints we tap:

1. ``SearchService/SearchSuggestions`` — keyword → advertiser list.
2. ``SearchService/SearchCreatives``   — advertiser_id → creative list,
                                        OR domain → advertiser_id.
3. ``LookupService/GetCreativeById``   — single ad full detail
                                        (text decoded from base64 protobuf).

The protocol decoding is borrowed from ``block-town/google-ads-transparency-mcp``
(MIT). Region encoding (``REGIONS``) is also from there.

Modes
-----
``mode="search_advertisers"`` (default)
    Find advertisers by keyword. Returns each advertiser with
    ``advertiser_id``, ``country``, ``ad_count``, plus any related
    domains.

``mode="domain"``
    Find an advertiser by exact domain. ``query="example.com"``.
    Returns the advertiser_id + name + ad_count.

``mode="advertiser_ads"``
    Given ``query="<advertiser_id>"`` (with the ``AR`` prefix), list
    that advertiser's ad creatives. Filterable by ``region``.

``mode="creative_detail"``
    Given ``query="<advertiser_id>:<creative_id>"`` (with the ``AR``
    and ``CR`` prefixes), fetch a single ad's full detail including
    decoded headline / description / destination URL (text ads),
    image_url (image ads) or video_url + youtube_video_id (video ads).

Returned fields
---------------
- search_advertisers / domain:
  ``advertiser_name``, ``advertiser_id``, ``country``, ``ad_count``,
  ``ad_count_max``, ``domain`` (when applicable), ``url``.
- advertiser_ads:
  + ``creative_id``, ``format`` (text/image/video/shopping/display),
  ``format_int``, ``first_seen_ms``, ``last_seen_ms``, ``days_running``,
  ``country``, ``text_summary``.
- creative_detail:
  + ``headline``, ``description``, ``destination_url``, ``image_url``,
  ``video_url``, ``youtube_video_id``, ``last_shown_iso``.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Optional

from .base import BaseEngine, SearchResult
from ._google_atc_options import region_num as _region_num, valid_codes as _valid_codes
from ..core import safe_goto

log = logging.getLogger(__name__)


_RPC_PATH = "/anji/_/rpc/"
_RPC_SUGGEST = "/anji/_/rpc/SearchService/SearchSuggestions"
_RPC_CREATIVES = "/anji/_/rpc/SearchService/SearchCreatives"
_RPC_LOOKUP = "/anji/_/rpc/LookupService/GetCreativeById"

_BASE_URL = "https://adstransparency.google.com"

# Headers used by the raw-HTTP transport. Mirrors a desktop Chrome 145
# request closely enough that ATC accepts them without an attached
# session beyond what ``GET /`` deposits in cookies. We deliberately
# avoid CloakBrowser's stealth headers because those plus a residential
# proxy egress trigger an ATC challenge that prevents navigation entirely.
_RAW_HEADERS = {
    "accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8,"
        "application/signed-exchange;v=b3;q=0.7"
    ),
    "accept-language": "en-US,en;q=0.9",
    "sec-ch-ua": (
        '"Chromium";v="145", "Not_A Brand";v="24", "Google Chrome";v="145"'
    ),
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "none",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36"
    ),
}

# Format integer codes — observed values from production responses.
_FORMAT_MAP = {
    1: "text",
    2: "image",
    3: "video",
    4: "shopping",
    5: "discovery",
    6: "html5",
    7: "rich_media",
    8: "display",
    9: "youtube_in_stream",
    10: "youtube_bumper",
    11: "youtube_in_feed",
    12: "youtube_outstream",
    13: "youtube_short",
    14: "maps",
}


# ---------------------------------------------------------------------------
# Text ad decoder (port of block-gatc/parser.py)
# ---------------------------------------------------------------------------


def _extract_ad_param(url: str) -> Optional[str]:
    """Pull the ``ad=`` query parameter from an iframe URL.

    Used to extract the base64-encoded protobuf payload that text ads
    carry inside their ``?ad=...`` parameter.
    """
    if not url or not isinstance(url, str):
        return None
    try:
        parsed = urllib.parse.urlparse(url)
        params = urllib.parse.parse_qs(parsed.query)
        values = params.get("ad")
        if values:
            return values[0]
    except Exception:
        pass
    match = re.search(r"[?&]ad=([A-Za-z0-9_+/=-]+)", url)
    return match.group(1) if match else None


def _looks_like_url(text: str) -> bool:
    return bool(
        text.startswith(("http://", "https://", "www."))
        or re.match(r"^[a-zA-Z0-9-]+\.[a-zA-Z]{2,}", text)
    )


def _is_readable(text: str) -> bool:
    if len(text) < 3:
        return False
    return sum(1 for c in text if c.isprintable()) / len(text) > 0.8


def _extract_strings(data: bytes) -> list[str]:
    """Walk a protobuf-style byte stream and return every readable
    length-delimited UTF-8 chunk."""
    strings: list[str] = []
    i = 0
    while i < len(data) - 1:
        byte = data[i]
        if (byte & 0x07) == 0x02:
            i += 1
            if i >= len(data):
                break
            length = data[i]
            i += 1
            if length > 0 and i + length <= len(data):
                chunk = data[i: i + length]
                try:
                    text = chunk.decode("utf-8")
                    if _is_readable(text) and len(text) >= 3:
                        strings.append(text)
                except UnicodeDecodeError:
                    pass
                i += length
        else:
            i += 1
    if len(strings) < 2:
        for match in re.finditer(rb"[\x20-\x7e]{5,}", data):
            text = match.group().decode("ascii", errors="ignore").strip()
            if text and text not in strings and _is_readable(text):
                strings.append(text)
    return strings


def decode_text_ad(iframe_url: str) -> Optional[dict[str, str]]:
    """Decode a text ad's iframe ``?ad=`` payload into structured fields.

    Returns ``{"headline", "description", "destination_url"}`` or ``None``
    if no decodable payload is present.
    """
    payload = _extract_ad_param(iframe_url)
    if not payload:
        return None
    try:
        raw = base64.b64decode(payload + "==")
    except Exception:
        return None
    strings = _extract_strings(raw)
    headline = description = destination_url = ""
    for s in strings:
        s = s.strip()
        if not s:
            continue
        if _looks_like_url(s):
            if not destination_url:
                destination_url = s
        elif not headline:
            headline = s
        elif not description:
            description = s
    return {
        "headline": headline,
        "description": description,
        "destination_url": destination_url,
    }


def _extract_youtube_video_id(url: str) -> Optional[str]:
    """Extract a YouTube video ID from common URL formats."""
    if not url:
        return None
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return None
    host = (parsed.hostname or "").lower()
    if "youtube.com" in host:
        qs = urllib.parse.parse_qs(parsed.query)
        v = qs.get("v", [None])[0]
        if v:
            return v
        # /shorts/<id>, /embed/<id>, /v/<id>, etc.
        parts = [p for p in parsed.path.strip("/").split("/") if p]
        for prefix in ("shorts", "embed", "v"):
            if len(parts) >= 2 and parts[0] == prefix:
                return parts[1]
    if "youtu.be" in host:
        parts = [p for p in parsed.path.strip("/").split("/") if p]
        if parts:
            return parts[0]
    return None


# ---------------------------------------------------------------------------
# Raw HTTP transport helpers (used when CloakBrowser + residential proxy is
# blocked by ATC's stealth checks)
# ---------------------------------------------------------------------------


def _build_raw_session(proxy_url: Optional[str] = None,
                       timeout: int = 30):
    """Construct a ``requests.Session`` for the raw RPC transport.

    The session is pre-loaded with realistic Chrome headers. When
    ``proxy_url`` is provided (e.g. ``http://USER:PASS@host:port``) the
    session routes both HTTP and HTTPS traffic through it. The session
    keeps cookies across calls so the RPC POSTs receive the same
    ``NID`` / ``CONSENT`` cookies that ATC plants on the homepage GET.

    The optional ``timeout`` is stashed as ``session.request_timeout``
    so callers can use it as the default ``timeout=`` value on each
    request without re-deriving it.
    """
    import requests  # late-imported so the module loads even when
                     # requests isn't installed (it usually is, since
                     # cloakbrowser ships with it transitively).

    session = requests.Session()
    session.headers.update(_RAW_HEADERS)
    if proxy_url:
        session.proxies.update({"http": proxy_url, "https": proxy_url})
    # Custom attribute — requests.Session accepts this without complaint.
    session.request_timeout = timeout  # type: ignore[attr-defined]
    return session


def _raw_warm(session, region: str = "anywhere") -> bool:
    """GET the ATC homepage so the session picks up baseline cookies.

    Returns ``True`` when the warm-up succeeded. Callers can ignore the
    return value — the RPCs degrade gracefully without cookies on most
    endpoints, and the warm-up failing usually means the proxy itself
    is misbehaving (which the RPC POST will surface anyway).
    """
    try:
        r = session.get(
            f"{_BASE_URL}/?region={region.lower()}",
            timeout=session.request_timeout,
        )
        return 200 <= r.status_code < 400
    except Exception as e:
        log.debug("[g_ads/raw] warm-up GET failed: %s", e)
        return False


def _raw_post_rpc(session, rpc_path: str,
                  req_body: dict) -> Optional[dict]:
    """POST a single RPC and return the JSON body, or ``None`` on
    transport failure / non-JSON response."""
    import requests
    url = f"{_BASE_URL}{rpc_path}"
    data = {"f.req": json.dumps(req_body)}
    try:
        r = session.post(
            url,
            data=data,
            params={"authuser": "0"},
            timeout=session.request_timeout,
        )
    except requests.exceptions.RequestException as e:
        log.warning("[g_ads/raw] POST %s failed: %s", rpc_path, e)
        return None
    if r.status_code != 200:
        log.warning("[g_ads/raw] POST %s returned %d", rpc_path, r.status_code)
        return None
    try:
        return r.json()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class GoogleAdTransparencyEngine(BaseEngine):
    """Google Ads Transparency Center adapter via RPC interception."""

    name = "google_ad_transparency"
    max_retries = 2

    _MODES = (
        "search_advertisers", "domain",
        "advertiser_ads", "creative_detail",
    )

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}
        # ``transport`` distinguishes the two execution paths:
        #   "browser" — navigate + intercept GraphQL via Playwright
        #               (default; works without a proxy from clean IPs).
        #   "raw"     — direct ``requests`` POST to the RPC endpoints
        #               (use via :meth:`raw` factory; required when a
        #               proxy is in play because cloakbrowser+proxy+
        #               Google's stealth combine to block navigation).
        self.transport = "browser"
        self._raw_session = None

    @classmethod
    def raw(cls, *, proxy_url: Optional[str] = None,
            timeout: int = 30) -> "GoogleAdTransparencyEngine":
        """Build a page-less engine that talks straight to ATC's RPC
        endpoints over plain ``requests``.

        Use this when your environment requires a proxy. The browser-
        based transport doesn't survive the cloakbrowser+residential-
        proxy+Google triangle (ATC drops the connection during
        stealth fingerprint checks); the raw transport sidesteps that
        entirely while still routing through whatever proxy you pass.

        Args:
            proxy_url: Full ``http(s)://[user:pass@]host:port`` URL.
                ``None`` → direct connection.
            timeout:   Per-request timeout in seconds.

        Returns:
            A configured engine instance whose :meth:`search` method
            uses HTTP RPC instead of the browser.
        """
        eng = cls.__new__(cls)
        eng.page = None  # type: ignore[assignment]
        eng.last_status = {}
        eng.transport = "raw"
        eng._raw_session = _build_raw_session(proxy_url, timeout)
        eng._raw_warm_done = False
        return eng

    def _ensure_raw_warm(self, region: str) -> None:
        if self.transport != "raw" or self._raw_session is None:
            return
        if getattr(self, "_raw_warm_done", False):
            return
        _raw_warm(self._raw_session, region)
        self._raw_warm_done = True

    # ------------------------------------------------------------------ public API

    def search(  # type: ignore[override]
        self,
        query: str,
        limit: int = 20,
        *,
        mode: str = "search_advertisers",
        region: str = "anywhere",
        advertiser_id: Optional[str] = None,
        creative_id: Optional[str] = None,
        page_size: int = 40,
    ) -> list[SearchResult]:
        m = (mode or "search_advertisers").lower()
        if m not in self._MODES:
            raise ValueError(
                f"unknown mode {m!r}; choose one of {list(self._MODES)}"
            )
        # Validate region (case-insensitive); ``anywhere`` is the wildcard.
        region_norm = (region or "anywhere").upper()
        if region_norm != "ANYWHERE" and region_norm not in _valid_codes():
            raise ValueError(
                f"region {region!r} not supported. Use 'anywhere' or an "
                f"ISO-3166 alpha-2 code from "
                f"agent_search/engines/_google_atc_options/regions.py"
            )

        self.last_status = {"mode": m, "region": region_norm, "query": query,
                            "transport": self.transport}

        # Raw transport needs a one-time homepage GET to bank cookies.
        self._ensure_raw_warm(region_norm)

        if m == "search_advertisers":
            return self._search_advertisers(query, limit, region_norm)
        if m == "domain":
            return self._domain_search(query, limit, region_norm)
        if m == "advertiser_ads":
            adv = advertiser_id or query
            return self._advertiser_ads(adv, limit, region_norm,
                                        page_size=page_size)
        if m == "creative_detail":
            adv, cid = self._parse_creative_query(query, advertiser_id, creative_id)
            return self._creative_detail(adv, cid, region_norm)
        return []

    # ------------------------------------------------------------------ search_advertisers

    def _search_advertisers(self, query: str, limit: int,
                            region: str) -> list[SearchResult]:
        if self.transport == "raw":
            body = _raw_post_rpc(
                self._raw_session, _RPC_SUGGEST,
                {"1": query, "2": 10, "3": 10},
            )
            if body is None:
                self.last_status["error"] = (
                    "raw POST SearchSuggestions returned no parseable JSON"
                )
                return []
            entries = body.get("1") or []
            results: list[SearchResult] = []
            for entry in entries[:limit]:
                r = self._suggestion_to_result(entry, region)
                if r:
                    results.append(r)
            self.last_status["found"] = len(results)
            return results

        # ── browser transport (default) ─────────────────────────────
        captured = self._capture_rpc(_RPC_SUGGEST, _BASE_URL +
                                     f"/?region={region.lower()}&hl=en",
                                     setup=lambda: self._fill_search_box(query),
                                     wait=12.0)
        if not captured:
            self.last_status["error"] = (
                "no SearchSuggestions response — Google ATC's stealth "
                "checks frequently reject CloakBrowser when egressing "
                "via residential proxies (the page never finishes "
                "navigation commit). Workarounds: (a) run without a "
                "proxy from a clean US/EU IP, (b) re-run from a "
                "datacenter IP that ATC trusts, (c) port the engine "
                "to raw HTTP RPC (block-town/google-ads-transparency-mcp "
                "shows the protocol)."
            )
            return []

        results: list[SearchResult] = []
        for entry in (captured.get("1") or [])[:limit]:
            r = self._suggestion_to_result(entry, region)
            if r:
                results.append(r)
        self.last_status["found"] = len(results)
        return results

    def _suggestion_to_result(self, entry: dict, region: str) -> Optional[SearchResult]:
        # SearchSuggestions returns two shapes:
        #   {"1": {"1": <name>, "2": <id>, "3": <country>,
        #          "4": {"2": {"1": <ad_count_min>, "2": <ad_count_max>}}}}
        #   {"2": {"1": <domain>}}                         — domain hit
        if "1" in entry and isinstance(entry["1"], dict):
            inner = entry["1"]
            name = str(inner.get("1") or "")
            adv_id = str(inner.get("2") or "")
            country = str(inner.get("3") or "")
            ad_count_min = ad_count_max = None
            cnt = (inner.get("4") or {}).get("2") or {}
            if isinstance(cnt, dict):
                ad_count_min = cnt.get("1")
                ad_count_max = cnt.get("2")
            if not adv_id:
                return None
            url = f"{_BASE_URL}/advertiser/{adv_id}?region={region.lower()}&hl=en"
            snippet_parts = [country] if country else []
            if ad_count_min is not None:
                if ad_count_max and ad_count_max != ad_count_min:
                    snippet_parts.append(f"ads={ad_count_min}-{ad_count_max}")
                else:
                    snippet_parts.append(f"ads={ad_count_min}")
            r = SearchResult(title=name, url=url,
                             snippet=" · ".join(snippet_parts))
            r.__dict__.update({
                "advertiser_name": name,
                "advertiser_id": adv_id,
                "country": country,
                "ad_count": ad_count_min,
                "ad_count_max": ad_count_max,
                "result_type": "advertiser",
                "region": region,
            })
            return r

        if "2" in entry and isinstance(entry["2"], dict):
            domain = str(entry["2"].get("1") or "")
            if not domain:
                return None
            r = SearchResult(
                title=domain,
                url=f"{_BASE_URL}/?domain={domain}&region={region.lower()}",
                snippet="(domain — use mode=domain to resolve to advertiser)",
            )
            r.__dict__.update({
                "domain": domain,
                "result_type": "domain",
                "region": region,
            })
            return r
        return None

    # ------------------------------------------------------------------ domain mode

    def _domain_search(self, domain: str, limit: int,
                       region: str) -> list[SearchResult]:
        if self.transport == "raw":
            # block-town's documented shape: SearchCreatives by-domain
            # returns the top advertisers running ads from the domain.
            body = _raw_post_rpc(
                self._raw_session, _RPC_CREATIVES,
                {
                    "1": domain,
                    "2": 1,
                    "3": {"12": {"1": domain}},
                    "7": {"1": 1},
                },
            )
            if body and (ads := body.get("1") or []):
                ad = ads[0]
                adv_id = str(ad.get("1") or "")
                name = str(ad.get("12") or "")
                if adv_id:
                    url = (
                        f"{_BASE_URL}/advertiser/{adv_id}"
                        f"?region={region.lower()}"
                    )
                    r = SearchResult(
                        title=name or domain, url=url,
                        snippet=f"domain={domain}",
                    )
                    r.__dict__.update({
                        "advertiser_id": adv_id,
                        "advertiser_name": name,
                        "domain": domain,
                        "region": region,
                        "result_type": "advertiser_by_domain",
                    })
                    return [r]
            # Fallback: treat as keyword.
            return self._search_advertisers(domain, limit, region)

        # ── browser transport ──────────────────────────────────────
        # Strategy: navigate to homepage, type the domain in the search box.
        # The SPA fires SearchCreatives with the by-domain shape ─ which we
        # capture. Falls back to SearchSuggestions if SearchCreatives is empty.
        captured = self._capture_rpc(
            _RPC_CREATIVES,
            _BASE_URL + f"/?region={region.lower()}&hl=en",
            setup=lambda: self._fill_search_box(domain),
            wait=12.0,
        )

        if captured:
            ads = captured.get("1") or []
            if ads:
                ad = ads[0]
                adv_id = str(ad.get("1") or "")
                name = str(ad.get("12") or "")
                if adv_id:
                    url = f"{_BASE_URL}/advertiser/{adv_id}?region={region.lower()}"
                    r = SearchResult(
                        title=name or domain, url=url,
                        snippet=f"domain={domain}",
                    )
                    r.__dict__.update({
                        "advertiser_id": adv_id,
                        "advertiser_name": name,
                        "domain": domain,
                        "region": region,
                        "result_type": "advertiser_by_domain",
                    })
                    return [r]

        # Fallback: try SearchSuggestions and pick first matching domain.
        return self._search_advertisers(domain, limit, region)

    # ------------------------------------------------------------------ advertiser_ads

    def _advertiser_ads(self, advertiser_id: str, limit: int,
                        region: str, *, page_size: int) -> list[SearchResult]:
        if not advertiser_id.startswith("AR"):
            log.warning(
                "[g_ads] advertiser_id should start with 'AR' (got %r); "
                "use search_advertisers/domain to find the right ID first",
                advertiser_id[:30],
            )

        if self.transport == "raw":
            req: dict = {
                "2": min(max(limit, page_size), 100),
                "3": {
                    "12": {"1": "", "2": True},
                    "13": {"1": [advertiser_id]},
                },
                "7": {"1": 1},
            }
            r_num = _region_num(region)
            if r_num is not None:
                req["3"]["8"] = [r_num]

            body = _raw_post_rpc(self._raw_session, _RPC_CREATIVES, req)
            if body is None:
                self.last_status["error"] = (
                    "raw POST SearchCreatives returned no parseable JSON"
                )
                return []
            rows = body.get("1") or []
            self.last_status["next_page_id"] = body.get("2")
            results: list[SearchResult] = []
            for ad in rows[:limit]:
                results.append(self._creative_summary(ad, advertiser_id, region))
            return results

        # ── browser transport ──────────────────────────────────────
        url = (
            f"{_BASE_URL}/advertiser/{advertiser_id}"
            f"?region={region.lower()}&hl=en"
        )
        captured = self._capture_rpc(_RPC_CREATIVES, url, wait=18.0)
        if not captured:
            self.last_status["error"] = (
                "SearchCreatives returned empty — Google ATC frequently "
                "rejects this RPC without extra session args; try opening "
                f"{url} in a browser tab"
            )
            return []

        rows = captured.get("1") or []
        next_page_id = captured.get("2")
        self.last_status["next_page_id"] = next_page_id

        results: list[SearchResult] = []
        for ad in rows[:limit]:
            results.append(self._creative_summary(ad, advertiser_id, region))
        return results

    def _creative_summary(self, ad: dict, advertiser_id: str,
                          region: str) -> SearchResult:
        """Convert one ``SearchCreatives.results[i]`` dict into a
        :class:`SearchResult`.

        Two schemas observed in the wild — the raw POST response (May
        2026) puts ``format_int`` at top-level ``"4"`` and timestamps as
        epoch *seconds* under ``"6"["1"]`` / ``"7"["1"]``. The browser-
        intercepted response (May 2026) used ``"3"["1"]`` for format and
        ``"5"["1"]`` / ``"5"["2"]`` for ms timestamps. We probe both.
        """
        cid = str(ad.get("2") or ad.get("1") or "")

        # Format: prefer top-level "4" (raw), else nested "3"."1" (browser).
        fmt_int = None
        fmt_subtype = None
        top4 = ad.get("4")
        if isinstance(top4, int):
            fmt_int = top4
        else:
            n3 = ad.get("3")
            if isinstance(n3, dict) and isinstance(n3.get("1"), int):
                fmt_int = n3["1"]
                fmt_subtype = n3.get("2") if isinstance(n3.get("2"), int) else None
        fmt = _FORMAT_MAP.get(fmt_int, str(fmt_int) if fmt_int else "")

        # Timestamps: raw schema has them in "6"/"7" as epoch seconds,
        # browser schema in "5" as epoch ms.
        first_ms = first_seconds = None
        last_ms = last_seconds = None
        n6 = ad.get("6")
        n7 = ad.get("7")
        if isinstance(n6, dict):
            first_seconds = self._coerce_int(n6.get("1"))
        if isinstance(n7, dict):
            last_seconds = self._coerce_int(n7.get("1"))
        n5 = ad.get("5")
        if first_seconds is None and isinstance(n5, dict):
            first_ms = self._coerce_int(n5.get("1"))
        if last_seconds is None and isinstance(n5, dict):
            last_ms = self._coerce_int(n5.get("2"))

        if first_seconds is not None and first_ms is None:
            first_ms = first_seconds * 1000
        if last_seconds is not None and last_ms is None:
            last_ms = last_seconds * 1000

        days = None
        if first_ms is not None and last_ms is not None:
            days = max(0, int((last_ms - first_ms) / (1000 * 86400)))

        # ``"12"`` doubles as text summary in the browser schema and as
        # advertiser display name in the raw schema. We expose it under
        # both keys so consumers can pick whichever they expect.
        adv_or_text = str(ad.get("12") or "")
        country = str(ad.get("9") or region)

        # Best-effort link / image extraction.
        link = self._extract_search_row_link(ad)

        ad_url = (
            f"{_BASE_URL}/advertiser/{advertiser_id}/creative/{cid}"
            f"?region={region.lower()}&hl=en" if cid else
            f"{_BASE_URL}/advertiser/{advertiser_id}?region={region.lower()}"
        )
        snippet_parts = []
        if fmt:
            snippet_parts.append(fmt)
        if days is not None:
            snippet_parts.append(f"{days}d running")
        if adv_or_text:
            snippet_parts.append(adv_or_text[:120])

        r = SearchResult(
            title=adv_or_text[:140] or cid,
            url=ad_url,
            snippet=" · ".join(snippet_parts),
        )
        r.__dict__.update({
            "creative_id": cid,
            "advertiser_id": advertiser_id,
            "format": fmt,
            "format_int": fmt_int,
            "format_subtype": fmt_subtype,
            "first_seen_ms": first_ms,
            "last_seen_ms": last_ms,
            "first_seen_iso": self._ms_to_iso(first_ms),
            "last_seen_iso": self._ms_to_iso(last_ms),
            "days_running": days,
            "country": country,
            "region": region,
            "text_summary": adv_or_text,
            "advertiser_display_name": adv_or_text,
            "preview_link": link,
            "image_url": link if link and self._looks_like_image(link) else "",
            "raw": ad,
        })
        return r

    @staticmethod
    def _coerce_int(value: Any) -> Optional[int]:
        if value is None or value == "":
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return None

    @staticmethod
    def _looks_like_image(url: str) -> bool:
        if not url:
            return False
        return any(x in url for x in (
            "simgad", ".png", ".jpg", ".jpeg", ".gif", ".webp",
            "googlesyndication.com",
        ))

    @staticmethod
    def _extract_search_row_link(ad: dict) -> str:
        """Walk a SearchCreatives row's ``"3"`` block to find an image
        ``src=`` or a displayads preview URL.

        Two shapes observed:
            - image:    ad["3"]["3"]["2"] = '<img src="...">'
            - display:  ad["3"]["1"]["4"] = 'https://displayads-...'
        """
        n3 = ad.get("3")
        if not isinstance(n3, dict):
            return ""
        # Image: ad["3"]["3"]["2"] ~ '<img src="..."  ...>'
        n3_3 = n3.get("3")
        if isinstance(n3_3, dict):
            raw = n3_3.get("2")
            if isinstance(raw, str):
                if 'src="' in raw:
                    return raw.split('src="', 1)[1].split('"', 1)[0]
                if "'" in raw:
                    return raw.split("'", 1)[1].split("'", 1)[0]
                return raw
        # Display / video preview: ad["3"]["1"]["4"]
        n3_1 = n3.get("1")
        if isinstance(n3_1, dict):
            v = n3_1.get("4")
            if isinstance(v, str):
                return v
        return ""

    # ------------------------------------------------------------------ creative_detail

    def _creative_detail(self, advertiser_id: str, creative_id: str,
                         region: str) -> list[SearchResult]:
        if not (advertiser_id and creative_id):
            self.last_status["error"] = (
                "creative_detail requires both advertiser_id (AR...) and "
                "creative_id (CR...). Pass query='AR...:CR...' or "
                "advertiser_id=... creative_id=..."
            )
            return []

        if self.transport == "raw":
            body = _raw_post_rpc(
                self._raw_session, _RPC_LOOKUP,
                {"1": advertiser_id, "2": creative_id, "5": {"1": 1}},
            )
            if body is None:
                self.last_status["error"] = (
                    "raw POST GetCreativeById returned no parseable JSON"
                )
                return []
            ad = body.get("1") or {}
            if not ad:
                return []
            return [self._creative_detail_to_result(
                ad, advertiser_id, creative_id, region,
                page_url=(f"{_BASE_URL}/advertiser/{advertiser_id}/"
                          f"creative/{creative_id}?region={region.lower()}"),
            )]

        # ── browser transport ──────────────────────────────────────
        url = (
            f"{_BASE_URL}/advertiser/{advertiser_id}/creative/{creative_id}"
            f"?region={region.lower()}&hl=en"
        )
        captured = self._capture_rpc(_RPC_LOOKUP, url, wait=18.0)
        if not captured:
            self.last_status["error"] = "GetCreativeById returned empty"
            return []

        ad = captured.get("1") or {}
        if not ad:
            return []
        return [self._creative_detail_to_result(
            ad, advertiser_id, creative_id, region, page_url=url,
        )]

    def _creative_detail_to_result(self, ad: dict, advertiser_id: str,
                                   creative_id: str, region: str,
                                   *, page_url: str) -> SearchResult:
        format_int = ad.get("8")
        fmt = _FORMAT_MAP.get(format_int, str(format_int) if format_int else "")
        last_shown_ms = (ad.get("4") or {}).get("1")

        link = self._extract_ad_link(ad)
        is_image = any(x in link for x in (
            "simgad", ".png", ".jpg", ".gif", ".webp", "googlesyndication.com"
        ))

        content: dict[str, Any] = {
            "headline": "",
            "description": "",
            "destination_url": "",
            "image_url": "",
            "video_url": "",
            "youtube_video_id": "",
            "preview_url": link,
        }

        if is_image:
            fmt = fmt or "image"
            content["image_url"] = link
        elif format_int == 1:
            decoded = decode_text_ad(link)
            if decoded:
                content.update(decoded)
        elif format_int == 3:
            content["video_url"] = link
            ytid = _extract_youtube_video_id(link)
            if ytid:
                content["youtube_video_id"] = ytid

        title = content["headline"] or content["destination_url"] or creative_id
        snippet_parts = [fmt] if fmt else []
        if content["headline"]:
            snippet_parts.append(content["headline"])
        if content["description"]:
            snippet_parts.append(content["description"])

        r = SearchResult(title=title[:200], url=page_url,
                         snippet=" · ".join(snippet_parts))
        r.__dict__.update({
            "advertiser_id": advertiser_id,
            "creative_id": creative_id,
            "format": fmt,
            "format_int": format_int,
            "last_seen_ms": last_shown_ms,
            "last_seen_iso": self._ms_to_iso(last_shown_ms),
            "headline": content["headline"],
            "description": content["description"],
            "destination_url": content["destination_url"],
            "image_url": content["image_url"],
            "video_url": content["video_url"],
            "youtube_video_id": content["youtube_video_id"],
            "preview_url": content["preview_url"],
            "region": region,
            "raw": ad,
        })
        return r

    # ------------------------------------------------------------------ helpers

    def _capture_rpc(self, rpc_marker: str, page_url: str,
                     *, setup=None, wait: float = 25.0,
                     nav_timeout: int = 30000) -> Optional[dict]:
        """Navigate ``page_url``, optionally run ``setup``, and return the
        first JSON body of an RPC matching ``rpc_marker``.

        Pure helper used by all 4 modes for response interception.

        We use ``wait_until="commit"`` (the most permissive Playwright
        wait state — fires as soon as the navigation request is
        committed to the renderer) because Google's ``adstransparency``
        SPA is heavy and ``domcontentloaded`` often takes >30s through
        residential proxies even though the target RPC fires within
        seconds. After the commit we keep the listener active for
        ``wait`` seconds, which is when the actual marker arrives.

        Important: this method has a strict total budget — never blocks
        more than ``nav_timeout/1000 + wait`` seconds.
        """
        captured: dict[str, Any] = {"body": None}

        def _on_response(resp):
            if rpc_marker in resp.url and resp.status == 200 and captured["body"] is None:
                try:
                    captured["body"] = resp.json()
                except Exception:
                    try:
                        text = resp.text()
                        captured["body"] = json.loads(text) if text else None
                    except Exception:
                        pass

        self.page.on("response", _on_response)
        nav_ok = False
        try:
            log.info("[g_ads] navigating %s (capture=%s)", page_url, rpc_marker)
            try:
                self.page.goto(page_url, timeout=nav_timeout,
                               wait_until="commit")
                nav_ok = True
            except Exception as e:
                log.warning("[g_ads] goto %s; relying on partial navigation",
                            type(e).__name__)

            if setup and nav_ok:
                # Give the SPA a moment after commit before we start
                # poking inputs.
                self.page.wait_for_timeout(3000)
                try:
                    setup()
                except Exception as e:
                    log.warning("[g_ads] setup() failed: %s", e)

            deadline = time.time() + wait
            while time.time() < deadline and captured["body"] is None:
                self.page.wait_for_timeout(400)
        finally:
            try:
                self.page.remove_listener("response", _on_response)
            except Exception:
                pass

        return captured["body"]

    def _fill_search_box(self, query: str) -> None:
        """Find and fill the homepage search box. Several selectors are
        candidates because Google rotates DOM. All operations are
        time-bounded so this method never blocks more than a few seconds."""
        self.page.wait_for_timeout(2000)
        box = None
        for sel in (
            'input[type="search"]',
            'input[type="text"]',
            'input[aria-label*="Search" i]',
            'input.gws-input',
            'input[placeholder*="advertiser" i]',
            'input[placeholder*="search" i]',
            'input',
        ):
            try:
                box = self.page.query_selector(sel)
                if box and box.is_visible():
                    break
            except Exception:
                continue
        if box is None:
            raise RuntimeError("search input not found")
        # Each interaction has its own short timeout; if the SPA isn't
        # yet hydrated we give up rather than blocking on the default
        # 30s Playwright timeout.
        try:
            box.click(timeout=5000)
        except Exception as e:
            log.warning("[g_ads] click failed: %s", e)
            return
        try:
            box.fill(query, timeout=5000)
        except Exception as e:
            log.warning("[g_ads] fill failed: %s", e)
            return
        self.page.wait_for_timeout(800)

    @staticmethod
    def _ms_to_iso(ms: Any) -> str:
        try:
            ts = int(ms) / 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            return ""

    @staticmethod
    def _extract_ad_link(ad: dict) -> str:
        """Pull the preview/content link out of a GetCreativeById response."""
        try:
            creatives = ad.get("5") or []
            if not creatives:
                return ""
            creative = creatives[0]
            if isinstance(creative, dict) and "3" in creative \
                    and isinstance(creative["3"], dict) and "2" in creative["3"]:
                raw = str(creative["3"]["2"])
                if 'src="' in raw:
                    return raw.split('src="', 1)[1].split('"', 1)[0]
                if "'" in raw:
                    return raw.split("'", 1)[1].split("'", 1)[0]
                return raw
            for path in (("2", "4"), ("1", "4"), ("4",)):
                node = creative
                for k in path:
                    node = (node or {}).get(k) if isinstance(node, dict) else None
                if isinstance(node, str):
                    return node
        except Exception:
            pass
        return ""

    @staticmethod
    def _parse_creative_query(query: str, advertiser_id: Optional[str],
                              creative_id: Optional[str]) -> tuple[str, str]:
        """Accept either ``query="AR...:CR..."`` or explicit kwargs."""
        if advertiser_id and creative_id:
            return advertiser_id, creative_id
        q = (query or "").strip()
        if ":" in q:
            adv, cid = q.split(":", 1)
            return adv.strip(), cid.strip()
        # Single value — treat as advertiser_id (caller must pass creative_id kwarg)
        return q, creative_id or ""

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        return self.search(query, limit)
