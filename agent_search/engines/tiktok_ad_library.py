"""TikTok Ad Library engine — advertiser-specific TikTok ads.

Background
----------
TikTok runs **two** distinct ad libraries:

* ``ads.tiktok.com/business/creativecenter`` — the **Creative Center**,
  shows *Top Ads* across the whole platform ranked by performance. No
  login. Best for trend / category research. We expose this via the
  separate :mod:`tiktok_creative_center` engine.

* ``library.tiktok.com`` — the **Commercial Content Library**, mandated
  by EU DSA. Shows *every advertiser's* paid ads in EU/UK, with
  start/end dates, target audience size estimates, advertiser
  verification, and disclaimers. **The public view is limited to EU/UK
  regions** — for global advertiser-specific intel, login on the
  TikTok-for-Business side is required (and the official API at
  ``business-api.tiktok.com`` requires app approval which is slow).

This engine wraps the public ``library.tiktok.com`` and exposes:

* ``mode="advertiser"`` — search ads by advertiser name
  (``--query "shopify"``). Supported regions are EU member states + UK.
* ``mode="region_top"`` — list current top advertisers for a region.

Strategy
--------
The DSA-mandated frontend at ``library.tiktok.com`` is a SPA that calls
``/api/v1/ad/list`` etc. We intercept the response. **Outside EU/UK
the page renders an empty state** — the engine detects this and returns
a clear hint to the caller.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .base import BaseEngine, SearchResult
from ..core import safe_goto

log = logging.getLogger(__name__)


_SUPPORTED_REGIONS = {
    "AT", "BE", "BG", "CH", "CY", "CZ", "DE", "DK", "EE", "ES", "FI",
    "FR", "GB", "GR", "HR", "HU", "IE", "IS", "IT", "LI", "LT", "LU",
    "LV", "MT", "NL", "NO", "PL", "PT", "RO", "SE", "SI", "SK",
}


class TikTokAdLibraryEngine(BaseEngine):
    """TikTok Ad Library (library.tiktok.com) adapter."""

    name = "tiktok_ad_library"
    max_retries = 1

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}

    # ------------------------------------------------------------------ public API

    def search(  # type: ignore[override]
        self,
        query: str,
        limit: int = 20,
        *,
        mode: str = "advertiser",
        region: str = "GB",
        days: int = 30,
    ) -> list[SearchResult]:
        m = (mode or "advertiser").lower()
        region = (region or "GB").upper()
        self.last_status = {"mode": m, "region": region, "days": days}

        if region not in _SUPPORTED_REGIONS:
            self.last_status["warning"] = (
                f"region={region!r} not in EU/UK — TikTok Ad Library returns "
                f"no data outside {sorted(_SUPPORTED_REGIONS)[:5]}... use "
                f"`--engine tiktok_creative_center` for global Top Ads."
            )
            log.warning("[ttal] %s", self.last_status["warning"])

        captured: dict[str, Any] = {"bodies": []}

        def _on_response(resp):
            if "library.tiktok.com/api/v1/" not in resp.url:
                return
            try:
                if resp.status != 200:
                    return
                # Capture endpoints that look like ad list responses.
                if any(seg in resp.url for seg in ("/ad/list", "/ad/detail",
                                                   "/advertiser/list",
                                                   "/advertisers/list",
                                                   "/aggregator/list")):
                    body = resp.json()
                    captured["bodies"].append({"url": resp.url, "body": body})
            except Exception as e:
                log.debug("[ttal] response parse error: %s", e)

        end = int(time.time() * 1000)
        start = end - (days * 86400 * 1000)
        url = (
            f"https://library.tiktok.com/ads"
            f"?region={region}&start_time={start}&end_time={end}"
            f"&adv_name={query}&query_type=&sort_type=last_shown_date,desc"
        )

        self.page.on("response", _on_response)
        try:
            log.info("[ttal] navigating %s", url)
            if not safe_goto(self.page, url, timeout=30000, retries=1):
                self.last_status["error"] = "navigation failed"
                return []
            deadline = time.time() + 18.0
            scrolls = 0
            while time.time() < deadline and len(captured["bodies"]) < 3:
                self.page.wait_for_timeout(700)
                if scrolls < 6:
                    try:
                        self.page.mouse.wheel(0, 1500)
                    except Exception:
                        pass
                    scrolls += 1
        finally:
            try:
                self.page.remove_listener("response", _on_response)
            except Exception:
                pass

        ads = self._collect_ads(captured["bodies"])
        self.last_status["ads_found"] = len(ads)
        if not ads:
            log.warning(
                "[ttal] 0 ads returned (region=%s). For non-EU/UK data, "
                "either log in via `agentsearch login tiktok_business` "
                "(future P1.5-N work) or use the Creative Center engine.",
                region,
            )
        return [self._ad_to_result(a, region) for a in ads[:limit]]

    # ------------------------------------------------------------------ helpers

    def _collect_ads(self, bodies: list[dict]) -> list[dict]:
        ads: list[dict] = []
        seen: set[str] = set()
        for entry in bodies:
            body = entry.get("body") or {}
            # Common shapes observed:
            #   { "data": { "ad_list": [...], "page_info": ... } }
            #   { "data": { "list":    [...] } }
            #   { "code": 0, "data": [...] }
            data = body.get("data") if isinstance(body.get("data"), (dict, list)) else body
            if isinstance(data, dict):
                lst = (
                    data.get("ad_list")
                    or data.get("list")
                    or data.get("ads")
                    or data.get("aggregators")
                    or []
                )
            elif isinstance(data, list):
                lst = data
            else:
                lst = []
            for ad in lst:
                if not isinstance(ad, dict):
                    continue
                aid = str(ad.get("ad_id") or ad.get("id") or
                          ad.get("creative_id") or "")
                if aid and aid in seen:
                    continue
                if aid:
                    seen.add(aid)
                ads.append(ad)
        return ads

    def _ad_to_result(self, ad: dict, region: str) -> SearchResult:
        ad_id = str(ad.get("ad_id") or ad.get("id") or
                    ad.get("creative_id") or "")
        adv_name = str(ad.get("advertiser_name") or ad.get("brand") or "")
        adv_id = str(ad.get("advertiser_id") or ad.get("biz_id") or "")
        title = str(ad.get("ad_title") or ad.get("title") or adv_name or ad_id)
        text = str(ad.get("text") or ad.get("ad_text") or "")
        first_shown = ad.get("first_shown_date") or ad.get("start_time")
        last_shown = ad.get("last_shown_date") or ad.get("end_time")

        # Extract media URLs.
        video_url = ""
        for k in ("video_url", "video_link", "play_url"):
            if ad.get(k):
                video_url = ad[k]; break
        image_url = ""
        for k in ("image_url", "cover", "thumbnail"):
            if ad.get(k):
                image_url = ad[k]; break

        days_running = None
        if first_shown and last_shown:
            try:
                days_running = int(
                    (int(last_shown) - int(first_shown)) / (1000 * 86400)
                )
            except Exception:
                days_running = None

        snippet_parts = []
        if adv_name:
            snippet_parts.append(f"by {adv_name}")
        if days_running is not None:
            snippet_parts.append(f"{days_running}d running")
        if text:
            snippet_parts.append(text[:120])

        url = (
            f"https://library.tiktok.com/ads/detail/{ad_id}?region={region}"
            if ad_id else ""
        )
        r = SearchResult(
            title=title[:200],
            url=url,
            snippet=" · ".join(snippet_parts),
        )
        r.__dict__.update({
            "ad_id": ad_id,
            "advertiser_name": adv_name,
            "advertiser_id": adv_id,
            "first_shown": first_shown,
            "last_shown": last_shown,
            "days_running": days_running,
            "region": region,
            "text": text,
            "video_url": video_url,
            "image_url": image_url,
            "raw": ad,
        })
        return r

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        return self.search(query, limit)
