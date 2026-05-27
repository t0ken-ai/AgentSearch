"""Meta Ad Library engine — competitive intelligence on Facebook / Instagram ads.

Background
----------
Meta's Ad Library at ``facebook.com/ads/library`` shows every active and
recently-inactive ad running on Facebook, Instagram, Messenger and Audience
Network. No login needed for most countries / most ad types (political /
EU has extra detail). The frontend is a React/Relay SPA that posts to
``/api/graphql/`` with rotating ``doc_id`` + per-session tokens
(``lsd``, ``fb_dtsg``, ``__dyn``, ``__csr``, ``x-asbd-id``, ...).

Strategy
--------
The most-starred GitHub project on this (`promisingcoder/MetaAdsCollector`)
reverse-engineers the GraphQL request manually with curl_cffi. That works
but **requires constant maintenance** because Meta rotates token names
quarterly (we observed ``__rev`` → ``server_revision``,
``__hs`` → ``haste_session`` in 2026 already).

Our approach is different and more sustainable: **let the page itself
fire the GraphQL request and intercept the response**. CloakBrowser is
indistinguishable from a real Chromium tab, so the frontend obtains a
valid session and the server returns real data. We just listen via
``page.on("response")`` and walk
``data.ad_library_main.search_results_connection.edges[]``.

The cost of this approach: a real page navigation per query (~6-10s vs
~1s for a raw HTTP). The benefit: when Meta rotates tokens / doc_ids /
GraphQL field names, our code keeps working with **zero maintenance**.

Modes
-----
``mode="keyword"`` (default)
    Search ad creative copy + advertiser names by keyword. ``query`` is
    the search string.

``mode="advertiser"``
    List a specific page's full ad library. ``query`` is the page name
    (typeahead-resolved) **or** ``"page_id:<numeric>"`` for an exact ID.

``mode="page_url"``
    Same as ``advertiser`` but accept any Facebook URL and auto-extract
    the page ID. Supports Ad Library URLs, profile URLs, numeric page
    URLs. Vanity URLs (``facebook.com/CocaCola``) are not resolvable
    without an extra typeahead call — fall back to ``mode="advertiser"``
    for those.

Filters (URL-level, sent to Meta)
---------------------------------
- ``country``         ISO-3166 code or ``ALL`` (default ``US``).
- ``ad_type``         ``all / political / housing / employment / credit``.
- ``status``          ``active / inactive / all`` (preferred over the legacy
                      ``active_only`` bool, which is kept for back-compat).
- ``media_type``      ``ALL / IMAGE / VIDEO / MEME / NONE``.
- ``search_type``     ``unordered`` (default) or ``exact`` for phrase match.
- ``sort_by``         ``relevancy`` (default, server picks) or ``impressions``.
- ``page_ids``        list of numeric page IDs (alternative to query mode).
- ``publisher_platforms`` list, e.g. ``["facebook", "instagram"]``.

Filters (client-side, applied after collection)
-----------------------------------------------
- ``min_impressions / max_impressions``  Range filter on impression bounds.
- ``min_spend / max_spend``              Range filter on spend bounds.
- ``start_date / end_date``              Filter by ``delivery_start_time``
                                         (accepts epoch int or
                                         ``datetime`` / ``YYYY-MM-DD`` str).
- ``languages``                          List of language codes.
- ``has_video / has_image``              Bool filters.

Returned fields per ad
----------------------
Core: ``ad_archive_id``, ``collation_id``, ``page_name``, ``page_id``,
``page_profile_url``, ``page_profile_picture_url``, ``page_like_count``,
``page_verified``, ``page_categories``, ``is_active``, ``ad_status``,
``start_date``, ``end_date``, ``days_running``, ``country``.

Creative: ``cta_text``, ``cta_type``, ``link_url``, ``body_text``,
``title``, ``caption``, ``link_description``, ``creatives[]`` (all
variations for carousel ads), ``image_urls[]``, ``video_urls[]``,
``video_url`` (first video).

Performance (political / EU only): ``spend_lower``, ``spend_upper``,
``currency``, ``impressions_lower``, ``impressions_upper``,
``reach_lower``, ``reach_upper``, ``estimated_audience_size_lower``,
``estimated_audience_size_upper``, ``age_gender_distribution[]``,
``region_distribution[]``.

Compliance (political / EU only): ``funding_entity``, ``disclaimer``,
``bylines[]``, ``beneficiary_payers[]``.

Extras: ``categories[]``, ``publisher_platforms[]``, ``languages[]``,
``rsoc_keywords[]`` (only when ``extract_rsoc=True``).
"""

from __future__ import annotations

import logging
import re
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Iterable, Optional, Sequence

from .base import BaseEngine, SearchResult
from ..core import safe_goto

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants (mirroring MetaAdsCollector/constants.py)
# ---------------------------------------------------------------------------

_GRAPHQL_PATH = "/api/graphql/"

_FRIENDLY_NAMES = {
    "AdLibrarySearchPaginationQuery",
    "AdLibrarySearchResultsQuery",
    "AdLibraryViewAllSearchResultsQuery",
    "AdLibraryAggregatorAdsByAdvertiserQuery",
}

# Typeahead is the GraphQL friendly-name fired by the search-input
# autocomplete dropdown. We intercept this separately because its
# response shape (``page_results[]``) is different from the normal
# search-results shape (``search_results_connection.edges[]``).
_TYPEAHEAD_FRIENDLY_NAMES = {
    "useAdLibraryTypeaheadSuggestionDataSourceQuery",
    "AdLibrarySearchTypeaheadQuery",
    "AdLibraryTypeaheadSearchQuery",
}

_AD_TYPE_ALIASES = {
    "all": "all",
    "any": "all",
    "political": "political_and_issue_ads",
    "political_and_issue_ads": "political_and_issue_ads",
    "issue": "political_and_issue_ads",
    "housing": "housing_ads",
    "housing_ads": "housing_ads",
    "employment": "employment_ads",
    "employment_ads": "employment_ads",
    "credit": "credit_ads",
    "credit_ads": "credit_ads",
}

_STATUS_ALIASES = {
    "active": "active",
    "inactive": "inactive",
    "all": "all",
}

_SEARCH_TYPE_ALIASES = {
    "keyword": "keyword_unordered",
    "unordered": "keyword_unordered",
    "keyword_unordered": "keyword_unordered",
    "exact": "keyword_exact_phrase",
    "phrase": "keyword_exact_phrase",
    "keyword_exact_phrase": "keyword_exact_phrase",
    "page": "page",
}

_SORT_ALIASES = {
    None: None,
    "relevancy": None,                              # server default
    "impressions": "impressions_monthly_grouped",
    "total_impressions": "impressions_monthly_grouped",
    "sort_by_total_impressions": "impressions_monthly_grouped",
}

_FACEBOOK_HOSTS = frozenset({
    "facebook.com", "www.facebook.com", "m.facebook.com",
    "web.facebook.com", "mobile.facebook.com",
    "l.facebook.com", "business.facebook.com",
})


# ---------------------------------------------------------------------------
# URL helpers (ported from MetaAdsCollector/url_parser.py)
# ---------------------------------------------------------------------------

def _extract_page_id_from_url(url: str) -> Optional[str]:
    """Extract a numeric Facebook page_id from any Facebook URL.

    Supports Ad Library URLs (``view_all_page_id``), profile URLs (``id``),
    and direct numeric paths. Returns ``None`` for vanity URLs which need
    a typeahead lookup to resolve.
    """
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    if not url:
        return None
    if url.isdigit():
        return url
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return None
    host = (parsed.hostname or "").lower()
    if host not in _FACEBOOK_HOSTS:
        return None

    qs = urllib.parse.parse_qs(parsed.query)
    for key in ("view_all_page_id", "id"):
        v = qs.get(key, [None])[0]
        if v and v.isdigit():
            return v

    parts = [p for p in parsed.path.strip("/").split("/") if p]
    for part in reversed(parts):
        if part.isdigit() and len(part) >= 5:
            return part
    return None


def _to_epoch(dt: Any) -> Optional[int]:
    """Coerce a datetime / date string / int into a unix epoch second.

    Accepts:
    - int / float — assumed to already be epoch seconds.
    - datetime — converted via ``.timestamp()``.
    - str — ``YYYY-MM-DD`` or ISO 8601, parsed with ``fromisoformat``.
    Returns ``None`` on unparseable input.
    """
    if dt is None:
        return None
    if isinstance(dt, (int, float)):
        return int(dt)
    if isinstance(dt, datetime):
        return int(dt.timestamp())
    if isinstance(dt, str):
        s = dt.strip()
        if s.isdigit():
            return int(s)
        try:
            return int(datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp())
        except ValueError:
            try:
                return int(datetime.strptime(s, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc).timestamp())
            except ValueError:
                return None
    return None


# ---------------------------------------------------------------------------
# Lightweight RSOC extractor (subset of Helvitiss-fb/rsoc.py).
# Only URL query-string extraction; no HTML fetch / no JWT decode.
# ---------------------------------------------------------------------------

_RSOC_KEYS = frozenset({
    "q", "qs", "search", "search_term", "search_terms",
    "keyword", "keywords", "kw", "kws", "kw_list", "keyword_list",
    "term", "terms", "term_list", "query_terms",
    "utm_term", "utm_terms",
    "rsoc", "forcekey",
})

_RSOC_SPLIT = re.compile(r"[,|;]|\s*\|\|\s*")


def _extract_rsoc_from_url(url: str) -> list[str]:
    """Extract intent keywords from a landing-page URL's query string.

    Handles ``forceKey*`` (highest signal), ``q``, ``utm_term``,
    ``terms[]`` style params. Returns a deduplicated list.
    """
    if not url or not isinstance(url, str):
        return []
    try:
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    except Exception:
        return []

    out: list[str] = []
    seen: set[str] = set()

    def _add(kw: str) -> None:
        kw = kw.strip().strip('"\'')
        if not kw or len(kw) > 120:
            return
        low = kw.lower()
        if low in seen:
            return
        seen.add(low)
        out.append(kw)

    # forceKey* gets priority — explicit intent signal
    for k, vals in qs.items():
        kl = k.lower()
        if kl.startswith("forcekey"):
            for v in vals:
                for part in _RSOC_SPLIT.split(urllib.parse.unquote(v)):
                    _add(part)

    if out:
        return out

    for k, vals in qs.items():
        kl = k.lower()
        if kl in _RSOC_KEYS or (
            kl[:-1] in _RSOC_KEYS and kl[-1:].isdigit()
        ):
            for v in vals:
                for part in _RSOC_SPLIT.split(urllib.parse.unquote(v)):
                    _add(part)
    return out


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class MetaAdLibraryEngine(BaseEngine):
    """Facebook / Instagram Ad Library adapter via response interception."""

    name = "meta_ad_library"
    max_retries = 2

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}

    # ------------------------------------------------------------------ public API

    def search(  # type: ignore[override]
        self,
        query: str = "",
        limit: int = 20,
        *,
        mode: str = "keyword",
        country: str = "US",
        # legacy bool, kept for back-compat
        active_only: Optional[bool] = None,
        # preferred 3-state filter
        status: Optional[str] = None,
        ad_type: str = "all",
        media_type: str = "ALL",
        search_type: Optional[str] = None,
        exact: bool = False,
        sort_by: Optional[str] = None,
        page_ids: Optional[Sequence[str]] = None,
        publisher_platforms: Optional[Sequence[str]] = None,
        # client-side filters (post-collection)
        min_impressions: Optional[int] = None,
        max_impressions: Optional[int] = None,
        min_spend: Optional[int] = None,
        max_spend: Optional[int] = None,
        start_date: Any = None,
        end_date: Any = None,
        languages: Optional[Sequence[str]] = None,
        has_video: Optional[bool] = None,
        has_image: Optional[bool] = None,
        # RSOC keyword extraction from landing pages
        extract_rsoc: bool = False,
    ) -> list[SearchResult]:
        """Search the Meta Ad Library.

        See module docstring for full parameter reference.
        """
        # ── Normalize parameters ───────────────────────────────────────
        m = (mode or "keyword").lower()
        if m not in ("keyword", "advertiser", "page_url"):
            raise ValueError(f"unknown mode {m!r}")

        ad_type_norm = _AD_TYPE_ALIASES.get((ad_type or "all").lower())
        if not ad_type_norm:
            raise ValueError(
                f"unknown ad_type {ad_type!r}. Valid: "
                f"{sorted(set(_AD_TYPE_ALIASES.values()))}"
            )

        if status is not None:
            status_norm = _STATUS_ALIASES.get(status.lower())
            if not status_norm:
                raise ValueError(
                    f"unknown status {status!r}. Valid: active / inactive / all"
                )
        elif active_only is False:
            status_norm = "all"
        else:
            status_norm = "active"

        if search_type is not None:
            st_norm = _SEARCH_TYPE_ALIASES.get(search_type.lower())
            if not st_norm:
                raise ValueError(f"unknown search_type {search_type!r}")
        elif exact:
            st_norm = "keyword_exact_phrase"
        elif m == "advertiser" or m == "page_url" or page_ids:
            st_norm = "page"
        else:
            st_norm = "keyword_unordered"

        sort_norm = _SORT_ALIASES.get(sort_by) if sort_by in _SORT_ALIASES \
            else _SORT_ALIASES.get((sort_by or "").lower())

        # ── Resolve page_ids if mode requires it ───────────────────────
        resolved_page_ids: list[str] = list(page_ids) if page_ids else []
        if m == "page_url":
            pid = _extract_page_id_from_url(query)
            if not pid:
                self.last_status = {
                    "error": f"could not extract page_id from URL {query!r}"
                }
                log.warning(self.last_status["error"])
                return []
            resolved_page_ids = [pid]
            query = ""  # PAGE search doesn't need ``q``
        elif m == "advertiser" and query.startswith("page_id:"):
            resolved_page_ids = [query[len("page_id:"):].strip()]
            query = ""

        self.last_status = {
            "mode": m, "country": country, "status": status_norm,
            "ad_type": ad_type_norm, "search_type": st_norm,
            "sort_by": sort_norm, "media_type": media_type,
            "page_ids": resolved_page_ids, "query": query,
        }

        # ── Build URL ─────────────────────────────────────────────────
        url = self._build_url(
            query=query,
            mode=m,
            country=country,
            status=status_norm,
            ad_type=ad_type_norm,
            media_type=media_type,
            search_type=st_norm,
            sort_by=sort_norm,
            page_ids=resolved_page_ids,
            publisher_platforms=publisher_platforms,
            start_date=start_date,
        )

        # ── Intercept GraphQL responses ───────────────────────────────
        captured: dict[str, Any] = {"bodies": [], "errors": [], "raw_count": 0}

        def _on_response(resp):
            if _GRAPHQL_PATH not in resp.url:
                return
            try:
                if resp.request.method != "POST" or resp.status != 200:
                    return
                pd = resp.request.post_data or ""
                params = dict(urllib.parse.parse_qsl(pd))
                fn = params.get("fb_api_req_friendly_name", "")
                if fn not in _FRIENDLY_NAMES:
                    return
                body = resp.json()
                captured["raw_count"] += 1
                if "errors" in body:
                    captured["errors"].append(body["errors"])
                if body.get("data"):
                    captured["bodies"].append(body)
            except Exception as e:
                log.debug("[meta_ads] response parse error: %s", e)

        self.page.on("response", _on_response)
        try:
            log.info("[meta_ads] navigating %s", url)
            if not safe_goto(self.page, url, timeout=45000, retries=1):
                self.last_status["error"] = "navigation failed"
                return []

            # Wait + scroll to trigger pagination GraphQL calls.
            deadline = time.time() + 35.0
            scroll_count = 0
            while time.time() < deadline:
                self.page.wait_for_timeout(700)
                if scroll_count < 12:
                    try:
                        self.page.mouse.wheel(0, 1500)
                    except Exception:
                        pass
                    scroll_count += 1
                if self._count_ads(captured["bodies"]) >= limit:
                    break
        finally:
            try:
                self.page.remove_listener("response", _on_response)
            except Exception:
                pass

        ads = self._collect_ads(captured["bodies"])

        # ── Client-side filtering ─────────────────────────────────────
        ads = self._apply_filters(
            ads,
            min_impressions=min_impressions,
            max_impressions=max_impressions,
            min_spend=min_spend,
            max_spend=max_spend,
            start_date=_to_epoch(start_date),
            end_date=_to_epoch(end_date),
            languages=[l.lower() for l in (languages or [])] or None,
            has_video=has_video,
            has_image=has_image,
        )

        self.last_status.update({
            "graphql_calls_total": captured["raw_count"],
            "graphql_calls_with_data": len(captured["bodies"]),
            "graphql_errors": len(captured["errors"]),
            "ads_found": len(ads),
        })
        if not ads and captured["errors"]:
            log.warning(
                "[meta_ads] no ads, %d error responses — likely IP-blocked. "
                "Try `--proxy pool:residential` or run via a residential proxy.",
                len(captured["errors"]),
            )

        return [
            self._ad_to_result(a, country, extract_rsoc=extract_rsoc)
            for a in ads[:limit]
        ]

    # ------------------------------------------------------------------ typeahead

    def lookup_pages(self, query: str, *,
                     country: str = "US",
                     limit: int = 10,
                     wait_seconds: float = 12.0) -> list[dict]:
        """Resolve a brand / company name to candidate Facebook page IDs.

        Drives Meta's Ad Library search box with ``query``, intercepts
        the typeahead RPC, and returns the suggested pages with their
        canonical IDs. Use the resulting ``page_id`` with
        ``mode="advertiser"`` (or ``mode="page_url"``) for a precise
        ad-library query — far more accurate than a free-text keyword
        search when the brand name is generic.

        Returns a list of dicts::

            [
              {"page_id": "...", "page_name": "...",
               "page_profile_uri": "...",
               "page_profile_picture_url": "...",
               "page_alias": "...",
               "page_like_count": int | None,
               "page_verified": bool | None,
               "category": "..."},
              ...
            ]
        """
        url = (
            f"https://www.facebook.com/ads/library/"
            f"?active_status=active&ad_type=all&country={country.upper()}"
            f"&search_type=keyword_unordered&media_type=all"
        )

        captured: dict[str, Any] = {"bodies": [], "errors": [],
                                     "raw_count": 0}

        def _on_response(resp):
            if _GRAPHQL_PATH not in resp.url:
                return
            try:
                if resp.request.method != "POST" or resp.status != 200:
                    return
                pd = resp.request.post_data or ""
                params = dict(urllib.parse.parse_qsl(pd))
                fn = params.get("fb_api_req_friendly_name", "")
                if fn not in _TYPEAHEAD_FRIENDLY_NAMES:
                    return
                body = resp.json()
                captured["raw_count"] += 1
                if body.get("errors"):
                    captured["errors"].append(body["errors"])
                if body.get("data"):
                    captured["bodies"].append(body)
            except Exception as e:
                log.debug("[meta_ads/typeahead] parse: %s", e)

        self.page.on("response", _on_response)
        try:
            log.info("[meta_ads/typeahead] navigating %s", url)
            if not safe_goto(self.page, url, timeout=45000, retries=1):
                self.last_status = {"error": "navigation failed",
                                    "mode": "typeahead"}
                return []

            # The search input doesn't fire typeahead until we focus +
            # type. Try a few selectors because Meta rotates classes.
            self.page.wait_for_timeout(2000)
            box = None
            for sel in (
                'input[type="search"]',
                'input[role="combobox"]',
                'input[aria-label*="Search" i]',
                'input[placeholder*="Search" i]',
                'input',
            ):
                try:
                    box = self.page.query_selector(sel)
                    if box and box.is_visible():
                        break
                except Exception:
                    continue
            if box is None:
                self.last_status = {"error": "search input not found",
                                    "mode": "typeahead"}
                return []
            try:
                box.click(timeout=5000)
                # Type slowly so the typeahead actually fires (instant
                # fill() sometimes skips the keystroke event).
                box.type(query, delay=80, timeout=10000)
            except Exception as e:
                log.warning("[meta_ads/typeahead] input fill: %s", e)
                # carry on — RPC may still have fired

            deadline = time.time() + wait_seconds
            while time.time() < deadline and not captured["bodies"]:
                self.page.wait_for_timeout(400)
        finally:
            try:
                self.page.remove_listener("response", _on_response)
            except Exception:
                pass

        pages = self._collect_typeahead_pages(captured["bodies"])
        self.last_status = {
            "mode": "typeahead", "country": country, "query": query,
            "graphql_calls": captured["raw_count"],
            "pages_found": len(pages),
            "errors": len(captured["errors"]),
        }
        return pages[:limit]

    @staticmethod
    def _collect_typeahead_pages(bodies: list[dict]) -> list[dict]:
        """Walk every captured typeahead body, dedup by page_id, and
        return a flat list of page dicts."""
        out: list[dict] = []
        seen: set[str] = set()
        for body in bodies:
            data = body.get("data") or {}
            # Three observed shapes — Meta rotates these:
            for path in [
                ("ad_library_main", "typeahead_search_results", "page_results"),
                ("ad_library_main", "typeahead_suggestions"),
                ("ad_library_main", "search_results_connection", "edges"),
            ]:
                node = data
                for key in path:
                    node = (node or {}).get(key) or {}
                if isinstance(node, list):
                    rows = node
                elif isinstance(node, dict):
                    rows = node.get("edges") or []
                else:
                    rows = []
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    inner = row.get("node") or row
                    page_id = str(
                        inner.get("page_id") or inner.get("pageID") or ""
                    )
                    if not page_id or page_id in seen:
                        continue
                    seen.add(page_id)
                    out.append({
                        "page_id": page_id,
                        "page_name": str(inner.get("page_name")
                                         or inner.get("pageName") or ""),
                        "page_profile_uri": str(
                            inner.get("page_profile_uri") or ""
                        ),
                        "page_profile_picture_url": str(
                            inner.get("page_profile_picture_url") or ""
                        ),
                        "page_alias": str(inner.get("page_alias") or ""),
                        "page_like_count": inner.get("page_like_count"),
                        "page_verified": inner.get("page_is_verified"),
                        "category": str(inner.get("category") or ""),
                    })
                if out:
                    break  # first matching path wins for this body
        return out

    # ------------------------------------------------------------------ helpers

    def _build_url(
        self,
        query: str,
        mode: str,
        country: str,
        status: str,
        ad_type: str,
        media_type: str,
        search_type: str,
        sort_by: Optional[str],
        page_ids: Sequence[str],
        publisher_platforms: Optional[Sequence[str]],
        start_date: Any,
    ) -> str:
        base = "https://www.facebook.com/ads/library/"
        params: list[tuple[str, str]] = [
            ("active_status", status),
            ("ad_type", ad_type),
            ("country", country.upper() if country.upper() != "ALL" else "ALL"),
            ("media_type", (media_type or "ALL").lower()),
            ("search_type", search_type),
        ]
        if query:
            params.append(("q", query))
        if page_ids:
            params.append(("view_all_page_id", page_ids[0]))
        if sort_by:
            params.append(("sort_data[mode]", sort_by))
            params.append(("sort_data[direction]", "desc"))
        if publisher_platforms:
            for i, plat in enumerate(publisher_platforms):
                params.append((f"publisher_platforms[{i}]", plat.lower()))
        sd = _to_epoch(start_date)
        if sd is not None:
            params.append(("start_date[min]", str(sd)))
        return base + "?" + urllib.parse.urlencode(params, safe=":[]")

    def _count_ads(self, bodies: list[dict]) -> int:
        return sum(self._extract_edges_count(b) for b in bodies)

    def _extract_edges_count(self, body: dict) -> int:
        try:
            data = body.get("data") or {}
            for path in [
                ("ad_library_main", "search_results_connection"),
                ("adLibraryMain", "searchResultsConnection"),
            ]:
                node = data
                for key in path:
                    node = (node or {}).get(key) or {}
                edges = node.get("edges") or []
                if edges:
                    return sum(
                        len(((e.get("node") or {}).get("collated_results")) or [])
                        for e in edges
                    )
        except Exception:
            return 0
        return 0

    def _collect_ads(self, bodies: list[dict]) -> list[dict]:
        """Walk every captured GraphQL body, dedup by collation_id then
        archive_id, and return raw ad dicts."""
        all_ads: list[dict] = []
        seen_collation: set[str] = set()
        seen_archive: set[str] = set()
        for body in bodies:
            data = body.get("data") or {}
            for path in [
                ("ad_library_main", "search_results_connection"),
                ("adLibraryMain", "searchResultsConnection"),
            ]:
                node = data
                for key in path:
                    node = (node or {}).get(key) or {}
                for edge in node.get("edges") or []:
                    enode = edge.get("node") or {}
                    collation_id = str(
                        enode.get("collation_id") or
                        enode.get("collationID") or ""
                    )
                    for ad in enode.get("collated_results") or []:
                        # Flatten snapshot fields if present.
                        snapshot = ad.get("snapshot") or {}
                        flat = dict(ad)
                        for k, v in snapshot.items():
                            flat.setdefault(k, v)
                        # Track collation membership
                        if collation_id and "collation_id" not in flat:
                            flat["collation_id"] = collation_id

                        archive_id = str(
                            flat.get("ad_archive_id") or
                            flat.get("adArchiveID") or
                            flat.get("id") or ""
                        )

                        # Dedup priority: collation_id > archive_id
                        if collation_id:
                            if collation_id in seen_collation:
                                continue
                            seen_collation.add(collation_id)
                        elif archive_id:
                            if archive_id in seen_archive:
                                continue
                            seen_archive.add(archive_id)

                        all_ads.append(flat)
        return all_ads

    # ── client-side filtering ──────────────────────────────────────────

    def _apply_filters(
        self,
        ads: list[dict],
        *,
        min_impressions: Optional[int],
        max_impressions: Optional[int],
        min_spend: Optional[int],
        max_spend: Optional[int],
        start_date: Optional[int],
        end_date: Optional[int],
        languages: Optional[list[str]],
        has_video: Optional[bool],
        has_image: Optional[bool],
    ) -> list[dict]:
        if not any([
            min_impressions, max_impressions, min_spend, max_spend,
            start_date, end_date, languages,
            has_video is not None, has_image is not None,
        ]):
            return ads

        def _passes(ad: dict) -> bool:
            imp = self._extract_range(ad, "impressions")
            sp = self._extract_range(ad, "spend")

            # Conservative: pass when range-data is missing.
            if min_impressions is not None and imp[1] is not None and imp[1] < min_impressions:
                return False
            if max_impressions is not None and imp[0] is not None and imp[0] > max_impressions:
                return False
            if min_spend is not None and sp[1] is not None and sp[1] < min_spend:
                return False
            if max_spend is not None and sp[0] is not None and sp[0] > max_spend:
                return False

            if start_date is not None or end_date is not None:
                ad_start = ad.get("start_date") or ad.get("startDate")
                if isinstance(ad_start, (int, float)):
                    if start_date is not None and ad_start < start_date:
                        return False
                    if end_date is not None and ad_start > end_date:
                        return False

            if languages:
                ad_langs = {
                    l.lower() for l in (ad.get("languages") or [])
                    if isinstance(l, str)
                }
                if ad_langs and not (set(languages) & ad_langs):
                    return False

            if has_video is True or has_video is False:
                vids = self._has_video(ad)
                if has_video and not vids:
                    return False
                if not has_video and vids:
                    return False

            if has_image is True or has_image is False:
                imgs = self._has_image(ad)
                if has_image and not imgs:
                    return False
                if not has_image and imgs:
                    return False

            return True

        return [a for a in ads if _passes(a)]

    @staticmethod
    def _has_video(ad: dict) -> bool:
        if ad.get("videos"):
            return True
        for card in ad.get("cards") or []:
            if card.get("video_hd_url") or card.get("video_sd_url"):
                return True
        return False

    @staticmethod
    def _has_image(ad: dict) -> bool:
        if ad.get("images"):
            return True
        for card in ad.get("cards") or []:
            if card.get("original_image_url") or card.get("resized_image_url"):
                return True
        return False

    @staticmethod
    def _extract_range(ad: dict, kind: str) -> tuple[Optional[int], Optional[int]]:
        """Pull ``(lower, upper)`` from impressions/spend/reach fields."""
        sources: list[Any] = []
        if kind == "impressions":
            sources = [
                ad.get("impressions_with_index"),
                ad.get("impressionsWithIndex"),
                ad.get("impressions"),
            ]
        elif kind == "spend":
            sources = [ad.get("spend"), ad.get("spendWithIndex")]
        elif kind == "reach":
            sources = [ad.get("reach"), ad.get("reach_estimate")]

        for src in sources:
            if isinstance(src, dict):
                lower = src.get("lower_bound") or src.get("lowerBound")
                upper = src.get("upper_bound") or src.get("upperBound")
                if lower is None and upper is None:
                    txt = src.get("impressions_text") or src.get("text")
                    if isinstance(txt, str):
                        return _parse_range_text(txt)
                    continue
                return (
                    _coerce_int(lower) if lower is not None else None,
                    _coerce_int(upper) if upper is not None else None,
                )
            if isinstance(src, str):
                return _parse_range_text(src)
        return (None, None)

    # ── Build the final SearchResult ──────────────────────────────────

    def _ad_to_result(self, ad: dict, country: str,
                      *, extract_rsoc: bool = False) -> SearchResult:
        ad_id = str(
            ad.get("ad_archive_id") or ad.get("adArchiveID") or
            ad.get("id") or ""
        )
        collation_id = str(
            ad.get("collation_id") or ad.get("collationID") or ""
        )
        page_name = str(ad.get("page_name") or ad.get("pageName") or "")
        page_id = str(ad.get("page_id") or ad.get("pageID") or "")
        body_text = self._extract_body_text(
            ad.get("body") or ad.get("body_text")
        )
        title = str(ad.get("title") or page_name or ad_id)

        snapshot_url = (
            f"https://www.facebook.com/ads/library/?id={ad_id}" if ad_id else ""
        )

        # Date math.
        start = ad.get("start_date") or ad.get("startDate")
        end = ad.get("end_date") or ad.get("endDate")
        days_running: Optional[int] = None
        if isinstance(start, (int, float)) and start > 0:
            end_ts = end if isinstance(end, (int, float)) and end > 0 else time.time()
            days_running = int((end_ts - start) / 86400)

        # Build creatives[] (carousel-aware).
        creatives = self._extract_creatives(ad)
        image_urls = [c["image_url"] for c in creatives if c.get("image_url")]
        video_urls = [c["video_url"] for c in creatives if c.get("video_url")]

        # Range fields.
        spend_lower, spend_upper = self._extract_range(ad, "spend")
        impressions_lower, impressions_upper = self._extract_range(ad, "impressions")
        reach_lower, reach_upper = self._extract_range(ad, "reach")

        currency = ad.get("currency")
        if not currency:
            spend = ad.get("spend") or {}
            if isinstance(spend, dict):
                currency = spend.get("currency")

        # Estimated audience size.
        eas_lower = eas_upper = None
        eas = ad.get("estimated_audience_size") or {}
        if isinstance(eas, dict):
            eas_lower = _coerce_int(eas.get("lower_bound") or eas.get("lowerBound"))
            eas_upper = _coerce_int(eas.get("upper_bound") or eas.get("upperBound"))

        # Demographics + regions.
        age_gender_dist: list[dict] = []
        for item in ad.get("demographic_distribution") or ad.get(
                "demographicDistribution") or []:
            if isinstance(item, dict):
                age_gender_dist.append({
                    "age": item.get("age", ""),
                    "gender": item.get("gender", ""),
                    "percentage": _coerce_float(item.get("percentage")),
                })

        region_dist: list[dict] = []
        for item in ad.get("delivery_by_region") or ad.get("deliveryByRegion") or []:
            if isinstance(item, dict):
                region_dist.append({
                    "region": item.get("region", ""),
                    "percentage": _coerce_float(item.get("percentage")),
                })

        # Page metadata.
        page_like_count = _coerce_int(
            ad.get("page_like_count") or ad.get("pageLikeCount")
        )
        page_verified = ad.get("page_is_verified") or ad.get("pageIsVerified")
        page_categories = ad.get("page_categories") or []
        if isinstance(page_categories, dict):
            page_categories = list(page_categories.values())

        # Compliance fields (political / EU).
        funding_entity = ad.get("funding_entity") or ad.get("fundingEntity")
        disclaimer = ad.get("disclaimer")
        bylines = ad.get("bylines") or []
        beneficiary_payers = (
            ad.get("beneficiary_payers") or ad.get("beneficiaryPayers") or []
        )
        ad_status = ad.get("ad_status") or ad.get("adStatus")

        # Active status: explicit > derived from ad_status.
        is_active = ad.get("is_active")
        if is_active is None:
            is_active = ad.get("isActive")
        if is_active is None and ad_status:
            is_active = (str(ad_status).upper() == "ACTIVE")

        # Languages, platforms.
        publisher_platforms = (
            ad.get("publisher_platforms") or ad.get("publisherPlatforms")
            or ad.get("publisher_platform") or []
        )
        if isinstance(publisher_platforms, str):
            publisher_platforms = [publisher_platforms]
        languages = ad.get("languages") or []

        # Snippet.
        snip_parts: list[str] = []
        if page_name:
            snip_parts.append(f"by {page_name}")
        if days_running is not None:
            snip_parts.append(f"{days_running}d running")
        if is_active:
            snip_parts.append("active")
        if body_text:
            snip_parts.append(body_text[:140].strip())

        # Optional RSOC keyword extraction from landing pages.
        rsoc_keywords: list[str] = []
        if extract_rsoc:
            for c in creatives:
                rsoc_keywords.extend(_extract_rsoc_from_url(c.get("link_url") or ""))
            link_url = ad.get("link_url") or ""
            rsoc_keywords.extend(_extract_rsoc_from_url(link_url))
            # dedupe preserving order
            seen: set[str] = set()
            uniq: list[str] = []
            for kw in rsoc_keywords:
                low = kw.lower()
                if low not in seen:
                    seen.add(low)
                    uniq.append(kw)
            rsoc_keywords = uniq

        r = SearchResult(
            title=title[:200],
            url=snapshot_url,
            snippet=" · ".join(snip_parts),
        )
        r.__dict__.update({
            # core identifiers
            "ad_archive_id": ad_id,
            "collation_id": collation_id,
            "collation_count": _coerce_int(
                ad.get("collation_count") or ad.get("collationCount")
            ),
            # page
            "page_name": page_name,
            "page_id": page_id,
            "page_profile_url": (
                ad.get("page_profile_uri") or ad.get("page_profile_url") or ""
            ),
            "page_profile_picture_url": ad.get("page_profile_picture_url") or "",
            "page_like_count": page_like_count,
            "page_verified": page_verified,
            "page_categories": page_categories,
            # status / dates
            "is_active": is_active,
            "ad_status": ad_status,
            "start_date": start,
            "end_date": end,
            "days_running": days_running,
            "country": country,
            # creative
            "title": title,
            "body_text": body_text,
            "cta_text": ad.get("cta_text") or "",
            "cta_type": ad.get("cta_type") or "",
            "link_url": ad.get("link_url") or "",
            "creatives": creatives,
            "image_urls": image_urls,
            "video_url": video_urls[0] if video_urls else "",
            "video_urls": video_urls,
            # platforms / categories / languages
            "categories": ad.get("categories") or [],
            "publisher_platforms": publisher_platforms,
            "languages": languages,
            # performance (political / EU)
            "currency": currency or "",
            "spend_lower": spend_lower,
            "spend_upper": spend_upper,
            "impressions_lower": impressions_lower,
            "impressions_upper": impressions_upper,
            "reach_lower": reach_lower,
            "reach_upper": reach_upper,
            "estimated_audience_size_lower": eas_lower,
            "estimated_audience_size_upper": eas_upper,
            "age_gender_distribution": age_gender_dist,
            "region_distribution": region_dist,
            # compliance
            "funding_entity": funding_entity or "",
            "disclaimer": disclaimer or "",
            "bylines": bylines,
            "beneficiary_payers": beneficiary_payers,
            # extras
            "rsoc_keywords": rsoc_keywords,
            "ad_type": ad.get("ad_type") or "",
        })
        return r

    @staticmethod
    def _extract_body_text(value: Any) -> str:
        """Body can be a plain str, a dict ``{"text": "..."}`` or None."""
        if value is None:
            return ""
        if isinstance(value, dict):
            return str(value.get("text") or "")
        return str(value)

    @staticmethod
    def _extract_creatives(ad: dict) -> list[dict]:
        """Return one dict per creative variation.

        Carousel ads put each card under ``cards[]``. Single-creative ads
        use top-level ``body / title / link_url / videos[] / images[]``.
        Legacy responses use ``ad_creative_bodies / ad_creative_link_titles``
        arrays. We support all three.
        """
        out: list[dict] = []

        # Format 1: cards[] (carousel)
        cards = ad.get("cards") or []
        if cards:
            for card in cards:
                out.append({
                    "body": MetaAdLibraryEngine._extract_body_text(card.get("body")),
                    "caption": card.get("caption"),
                    "title": card.get("title"),
                    "link_description": card.get("link_description"),
                    "link_url": card.get("link_url"),
                    "image_url": (
                        card.get("resized_image_url") or
                        card.get("original_image_url")
                    ),
                    "video_url": (
                        card.get("video_hd_url") or
                        card.get("video_sd_url")
                    ),
                    "video_hd_url": card.get("video_hd_url"),
                    "video_sd_url": card.get("video_sd_url"),
                    "thumbnail_url": card.get("video_preview_image_url"),
                    "cta_text": card.get("cta_text"),
                    "cta_type": card.get("cta_type"),
                })
            return out

        # Format 2: live API flat
        videos = ad.get("videos") or []
        images = ad.get("images") or []
        flat_present = bool(
            ad.get("body") or ad.get("title") or videos or images
        )
        if flat_present:
            first_video = videos[0] if videos else {}
            first_image = images[0] if images else {}

            video_hd = first_video.get("video_hd_url") if isinstance(first_video, dict) else None
            video_sd = first_video.get("video_sd_url") if isinstance(first_video, dict) else None
            image_url = (
                (first_image.get("original_image_url") if isinstance(first_image, dict) else None) or
                (first_image.get("resized_image_url") if isinstance(first_image, dict) else None) or
                (first_image if isinstance(first_image, str) else None)
            )

            out.append({
                "body": MetaAdLibraryEngine._extract_body_text(ad.get("body")),
                "caption": ad.get("caption"),
                "title": ad.get("title"),
                "link_description": ad.get("link_description"),
                "link_url": ad.get("link_url"),
                "image_url": image_url,
                "video_url": video_hd or video_sd,
                "video_hd_url": video_hd,
                "video_sd_url": video_sd,
                "thumbnail_url": (
                    first_video.get("video_preview_image_url")
                    if isinstance(first_video, dict) else None
                ),
                "cta_text": ad.get("cta_text"),
                "cta_type": ad.get("cta_type"),
            })
            # also attach extras images/videos beyond the first
            for img in images[1:]:
                u = img.get("original_image_url") or img.get("resized_image_url") \
                    if isinstance(img, dict) else (img if isinstance(img, str) else None)
                if u:
                    out.append({"image_url": u})
            for vid in videos[1:]:
                if isinstance(vid, dict):
                    u = vid.get("video_hd_url") or vid.get("video_sd_url")
                    if u:
                        out.append({"video_url": u, "video_hd_url": vid.get("video_hd_url"),
                                    "video_sd_url": vid.get("video_sd_url")})
            return out

        # Format 3: legacy ad_creative_bodies arrays
        bodies = ad.get("ad_creative_bodies") or ad.get("adCreativeBodies") or []
        captions = (
            ad.get("ad_creative_link_captions") or
            ad.get("adCreativeLinkCaptions") or []
        )
        descriptions = (
            ad.get("ad_creative_link_descriptions") or
            ad.get("adCreativeLinkDescriptions") or []
        )
        titles = (
            ad.get("ad_creative_link_titles") or
            ad.get("adCreativeLinkTitles") or []
        )
        n = max(len(bodies), len(titles), 1)
        snap_cards = (ad.get("snapshot") or {}).get("cards") or []
        for i in range(n):
            entry = {
                "body": bodies[i] if i < len(bodies) else None,
                "caption": captions[i] if i < len(captions) else None,
                "title": titles[i] if i < len(titles) else None,
                "link_description": descriptions[i] if i < len(descriptions) else None,
            }
            if i < len(snap_cards):
                c = snap_cards[i]
                entry.update({
                    "link_url": c.get("link_url"),
                    "image_url": c.get("resized_image_url") or c.get("original_image_url"),
                    "video_url": c.get("video_hd_url") or c.get("video_sd_url"),
                    "video_hd_url": c.get("video_hd_url"),
                    "video_sd_url": c.get("video_sd_url"),
                    "cta_text": c.get("cta_text"),
                    "cta_type": c.get("cta_type"),
                })
            out.append(entry)
        return out

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        return self.search(query, limit)


# ---------------------------------------------------------------------------
# Module-level helpers for parsing range strings like "1K-5K", ">1M"
# ---------------------------------------------------------------------------

_MULTIPLIERS = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}
_RANGE_RE = re.compile(r"[\d,.]+[KMB]?")


def _parse_range_text(text: str) -> tuple[Optional[int], Optional[int]]:
    """Parse strings like ``'1K-5K'``, ``'>1M'``, ``'$9K-$10K'`` into ints."""
    if not text:
        return (None, None)
    parts = _RANGE_RE.findall(text)
    vals: list[int] = []
    for part in parts:
        suffix = part[-1].upper() if part[-1].upper() in _MULTIPLIERS else ""
        num_str = part[:-1] if suffix else part
        num_str = num_str.replace(",", "")
        try:
            n = float(num_str)
            if suffix:
                n *= _MULTIPLIERS[suffix]
            vals.append(int(n))
        except ValueError:
            continue
    if len(vals) >= 2:
        return vals[0], vals[1]
    if len(vals) == 1:
        return vals[0], None
    return (None, None)


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


def _coerce_float(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
