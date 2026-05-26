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
    the search string. Filters: ``country`` (US/GB/...), ``active_only``,
    ``media_type`` (ALL/IMAGE/VIDEO/MEME).

``mode="advertiser"``
    List a specific page's full ad library. ``query`` is the page name
    (typeahead-resolved) **or** ``"page_id:<numeric>"`` for an exact ID.

Returned fields per ad
----------------------
``ad_archive_id``, ``page_name``, ``page_id``, ``page_profile_url``,
``page_profile_picture_url``, ``is_active``, ``start_date``, ``end_date``,
``days_running``, ``country``, ``cta_text``, ``link_url``, ``body_text``,
``title``, ``image_urls[]``, ``video_urls[]``, ``categories[]``,
``publisher_platforms[]``. For political / EU ads, also ``spend_lower``,
``spend_upper``, ``impressions_lower``, ``impressions_upper``.
"""

from __future__ import annotations

import logging
import re
import time
import urllib.parse
from typing import Any

from .base import BaseEngine, SearchResult
from ..core import safe_goto

log = logging.getLogger(__name__)


_GRAPHQL_PATH = "/api/graphql/"
_FRIENDLY_NAMES = {
    "AdLibrarySearchPaginationQuery",
    "AdLibrarySearchResultsQuery",
    "AdLibraryViewAllSearchResultsQuery",
    "AdLibraryAggregatorAdsByAdvertiserQuery",
}


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
        query: str,
        limit: int = 20,
        *,
        mode: str = "keyword",
        country: str = "US",
        active_only: bool = True,
        media_type: str = "ALL",
    ) -> list[SearchResult]:
        """Search the Meta Ad Library.

        :param query:        Keyword (mode=keyword) or page name / ``page_id:<id>``
                             (mode=advertiser).
        :param limit:        Cap on returned ads.
        :param mode:         ``keyword`` (default) or ``advertiser``.
        :param country:      ISO-3166 code. ``ALL`` is also accepted.
        :param active_only:  Only ads currently running.
        :param media_type:   ``ALL`` / ``IMAGE`` / ``VIDEO`` / ``MEME``.
        """
        m = (mode or "keyword").lower()
        if m not in ("keyword", "advertiser"):
            raise ValueError(f"unknown mode {m!r}")
        self.last_status = {
            "mode": m, "country": country, "active_only": active_only,
            "media_type": media_type, "query": query,
        }

        url = self._build_url(query, m, country, active_only, media_type)
        captured: dict[str, Any] = {"bodies": [], "errors": [], "raw_count": 0}

        def _on_response(resp):
            if _GRAPHQL_PATH not in resp.url:
                return
            try:
                if resp.request.method != "POST" or resp.status != 200:
                    return
                # Friendly-name filter avoids parsing the dozens of
                # housekeeping queries the page also fires.
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
                # Stop early if we have enough ads.
                if self._count_ads(captured["bodies"]) >= limit:
                    break
        finally:
            try:
                self.page.remove_listener("response", _on_response)
            except Exception:
                pass

        ads = self._collect_ads(captured["bodies"])
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

        return [self._ad_to_result(a, country) for a in ads[:limit]]

    # ------------------------------------------------------------------ helpers

    def _build_url(
        self, query: str, mode: str, country: str,
        active_only: bool, media_type: str,
    ) -> str:
        base = "https://www.facebook.com/ads/library/"
        params = {
            "active_status": "active" if active_only else "all",
            "ad_type": "all",
            "country": country.upper() if country.upper() != "ALL" else "ALL",
            "media_type": (media_type or "ALL").lower(),
        }
        if mode == "keyword":
            params["q"] = query
            params["search_type"] = "keyword_unordered"
        else:
            # mode == "advertiser"
            if query.startswith("page_id:"):
                params["view_all_page_id"] = query[len("page_id:"):]
                params["search_type"] = "page"
            else:
                params["q"] = query
                params["search_type"] = "page"
        return base + "?" + urllib.parse.urlencode(params, safe=":")

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
                    # Sum up collated_results lengths.
                    return sum(
                        len(((e.get("node") or {}).get("collated_results")) or [])
                        for e in edges
                    )
        except Exception:
            return 0
        return 0

    def _collect_ads(self, bodies: list[dict]) -> list[dict]:
        all_ads: list[dict] = []
        seen_ids: set[str] = set()
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
                    for ad in enode.get("collated_results") or []:
                        # Flatten snapshot fields if present.
                        snapshot = ad.get("snapshot") or {}
                        flat = dict(ad)
                        for k, v in snapshot.items():
                            flat.setdefault(k, v)
                        ad_id = str(flat.get("ad_archive_id") or
                                    flat.get("adArchiveID") or
                                    flat.get("id") or "")
                        if ad_id and ad_id in seen_ids:
                            continue
                        if ad_id:
                            seen_ids.add(ad_id)
                        all_ads.append(flat)
        return all_ads

    def _ad_to_result(self, ad: dict, country: str) -> SearchResult:
        ad_id = str(ad.get("ad_archive_id") or ad.get("adArchiveID") or
                    ad.get("id") or "")
        page_name = str(ad.get("page_name") or ad.get("pageName") or "")
        page_id = str(ad.get("page_id") or ad.get("pageID") or "")
        body_text = str(ad.get("body") or ad.get("body_text") or "")
        title = str(ad.get("title") or page_name or ad_id)

        # Ad Library snapshot URL (the canonical "view this ad" link).
        snapshot_url = (
            f"https://www.facebook.com/ads/library/?id={ad_id}" if ad_id else ""
        )

        # Date math.
        start = ad.get("start_date") or ad.get("startDate")
        end = ad.get("end_date") or ad.get("endDate")
        days_running: int | None = None
        if isinstance(start, (int, float)) and start > 0:
            end_ts = end if isinstance(end, (int, float)) and end > 0 else time.time()
            days_running = int((end_ts - start) / 86400)

        # Media URLs.
        image_urls: list[str] = []
        video_urls: list[str] = []
        # Top-level images
        for fld in ("images", "image_urls"):
            v = ad.get(fld)
            if isinstance(v, list):
                for it in v:
                    if isinstance(it, str):
                        image_urls.append(it)
                    elif isinstance(it, dict):
                        for k in ("original_image_url", "resized_image_url",
                                  "watermarked_resized_image_url", "url"):
                            if it.get(k):
                                image_urls.append(it[k]); break
        for fld in ("videos", "video_urls"):
            v = ad.get(fld)
            if isinstance(v, list):
                for it in v:
                    if isinstance(it, str):
                        video_urls.append(it)
                    elif isinstance(it, dict):
                        for k in ("video_hd_url", "video_sd_url",
                                  "watermarked_video_hd_url", "url"):
                            if it.get(k):
                                video_urls.append(it[k]); break
        # Cards (carousel) also contain media.
        for card in ad.get("cards") or []:
            for k in ("original_image_url", "resized_image_url"):
                if card.get(k):
                    image_urls.append(card[k]); break
            for k in ("video_hd_url", "video_sd_url"):
                if card.get(k):
                    video_urls.append(card[k]); break

        # Spend / impressions (only present for political / EU).
        spend_lower = spend_upper = None
        spend = ad.get("spend") or {}
        if isinstance(spend, dict):
            spend_lower = spend.get("lower_bound")
            spend_upper = spend.get("upper_bound")

        impressions_lower = impressions_upper = None
        imps = ad.get("impressions_with_index") or ad.get("impressions") or {}
        if isinstance(imps, dict):
            impressions_lower = imps.get("impressions_lower_bound") or imps.get("lower_bound")
            impressions_upper = imps.get("impressions_upper_bound") or imps.get("upper_bound")

        # Build the snippet.
        snip_parts = []
        if page_name:
            snip_parts.append(f"by {page_name}")
        if days_running is not None:
            snip_parts.append(f"{days_running}d running")
        if ad.get("is_active") is True:
            snip_parts.append("active")
        if body_text:
            snip_parts.append(body_text[:140].strip())

        r = SearchResult(
            title=title[:200],
            url=snapshot_url,
            snippet=" · ".join(snip_parts),
        )
        r.__dict__.update({
            "ad_archive_id": ad_id,
            "page_name": page_name,
            "page_id": page_id,
            "page_profile_url": ad.get("page_profile_uri") or ad.get("page_profile_url") or "",
            "page_profile_picture_url": ad.get("page_profile_picture_url") or "",
            "is_active": ad.get("is_active"),
            "start_date": start,
            "end_date": end,
            "days_running": days_running,
            "country": country,
            "cta_text": ad.get("cta_text") or "",
            "cta_type": ad.get("cta_type") or "",
            "link_url": ad.get("link_url") or "",
            "body_text": body_text,
            "title": title,
            "image_urls": image_urls,
            "video_url": video_urls[0] if video_urls else "",
            "video_urls": video_urls,
            "categories": ad.get("categories") or [],
            "publisher_platforms": ad.get("publisher_platforms") or [],
            "currency": ad.get("currency") or "",
            "spend_lower": spend_lower,
            "spend_upper": spend_upper,
            "impressions_lower": impressions_lower,
            "impressions_upper": impressions_upper,
        })
        return r

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        return self.search(query, limit)
