"""TikTok Creative Center engine — competitive intelligence on TikTok ads.

Background
----------
TikTok exposes a public "Creative Center" portal at
``ads.tiktok.com/business/creativecenter`` showing **Top Ads** ranked by
CTR / likes / 6-sec play rate / conversion rate, plus **Trending Songs**
/ **Trending Hashtags** / **Trending Creators**. The frontend is a React
SPA that calls a lightly-defended internal REST API at
``creative_radar_api/v1/...``. No login, no key required — but the raw
API rejects requests without a real first-party session (returns
``code=40101 no permission``).

Strategy
--------
Instead of fighting the auth check, **let the page itself fire the API
call** and intercept the response via ``page.on("response")``. CloakBrowser
makes the navigation indistinguishable from a real visitor, so the
session cookies / signed headers are minted automatically. We just listen.

This trick gives us:

* Ad ``id``, ``ad_title``, ``brand_name``
* Performance signals — ``ctr``, ``like``, ``cost`` (cost trend index,
  not real $), ``cvr``
* Industry classification — ``industry_key``, ``objective_key``
* **Direct video URLs** at 5 resolutions (360p/480p/540p/720p/1080p) +
  cover image URL — perfect for downloading creative for swipe files

Modes
-----
``mode="top_ads"`` (default)
    Browse Top Ads with filters: ``period`` (7/30/180), ``country_code``
    (US/GB/JP/...), ``industry_id``, ``order_by`` (for_you/ctr/like/
    play_6s_rate/cvr).

``mode="trending_songs"``
    Trending songs used in TikTok ads. ``period`` and ``country_code``
    accepted.

``mode="trending_hashtags"``
    Trending hashtags. ``period`` and ``country_code`` accepted.

``mode="trending_creators"``
    Trending creators / KOLs. ``period`` and ``country_code`` accepted.

The ``query`` argument is currently used only as a **post-filter** on
``ad_title`` / ``brand_name`` (Creative Center has no public keyword
search endpoint — it's by category and trend). For exact-keyword search
across creative copy, see the ``meta_ad_library`` engine, which has
GraphQL keyword query support.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .base import BaseEngine, SearchResult
from ..core import safe_goto

log = logging.getLogger(__name__)


# Public Creative Center pages that fire the right API endpoint on load.
_PAGE_URLS = {
    "top_ads":            "https://ads.tiktok.com/business/creativecenter/inspiration/topads/pc/en?period={period}&region={country}",
    "trending_songs":     "https://ads.tiktok.com/business/creativecenter/inspiration/popular/music/pc/en?period={period}&region={country}",
    "trending_hashtags":  "https://ads.tiktok.com/business/creativecenter/inspiration/popular/hashtag/pc/en?period={period}&region={country}",
    "trending_creators":  "https://ads.tiktok.com/business/creativecenter/inspiration/popular/creator/pc/en?period={period}&region={country}",
}

# API path fragments — when ``X in resp.url`` we know we got the right call.
_API_MARKERS = {
    "top_ads":            "/creative_radar_api/v1/top_ads/v2/list",
    "trending_songs":     "/creative_radar_api/v1/popular_trend/song/list",
    "trending_hashtags":  "/creative_radar_api/v1/popular_trend/hashtag/list",
    "trending_creators":  "/creative_radar_api/v1/popular_trend/creator/list",
}

# Valid filter values (sanity-checked at engine boundary; the API
# silently drops anything else).
_VALID_PERIODS = {7, 30, 180}
_VALID_ORDER_BY = {"for_you", "ctr", "like", "play_6s_rate", "cvr"}


class TikTokCreativeCenterEngine(BaseEngine):
    """TikTok Creative Center adapter — Top Ads + Trending content."""

    name = "tiktok_creative_center"
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
        mode: str = "top_ads",
        period: int = 30,
        country_code: str = "US",
        order_by: str = "for_you",
        industry_id: str | None = None,
    ) -> list[SearchResult]:
        """Browse the Creative Center.

        :param query:         Optional substring to filter ``ad_title`` /
                              ``brand_name`` after fetch (Creative Center
                              has no real keyword search). Pass ``""`` to
                              return everything.
        :param limit:         Cap on returned rows.
        :param mode:          One of ``top_ads`` / ``trending_songs`` /
                              ``trending_hashtags`` / ``trending_creators``.
        :param period:        Lookback window in days — 7, 30 or 180.
        :param country_code:  ISO-3166 code (US, GB, JP, KR, ID, BR, ...).
        :param order_by:      ``top_ads`` only — for_you / ctr / like /
                              play_6s_rate / cvr.
        :param industry_id:   ``top_ads`` only — narrow to a single
                              industry tag (e.g. ``label_10100000000``).
                              Discoverable via the inspector or the
                              ``top_ads/v2/filters`` endpoint.
        """
        m = (mode or "top_ads").lower()
        if m not in _PAGE_URLS:
            raise ValueError(
                f"unknown mode {m!r}; choose one of {sorted(_PAGE_URLS)}"
            )
        if period not in _VALID_PERIODS:
            log.warning("[ttcc] period=%s not in %s, defaulting to 30",
                        period, sorted(_VALID_PERIODS))
            period = 30
        if order_by not in _VALID_ORDER_BY:
            order_by = "for_you"

        self.last_status = {
            "mode": m, "period": period, "country": country_code,
            "order_by": order_by, "industry_id": industry_id,
            "query": query,
        }

        url = _PAGE_URLS[m].format(period=period, country=country_code)
        if m == "top_ads" and industry_id:
            url += f"&industry={industry_id}"
        if m == "top_ads" and order_by != "for_you":
            url += f"&order_by={order_by}"

        captured: dict[str, Any] = {"body": None, "url": None, "err": None}
        marker = _API_MARKERS[m]

        def _on_response(resp):
            try:
                if marker in resp.url and resp.status == 200 and captured["body"] is None:
                    captured["body"] = resp.json()
                    captured["url"] = resp.url
            except Exception as e:
                captured["err"] = str(e)

        self.page.on("response", _on_response)
        try:
            log.info("[ttcc] navigating %s", url)
            if not safe_goto(self.page, url, timeout=30000, retries=1):
                log.warning("[ttcc] navigation failed")
                return []

            # Yield to playwright so queued response events run; poll for
            # the captured body. We can't use time.sleep — sync API events
            # only fire when control returns to the playwright runner.
            deadline = time.time() + 25.0
            while time.time() < deadline and captured["body"] is None:
                self.page.wait_for_timeout(500)
        finally:
            try:
                self.page.remove_listener("response", _on_response)
            except Exception:
                pass

        if captured["body"] is None:
            self.last_status["error"] = captured.get("err") or "no response captured"
            log.warning("[ttcc] no API response captured (%s)",
                        self.last_status["error"])
            return []

        data = (captured["body"] or {}).get("data") or {}
        if m == "top_ads":
            rows = data.get("materials") or []
        else:
            # Trending endpoints all use ``list`` (sometimes ``data: null``
            # when locale lacks data — graceful empty).
            rows = data.get("list") or []

        if not rows:
            return []

        # Optional substring filter so ``--engine tiktok_creative_center
        # 'fitness'`` actually narrows the feed even though the upstream
        # endpoint doesn't support keyword search.
        if query:
            ql = query.strip().lower()
            if ql:
                rows = [r for r in rows if self._row_matches(r, ql, m)]

        rows = rows[:limit]
        return [self._row_to_result(r, m) for r in rows]

    # ------------------------------------------------------------------ helpers

    def _row_matches(self, row: dict, ql: str, mode: str) -> bool:
        """True if ``ql`` appears in any human-readable field of ``row``."""
        haystack: list[str] = []
        if mode == "top_ads":
            haystack += [str(row.get("ad_title") or ""),
                         str(row.get("brand_name") or ""),
                         str(row.get("industry_key") or "")]
        elif mode == "trending_songs":
            haystack += [str(row.get("title") or ""),
                         str(row.get("author") or "")]
        elif mode == "trending_hashtags":
            haystack += [str(row.get("hashtag_name") or ""),
                         str(row.get("name") or "")]
        elif mode == "trending_creators":
            haystack += [str(row.get("nick_name") or ""),
                         str(row.get("name") or "")]
        return any(ql in s.lower() for s in haystack)

    def _row_to_result(self, row: dict, mode: str) -> SearchResult:
        """Convert one upstream row into a SearchResult + extra fields."""
        if mode == "top_ads":
            ad_id = str(row.get("id") or "")
            title = str(row.get("ad_title") or row.get("brand_name") or ad_id)
            brand = str(row.get("brand_name") or "")
            url = (
                f"https://ads.tiktok.com/business/creativecenter/inspiration/topads/"
                f"pc/en/detail/{ad_id}" if ad_id else ""
            )
            snippet_parts = []
            if brand:
                snippet_parts.append(f"by {brand}")
            ctr = row.get("ctr")
            if ctr is not None:
                snippet_parts.append(f"CTR={ctr}%")
            like = row.get("like")
            if like is not None:
                snippet_parts.append(f"likes={like}")
            objective = (row.get("objective_key") or "").replace("campaign_objective_", "")
            if objective:
                snippet_parts.append(f"goal={objective}")
            snippet = " · ".join(snippet_parts)

            r = SearchResult(title=title, url=url, snippet=snippet)
            # Attach the rich ad payload via __dict__ so the JSON path
            # picks it up (consistent with the reddit / instagram pattern).
            video_info = row.get("video_info") or {}
            video_urls = video_info.get("video_url") or {}
            r.__dict__.update({
                "ad_id": ad_id,
                "brand_name": brand,
                "industry_key": row.get("industry_key") or "",
                "objective_key": row.get("objective_key") or "",
                "ctr": row.get("ctr"),
                "likes": row.get("like"),
                "cost_index": row.get("cost"),
                "cvr": row.get("cvr"),
                "play_6s_rate": row.get("play_6s_rate"),
                "is_search": row.get("is_search"),
                "duration_s": video_info.get("duration"),
                "cover_image_url": video_info.get("cover") or "",
                "video_url": video_urls.get("720p")
                              or video_urls.get("540p")
                              or video_urls.get("480p")
                              or video_urls.get("1080p")
                              or video_urls.get("360p")
                              or "",
                "video_urls": dict(video_urls),  # all resolutions
                "width": video_info.get("width"),
                "height": video_info.get("height"),
                "vid": video_info.get("vid") or "",
            })
            return r

        if mode == "trending_songs":
            song_id = str(row.get("clip_id") or row.get("id") or "")
            title = str(row.get("title") or "(untitled)")
            author = str(row.get("author") or "")
            r = SearchResult(
                title=title,
                url=row.get("link") or row.get("clip_link") or "",
                snippet=f"by {author}" if author else "",
            )
            r.__dict__.update({
                "song_id": song_id,
                "author": author,
                "duration_s": row.get("duration"),
                "rank": row.get("rank"),
                "cover_image_url": row.get("cover") or "",
                "audio_url": row.get("song_url") or row.get("audio_url") or "",
                "is_commerce_music": row.get("is_commerce_music"),
            })
            return r

        if mode == "trending_hashtags":
            tag = str(row.get("hashtag_name") or row.get("name") or "")
            r = SearchResult(
                title=f"#{tag}" if tag else "",
                url=f"https://www.tiktok.com/tag/{tag}" if tag else "",
                snippet=f"posts={row.get('publish_cnt')}" if row.get("publish_cnt") else "",
            )
            r.__dict__.update({
                "hashtag": tag,
                "publish_cnt": row.get("publish_cnt"),
                "video_views": row.get("video_views"),
                "rank": row.get("rank"),
                "industry": row.get("industry") or "",
            })
            return r

        # trending_creators
        nick = str(row.get("nick_name") or row.get("name") or "")
        username = str(row.get("user_name") or "")
        r = SearchResult(
            title=nick or username,
            url=f"https://www.tiktok.com/@{username}" if username else "",
            snippet=f"followers={row.get('follower_cnt')}"
                    if row.get("follower_cnt") else "",
        )
        r.__dict__.update({
            "username": username,
            "nick_name": nick,
            "follower_cnt": row.get("follower_cnt"),
            "liked_cnt": row.get("liked_cnt"),
            "country_code": row.get("country_code"),
            "rank": row.get("rank"),
            "avatar_url": row.get("avatar_url") or "",
            "tt_link": row.get("tt_link") or "",
        })
        return r

    # ------------------------------------------------------------------ BaseEngine
    # `BaseEngine.search()` calls `_do_search(query, limit)` in its retry
    # loop. We bypass that loop by overriding `search()` directly above,
    # but we still need a stub here so abstract enforcement passes.

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        return self.search(query, limit)
