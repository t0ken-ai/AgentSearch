"""TikTok Creative Center engine — competitive intelligence on TikTok ads.

Background
----------
TikTok exposes a public "Creative Center" portal at
``ads.tiktok.com/business/creativecenter`` showing 19 distinct datasets:

* **Top Ads dashboards** — ranked ads by performance, with per-ad
  analytics, second-by-second keyframe metrics, and percentile vs the
  industry average.
* **Keyword Insights** — keyword popularity / CPA / CTR / CVR + the
  videos that use each keyword + sentence templates.
* **Creative Insights** — creative pattern labels (hook style, scene
  count, CTA placement) ranked by performance.
* **Top Products** — ranked TikTok-Shop products by ecom category.
* **Trending** — songs (popular + breakout), hashtags, creators,
  videos, with detail endpoints for each.

The frontend is a React SPA that calls a lightly-defended REST API at
``creative_radar_api/v1/...``. The raw API rejects requests without a
real first-party session (``user-sign``, ``timestamp``, ``web-id``
headers + valid ``sessionid_ads`` cookie).

Strategy
--------
Instead of fighting the auth check, **let the page itself fire the API
call** and intercept the response via ``page.on("response")``. CloakBrowser
makes the navigation indistinguishable from a real visitor; for endpoints
that require login, the engine works transparently when the user has
authenticated to ``ads.tiktok.com`` once (cookies persist).

This is the same pattern as ``meta_ad_library.py`` — high-stability,
low-maintenance, no token reverse-engineering.

Modes (19)
----------
**Top Ads family**
- ``top_ads``               — Top Ads Dashboard. Filters: period, country, industry, objective, keyword, ad_format, ad_language, likes, sort_by.
- ``top_ads_spotlight``     — Industry-focused Spotlight ranks (6 industries).
- ``ad_analytics``          — Single ad full detail. Param: material_id.
- ``ad_keyframe``           — Per-second metric trace. Params: material_id, metric.
- ``ad_percentile``         — Percentile vs industry. Params: material_id, metric, period_type.
- ``ad_recommend``          — Similar-ads recommendations. Params: material_id, industry, country.

**Keyword Insights family**
- ``keyword_insights``      — Keyword leaderboard. Filters: period, region, industry, objective, keyword_type.
- ``keyword_videos``        — Videos using a keyword. Params: keyword, period.
- ``keyword_examples``      — Sentence templates with a keyword. Params: keyword, period.
- ``keyword_related``       — Related keywords / hashtags. Params: keyword, type, period.

**Creative pattern**
- ``creative_insights``     — Pattern label leaderboard. Filters: industry, period_type, date, order_field.

**Ecommerce**
- ``top_products``          — TikTok-Shop ranking. Filters: country, first_category, second_category, period_type, date, order_field.

**Trending**
- ``trending_hashtags``     — Hashtag chart. Filters: country, industry, period, new_to_top_100, search.
- ``hashtag_analytics``     — Hashtag deep dive. Params: hashtag_name, country, period.
- ``trending_songs``        — Popular songs. Filters: country, period, new_to_top_100, approved_for_business_use, search.
- ``trending_songs_breakout`` — Breakout songs. Filters: country, search.
- ``song_analytics``        — Single song deep dive. Params: clip_id, country, period.
- ``trending_creators``     — Top creators. Filters: country, audience_country, followers, sort_by, search.
- ``trending_videos``       — Trending videos. Filters: country, period, order_by.

The ``query`` argument behaves differently per mode:
- For ``top_ads`` and ``keyword_insights`` it's sent as a real backend
  ``keyword`` / ``search`` parameter when supported by the upstream.
- For trending endpoints with a backend ``search`` filter, it's used
  there too.
- Otherwise it falls back to a client-side substring filter on the
  human-readable fields.
"""

from __future__ import annotations

import logging
import time
import urllib.parse
from typing import Any, Callable, Optional, Sequence

from .base import BaseEngine, SearchResult
from . import _tiktok_cc_options as cc_options
from ..core import safe_goto

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Mode dispatch table
# ---------------------------------------------------------------------------
#
# Each mode has:
#   page_url:    URL template the user-facing page sits on; navigating
#                here causes the SPA to fire the API call we want to
#                intercept.
#   api_marker:  substring that must appear in the response URL for us
#                to keep the body. We pick it specific enough that we
#                don't grab unrelated requests fired by the same page.
#   data_keys:   ordered tuple of dict keys to walk inside the captured
#                ``response["data"]`` to find the array of rows. The
#                first matching key wins. (Use a tuple of one for "just
#                this key", or multiple to express alternatives.)
#   parse_row:   per-mode SearchResult builder.
#
# Some modes return a single object (e.g. ``ad_analytics``) rather than
# a list; for those we synthesise a one-element list so the caller's
# pagination loop is uniform.

_HOST_BUSINESS = "https://ads.tiktok.com/business/creativecenter"


# Periods accepted by most "popular_trend" + "topads" + "keyword" pages.
_VALID_PERIODS = {7, 30, 120, 180, 365, 1095}


# ---------------------------------------------------------------------------
# URL template + API marker per mode
# ---------------------------------------------------------------------------

_MODES: dict[str, dict[str, Any]] = {
    # --------------- Top Ads family ---------------
    "top_ads": {
        "api_marker": "/creative_radar_api/v1/top_ads/v2/list",
        "data_keys": ("materials",),
        "page_url": (
            f"{_HOST_BUSINESS}/inspiration/topads/pc/en"
            "?period={period}&region={country}"
        ),
    },
    "top_ads_spotlight": {
        "api_marker": "/creative_radar_api/v1/education/top_ads/list",
        "data_keys": ("materials", "list"),
        "page_url": f"{_HOST_BUSINESS}/tiktok-topads-spotlight/pc/en",
    },
    "ad_analytics": {
        "api_marker": "/creative_radar_api/v1/top_ads/v2/detail",
        "data_keys": ("",),  # entire data dict is the single row
        "page_url": (
            f"{_HOST_BUSINESS}/topads/{{material_id}}/pc/en"
            "?countryCode={country}&from=001110&period={period}"
        ),
    },
    "ad_keyframe": {
        "api_marker": "/creative_radar_api/v1/top_ads/keyframe",
        "data_keys": ("",),
        "page_url": (
            f"{_HOST_BUSINESS}/topads/{{material_id}}/pc/en"
            "?countryCode={country}&from=001110&period={period}"
        ),
    },
    "ad_percentile": {
        "api_marker": "/creative_radar_api/v1/top_ads/percentile",
        "data_keys": ("",),
        "page_url": (
            f"{_HOST_BUSINESS}/topads/{{material_id}}/pc/en"
            "?countryCode={country}&from=001110&period={period}"
        ),
    },
    "ad_recommend": {
        "api_marker": "/creative_radar_api/v1/top_ads/recommend",
        "data_keys": ("materials", "list"),
        "page_url": (
            f"{_HOST_BUSINESS}/topads/{{material_id}}/pc/en"
            "?countryCode={country}&from=001110&period={period}"
        ),
    },

    # --------------- Keyword Insights family ---------------
    "keyword_insights": {
        "api_marker": "/keyword/list",
        "data_keys": ("keyword_list", "list"),
        "page_url": f"{_HOST_BUSINESS}/keyword-insights/pc/en",
    },
    "keyword_videos": {
        "api_marker": "/keyword/related_video",
        "data_keys": ("video_list",),
        "page_url": f"{_HOST_BUSINESS}/tiktok-keyword/{{keyword_url}}/pc/en",
    },
    "keyword_examples": {
        "api_marker": "/keyword/sentence",
        "data_keys": ("sentence_list",),
        "page_url": f"{_HOST_BUSINESS}/tiktok-keyword/{{keyword_url}}/pc/en",
    },
    "keyword_related": {
        "api_marker": "/keyword/related_keyword",
        "data_keys": ("list",),
        "page_url": f"{_HOST_BUSINESS}/tiktok-keyword/{{keyword_url}}/pc/en",
    },

    # --------------- Creative pattern ---------------
    "creative_insights": {
        "api_marker": "/creative_insights/v2/list",
        "data_keys": ("list",),
        "page_url": f"{_HOST_BUSINESS}/creative-pattern/pc/en",
    },

    # --------------- Ecommerce ---------------
    "top_products": {
        "api_marker": "/top_products/list",
        "data_keys": ("list",),
        "page_url": f"{_HOST_BUSINESS}/top-products/pc/en",
    },

    # --------------- Trending ---------------
    "trending_hashtags": {
        "api_marker": "/popular_trend/hashtag/list",
        "data_keys": ("list",),
        "page_url": (
            f"{_HOST_BUSINESS}/inspiration/popular/hashtag/pc/en"
            "?period={period}&region={country}"
        ),
    },
    "hashtag_analytics": {
        "api_marker": "/popular_trend/hashtag/detail",
        "data_keys": ("",),
        "page_url": (
            f"{_HOST_BUSINESS}/hashtag/{{hashtag_name}}/pc/en"
            "?period={period}&country={country}"
        ),
    },
    "trending_songs": {
        "api_marker": "/popular_trend/song/list",
        "data_keys": ("sound_list", "list"),
        "page_url": (
            f"{_HOST_BUSINESS}/inspiration/popular/music/pc/en"
            "?period={period}&region={country}"
        ),
    },
    "trending_songs_breakout": {
        "api_marker": "/popular_trend/song/list",
        "data_keys": ("sound_list", "list"),
        "page_url": (
            f"{_HOST_BUSINESS}/inspiration/popular/music/pc/en"
            "?period={period}&region={country}&rank_type=breakout"
        ),
    },
    "song_analytics": {
        "api_marker": "/popular_trend/song/detail",
        "data_keys": ("",),
        "page_url": (
            f"{_HOST_BUSINESS}/song/{{clip_id}}/pc/en"
            "?period={period}&country={country}"
        ),
    },
    "trending_creators": {
        "api_marker": "/popular_trend/creator/list",
        "data_keys": ("creators", "list"),
        "page_url": (
            f"{_HOST_BUSINESS}/inspiration/popular/creator/pc/en"
            "?period={period}&region={country}"
        ),
    },
    "trending_videos": {
        "api_marker": "/popular_trend/video/list",
        "data_keys": ("videos", "list"),
        "page_url": (
            f"{_HOST_BUSINESS}/inspiration/popular/pc/en"
            "?period={period}&region={country}"
        ),
    },
}


def list_modes() -> list[str]:
    """Return all 19 supported modes."""
    return sorted(_MODES.keys())


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class TikTokCreativeCenterEngine(BaseEngine):
    """TikTok Creative Center adapter — Top Ads + Keywords + Trending + Products."""

    name = "tiktok_creative_center"
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
        mode: str = "top_ads",
        country_code: str = "US",
        country: Optional[str] = None,    # alias for country_code
        period: int = 30,
        industry: Optional[Sequence[str] | str] = None,
        objective: Optional[Sequence[str] | str] = None,
        order_by: Optional[str] = None,
        ad_format: Optional[str] = None,
        ad_language: Optional[str] = None,
        likes: Optional[str] = None,
        material_id: Optional[str] = None,
        metric: Optional[str] = None,
        period_type: Optional[str] = None,
        keyword: Optional[str] = None,
        keyword_type: Optional[str] = None,
        related_type: Optional[str] = None,
        clip_id: Optional[str] = None,
        hashtag_name: Optional[str] = None,
        first_category: Optional[Sequence[str] | str] = None,
        second_category: Optional[Sequence[str] | str] = None,
        date: Optional[str] = None,
        order_field: Optional[str] = None,
        order_type: Optional[str] = None,
        new_to_top_100: Optional[bool] = None,
        approved_for_business_use: Optional[bool] = None,
        followers: Optional[str] = None,
        audience_country: Optional[str] = None,
        page: int = 1,
        page_size: Optional[int] = None,
        wait_seconds: float = 25.0,
    ) -> list[SearchResult]:
        """Browse the Creative Center.

        :param query:        Optional keyword. Sent as a real backend filter
                             when the mode supports it; otherwise applied
                             as a client-side substring match on
                             human-readable fields.
        :param limit:        Cap on returned rows.
        :param mode:         One of the 19 modes. See :func:`list_modes`.
        :param country_code: ISO-3166 region code (e.g. ``US`` / ``GB`` /
                             ``JP`` / ``BR``).
        :param period:       Lookback window in days. Validated against
                             the per-page list of allowed values.
        :param industry:     Industry id(s). For ``top_ads`` &
                             ``top_ads_spotlight`` this filters the
                             result list. Also for ``keyword_insights`` /
                             ``ad_recommend`` / ``creative_insights`` /
                             ``trending_hashtags`` (where supported).
        :param objective:    Campaign objective id(s) for ``top_ads`` /
                             ``keyword_insights``.
        :param material_id:  Required for ``ad_*`` detail modes.
        :param keyword:      Required for ``keyword_videos`` /
                             ``keyword_examples`` / ``keyword_related``.
        :param hashtag_name: Required for ``hashtag_analytics``.
        :param clip_id:      Required for ``song_analytics``.
        """
        m = (mode or "top_ads").lower()
        if m not in _MODES:
            raise ValueError(
                f"unknown mode {m!r}; choose one of {list_modes()}"
            )

        country_code = (country or country_code or "US").upper()
        if period not in _VALID_PERIODS:
            log.warning("[ttcc] period=%s not in %s, defaulting to 30",
                        period, sorted(_VALID_PERIODS))
            period = 30

        # Mode-specific required-param check.
        if m in ("ad_analytics", "ad_keyframe", "ad_percentile", "ad_recommend"):
            if not material_id:
                raise ValueError(f"mode={m!r} requires material_id=...")
        if m in ("keyword_videos", "keyword_examples", "keyword_related"):
            kw = keyword or query
            if not kw:
                raise ValueError(f"mode={m!r} requires keyword=... (or query)")
            keyword = kw
        if m == "hashtag_analytics" and not hashtag_name:
            raise ValueError("mode=hashtag_analytics requires hashtag_name=...")
        if m == "song_analytics" and not clip_id:
            raise ValueError("mode=song_analytics requires clip_id=...")

        # Industry id validation (only when explicitly provided).
        if industry is not None and m in ("top_ads", "top_ads_spotlight",
                                          "keyword_insights", "creative_insights",
                                          "trending_hashtags"):
            self._validate_industry(industry, m)

        cfg = _MODES[m]
        url = self._build_url(cfg["page_url"], {
            "period": period,
            "country": country_code,
            "material_id": material_id or "",
            "keyword_url": urllib.parse.quote(keyword or "", safe=""),
            "hashtag_name": urllib.parse.quote(hashtag_name or "", safe=""),
            "clip_id": clip_id or "",
        })

        # Append mode-specific query string parameters where supported.
        url = self._append_qs(url, m, {
            "industry": industry,
            "objective": objective,
            "order_by": order_by,
            "order_field": order_field,
            "order_type": order_type,
            "ad_format": ad_format,
            "ad_language": ad_language,
            "likes": likes,
            "metric": metric,
            "period_type": period_type,
            "keyword": keyword if m == "keyword_insights" else None,
            "keyword_type": keyword_type,
            "type": related_type if m == "keyword_related" else None,
            "first_category": first_category,
            "second_category": second_category,
            "date": date,
            "new_to_top_100": new_to_top_100,
            "approved_for_business_use": approved_for_business_use,
            "followers": followers,
            "audience_country": audience_country,
            "search": (
                query if m in ("trending_hashtags", "trending_songs",
                                "trending_songs_breakout", "trending_creators")
                else None
            ),
            "page": page,
            "limit": page_size,
        })

        self.last_status = {
            "mode": m, "period": period, "country": country_code,
            "url": url, "query": query,
        }

        captured: dict[str, Any] = {"body": None, "url": None, "err": None}
        marker = cfg["api_marker"]

        def _on_response(resp):
            try:
                if marker in resp.url and resp.status == 200 and captured["body"] is None:
                    captured["body"] = resp.json()
                    captured["url"] = resp.url
            except Exception as e:
                captured["err"] = str(e)

        self.page.on("response", _on_response)
        try:
            log.info("[ttcc/%s] navigating %s", m, url)
            if not safe_goto(self.page, url, timeout=30000, retries=1):
                self.last_status["error"] = "navigation failed"
                return []

            deadline = time.time() + wait_seconds
            while time.time() < deadline and captured["body"] is None:
                self.page.wait_for_timeout(500)
        finally:
            try:
                self.page.remove_listener("response", _on_response)
            except Exception:
                pass

        if captured["body"] is None:
            self.last_status["error"] = captured.get("err") or "no response captured"
            log.warning("[ttcc/%s] no API response captured (%s)",
                        m, self.last_status["error"])
            return []

        api_code = captured["body"].get("code")
        if api_code is not None and api_code != 0:
            self.last_status["error"] = (
                f"upstream returned code={api_code} "
                f"({captured['body'].get('msg')!r})"
            )
            log.warning("[ttcc/%s] %s", m, self.last_status["error"])
            return []

        data = captured["body"].get("data") or {}
        rows = self._extract_rows(data, cfg["data_keys"])
        self.last_status["raw_count"] = len(rows)

        # Client-side query filter (only if upstream didn't apply it).
        if query and m not in ("top_ads", "keyword_insights",
                                "trending_hashtags", "trending_songs",
                                "trending_songs_breakout", "trending_creators"):
            ql = query.strip().lower()
            rows = [r for r in rows if self._row_matches(r, ql, m)]

        rows = rows[:limit]
        return [self._row_to_result(r, m, country_code, period=period) for r in rows]

    # ------------------------------------------------------------------ helpers

    def _build_url(self, template: str, params: dict[str, Any]) -> str:
        # ``str.format_map`` with a defaultdict-like wrapper would be
        # nicer, but our templates only ever reference these specific
        # keys so a plain ``format`` is sufficient.
        return template.format(**params)

    def _append_qs(self, url: str, mode: str, params: dict[str, Any]) -> str:
        qs: list[tuple[str, str]] = []
        for key, val in params.items():
            if val is None or val == "":
                continue
            if isinstance(val, bool):
                qs.append((key, "true" if val else "false"))
            elif isinstance(val, (list, tuple, set)):
                if not val:
                    continue
                qs.append((key, ",".join(str(x) for x in val)))
            else:
                qs.append((key, str(val)))
        if not qs:
            return url
        sep = "&" if "?" in url else "?"
        return url + sep + urllib.parse.urlencode(qs, safe=",")

    def _validate_industry(
        self, industry: Sequence[str] | str, mode: str
    ) -> None:
        opt_name = {
            "top_ads":            "dashboard_industry",
            "top_ads_spotlight":  "spotlight_industry",
            "keyword_insights":   "keyword_industry",
            "creative_insights":  "creative_insights_industry",
            "trending_hashtags":  "hashtags_industry",
        }.get(mode)
        if not opt_name:
            return
        valid = cc_options.valid_ids(opt_name)
        if not valid:
            return
        ids = [str(industry)] if isinstance(industry, str) else [str(x) for x in industry]
        ids = [i for i in [s.strip() for s in ",".join(ids).split(",")] if i]
        unknown = [i for i in ids if i not in valid]
        if unknown:
            log.warning(
                "[ttcc/%s] unknown industry id(s) %s — valid ids come from "
                "agent_search/engines/_tiktok_cc_options/%s.json",
                mode, unknown, opt_name,
            )

    @staticmethod
    def _extract_rows(data: dict, keys: tuple[str, ...]) -> list[dict]:
        for key in keys:
            if key == "":
                # Whole data dict is a single row.
                return [data] if data else []
            v = data.get(key)
            if isinstance(v, list):
                # If list of strings (e.g. keyword_videos -> ["7556...", ...]),
                # wrap each into a stub dict so downstream code can index it.
                if v and isinstance(v[0], str):
                    return [{"_string_value": s} for s in v]
                return v
        return []

    def _row_matches(self, row: dict, ql: str, mode: str) -> bool:
        haystack: list[str] = []
        for k in ("ad_title", "brand_name", "industry_key", "title", "author",
                  "name", "hashtag_name", "nick_name", "user_name", "keyword",
                  "sentence", "label_info", "url_title"):
            v = row.get(k)
            if isinstance(v, str):
                haystack.append(v)
            elif isinstance(v, dict):
                haystack.append(str(v.get("value") or v.get("label") or ""))
        return any(ql in s.lower() for s in haystack)

    # ── per-mode row → SearchResult ──────────────────────────────────

    def _row_to_result(self, row: dict, mode: str, country_code: str,
                       *, period: int) -> SearchResult:
        if mode in ("top_ads", "top_ads_spotlight", "ad_recommend",
                    "ad_analytics"):
            return self._row_top_ad(row, country_code)
        if mode == "ad_keyframe":
            return self._row_keyframe(row, country_code)
        if mode == "ad_percentile":
            return self._row_percentile(row, country_code)
        if mode == "keyword_insights":
            return self._row_keyword_insights(row, country_code)
        if mode == "keyword_videos":
            return self._row_keyword_video(row)
        if mode == "keyword_examples":
            return self._row_keyword_example(row)
        if mode == "keyword_related":
            return self._row_keyword_related(row)
        if mode == "creative_insights":
            return self._row_creative_insights(row)
        if mode == "top_products":
            return self._row_top_product(row, country_code)
        if mode == "trending_hashtags":
            return self._row_trending_hashtag(row)
        if mode == "hashtag_analytics":
            return self._row_hashtag_analytics(row)
        if mode in ("trending_songs", "trending_songs_breakout"):
            return self._row_trending_song(row)
        if mode == "song_analytics":
            return self._row_song_analytics(row)
        if mode == "trending_creators":
            return self._row_trending_creator(row)
        if mode == "trending_videos":
            return self._row_trending_video(row)

        # Fallback — shouldn't be reachable.
        r = SearchResult(title=str(row)[:80], url="", snippet="")
        r.__dict__["raw"] = row
        return r

    # --- Top Ads ---
    def _row_top_ad(self, row: dict, country: str) -> SearchResult:
        ad_id = str(row.get("id") or "")
        title = str(row.get("ad_title") or row.get("brand_name") or ad_id)
        brand = str(row.get("brand_name") or "")
        url = (
            f"{_HOST_BUSINESS}/topads/{ad_id}/pc/en?countryCode={country}&from=001110"
            if ad_id else ""
        )
        snippet_parts: list[str] = []
        if brand:
            snippet_parts.append(f"by {brand}")
        for label, key, suffix in [("CTR", "ctr", "%"), ("likes", "like", ""),
                                    ("CVR", "cvr", "%"),
                                    ("play_6s_rate", "play_6s_rate", "%")]:
            if row.get(key) is not None:
                snippet_parts.append(f"{label}={row[key]}{suffix}")
        objective = str(row.get("objective_key") or "").replace("campaign_objective_", "")
        if objective:
            snippet_parts.append(f"goal={objective}")
        video_info = row.get("video_info") or {}
        video_urls = video_info.get("video_url") or {}
        r = SearchResult(title=title[:200], url=url, snippet=" · ".join(snippet_parts))
        r.__dict__.update({
            "ad_id": ad_id,
            "brand_name": brand,
            "industry_key": row.get("industry_key") or "",
            "objective_key": row.get("objective_key") or "",
            "objectives": row.get("objectives") or [],
            "ctr": row.get("ctr"),
            "likes": row.get("like"),
            "cost_index": row.get("cost"),
            "cvr": row.get("cvr"),
            "play_6s_rate": row.get("play_6s_rate"),
            "comment": row.get("comment"),
            "share": row.get("share"),
            "is_search": row.get("is_search"),
            "duration_s": video_info.get("duration"),
            "cover_image_url": video_info.get("cover") or "",
            "video_url": (video_urls.get("720p") or video_urls.get("540p") or
                          video_urls.get("480p") or video_urls.get("1080p") or
                          video_urls.get("360p") or ""),
            "video_urls": dict(video_urls),
            "width": video_info.get("width"),
            "height": video_info.get("height"),
            "vid": video_info.get("vid") or "",
            # ad_analytics extras
            "country_code": row.get("country_code"),
            "landing_page": row.get("landing_page") or "",
            "highlight_text": row.get("highlight_text") or "",
            "keyword_list": row.get("keyword_list"),
            "pattern_label": row.get("pattern_label") or [],
            "source": row.get("source"),
            "voice_over": row.get("voice_over"),
        })
        return r

    def _row_keyframe(self, row: dict, country: str) -> SearchResult:
        analysis = row.get("analysis") or []
        duration = row.get("duration") or 0
        title = f"keyframe analysis (d={duration}s)"
        r = SearchResult(title=title, url="", snippet=f"{len(analysis)} data points")
        r.__dict__.update({
            "analysis": analysis,
            "duration": duration,
            "highlight": row.get("highlight") or [],
        })
        return r

    def _row_percentile(self, row: dict, country: str) -> SearchResult:
        # row is the data dict itself, e.g. {"ctr_percentile": 0.48}
        snippet = " · ".join(f"{k}={v}" for k, v in row.items())
        r = SearchResult(title=f"percentile snapshot", url="", snippet=snippet)
        r.__dict__.update(dict(row))
        return r

    # --- Keyword Insights ---
    def _row_keyword_insights(self, row: dict, country: str) -> SearchResult:
        kw = str(row.get("keyword") or "")
        snippet_parts = []
        for k in ("ctr", "cvr", "cpa", "post", "post_change", "impression",
                  "like", "share", "comment", "play_six_rate"):
            if row.get(k) is not None:
                snippet_parts.append(f"{k}={row[k]}")
        url = (
            f"{_HOST_BUSINESS}/tiktok-keyword/{urllib.parse.quote(kw, safe='')}/pc/en"
            if kw else ""
        )
        r = SearchResult(title=f"#{kw}" if kw else "(keyword)",
                         url=url, snippet=" · ".join(snippet_parts))
        r.__dict__.update({
            "keyword": kw,
            "ctr": row.get("ctr"), "cvr": row.get("cvr"), "cpa": row.get("cpa"),
            "cost": row.get("cost"), "impression": row.get("impression"),
            "like": row.get("like"), "share": row.get("share"),
            "comment": row.get("comment"), "post": row.get("post"),
            "post_change": row.get("post_change"),
            "play_six_rate": row.get("play_six_rate"),
            "video_list": row.get("video_list") or [],
        })
        return r

    def _row_keyword_video(self, row: dict) -> SearchResult:
        # Upstream returns a list of bare ID strings; we wrap each in
        # ``{"_string_value": "..."}``.
        vid = str(row.get("_string_value") or row.get("item_id") or "")
        url = f"https://www.tiktok.com/video/{vid}" if vid else ""
        r = SearchResult(title=vid or "(video)", url=url, snippet="")
        r.__dict__["video_id"] = vid
        return r

    def _row_keyword_example(self, row: dict) -> SearchResult:
        sentence = str(row.get("sentence") or "")
        snippet = " · ".join(filter(None, [
            f"CTR={row['ctr']}" if row.get("ctr") is not None else "",
            f"CVR={row['cvr']}" if row.get("cvr") is not None else "",
            f"type={row.get('use_type', '')}",
        ]))
        r = SearchResult(title=sentence[:200], url="", snippet=snippet)
        r.__dict__.update({
            "sentence": sentence,
            "ctr": row.get("ctr"), "cvr": row.get("cvr"),
            "covers": row.get("covers") or [],
            "use_type": row.get("use_type") or "",
        })
        return r

    def _row_keyword_related(self, row: dict) -> SearchResult:
        name = str(row.get("name") or "")
        score = row.get("score")
        r = SearchResult(title=name, url="",
                         snippet=f"score={score}" if score is not None else "")
        r.__dict__.update({"name": name, "score": score})
        return r

    # --- Creative Insights ---
    def _row_creative_insights(self, row: dict) -> SearchResult:
        label_info = row.get("label_info") or {}
        title = str(label_info.get("value") or label_info.get("label") or "")
        snippet_parts = []
        for k in ("ctr", "high_spending_rate", "high_spending_rate_change",
                  "play_over_rate"):
            if row.get(k) is not None:
                snippet_parts.append(f"{k}={row[k]}")
        r = SearchResult(title=title, url="", snippet=" · ".join(snippet_parts))
        r.__dict__.update({
            "label_id": row.get("id"),
            "label_value": label_info.get("value"),
            "label_key": label_info.get("label"),
            "ctr": row.get("ctr"),
            "high_spending_rate": row.get("high_spending_rate"),
            "high_spending_rate_change": row.get("high_spending_rate_change"),
            "play_over_rate": row.get("play_over_rate"),
        })
        return r

    # --- Top Products ---
    def _row_top_product(self, row: dict, country: str) -> SearchResult:
        first = row.get("first_ecom_category") or {}
        second = row.get("second_ecom_category") or {}
        third = row.get("third_ecom_category") or {}
        title = str(third.get("value") or second.get("value") or first.get("value") or "")
        snippet_parts = []
        for k in ("ctr", "cvr", "cpa", "cost", "impression", "post",
                  "post_change", "like", "share", "comment", "play_six_rate"):
            if row.get(k) is not None:
                snippet_parts.append(f"{k}={row[k]}")
        r = SearchResult(title=title, url=row.get("cover_url") or "",
                         snippet=" · ".join(snippet_parts))
        r.__dict__.update({
            "category_l1": first.get("value"),
            "category_l1_id": first.get("id"),
            "category_l2": second.get("value"),
            "category_l2_id": second.get("id"),
            "category_l3": third.get("value"),
            "category_l3_id": third.get("id"),
            "url_title": row.get("url_title") or "",
            "ecom_type": row.get("ecom_type") or "",
            "ctr": row.get("ctr"), "cvr": row.get("cvr"),
            "cpa": row.get("cpa"), "cost": row.get("cost"),
            "impression": row.get("impression"), "post": row.get("post"),
            "post_change": row.get("post_change"),
            "like": row.get("like"), "share": row.get("share"),
            "comment": row.get("comment"),
            "play_six_rate": row.get("play_six_rate"),
        })
        return r

    # --- Trending Hashtags / Hashtag Analytics ---
    def _row_trending_hashtag(self, row: dict) -> SearchResult:
        tag = str(row.get("hashtag_name") or row.get("name") or "")
        country_info = row.get("country_info") or {}
        snippet_parts = []
        if row.get("publish_cnt") is not None:
            snippet_parts.append(f"posts={row['publish_cnt']}")
        if row.get("video_views") is not None:
            snippet_parts.append(f"views={row['video_views']}")
        if row.get("rank") is not None:
            snippet_parts.append(f"rank={row['rank']}")
        r = SearchResult(
            title=f"#{tag}" if tag else "",
            url=f"https://www.tiktok.com/tag/{tag}" if tag else "",
            snippet=" · ".join(snippet_parts),
        )
        r.__dict__.update({
            "hashtag": tag,
            "hashtag_id": row.get("hashtag_id"),
            "publish_cnt": row.get("publish_cnt"),
            "video_views": row.get("video_views"),
            "rank": row.get("rank"),
            "rank_diff_type": row.get("rank_diff_type"),
            "is_promoted": row.get("is_promoted"),
            "trend": row.get("trend") or [],
            "country_label": country_info.get("label") or "",
        })
        return r

    def _row_hashtag_analytics(self, row: dict) -> SearchResult:
        # row is the full data dict; ``info`` holds the meat.
        info = row.get("info") or row
        tag = str(info.get("hashtag_name") or "")
        snippet_parts = []
        for k in ("publish_cnt", "video_views", "publish_cnt_all",
                  "video_views_all"):
            if info.get(k) is not None:
                snippet_parts.append(f"{k}={info[k]}")
        r = SearchResult(
            title=f"#{tag}" if tag else "",
            url=info.get("video_url") or (
                f"https://www.tiktok.com/tag/{tag}" if tag else ""
            ),
            snippet=" · ".join(snippet_parts),
        )
        r.__dict__.update({
            "hashtag": tag,
            "hashtag_id": info.get("hashtag_id"),
            "description": info.get("description") or "",
            "publish_cnt": info.get("publish_cnt"),
            "video_views": info.get("video_views"),
            "publish_cnt_all": info.get("publish_cnt_all"),
            "video_views_all": info.get("video_views_all"),
            "trend": info.get("trend") or [],
            "longevity": info.get("longevity"),
            "audience_ages": info.get("audience_ages") or [],
            "audience_interests": info.get("audience_interests") or [],
            "audience_countries": info.get("audience_countries") or [],
            "related_hashtags": info.get("related_hashtags") or [],
            "related_items": info.get("related_items") or [],
        })
        return r

    # --- Trending Songs / Song Analytics ---
    def _row_trending_song(self, row: dict) -> SearchResult:
        clip_id = str(row.get("clip_id") or row.get("id") or "")
        title = str(row.get("title") or "(untitled)")
        author = str(row.get("author") or "")
        r = SearchResult(
            title=title,
            url=row.get("link") or "",
            snippet=" · ".join(filter(None, [
                f"by {author}" if author else "",
                f"rank={row['rank']}" if row.get("rank") is not None else "",
                "[promoted]" if row.get("promoted") else "",
            ])),
        )
        r.__dict__.update({
            "clip_id": clip_id,
            "song_id": row.get("song_id"),
            "title": title,
            "author": author,
            "duration_s": row.get("duration"),
            "rank": row.get("rank"),
            "rank_diff": row.get("rank_diff"),
            "rank_diff_type": row.get("rank_diff_type"),
            "country_code": row.get("country_code") or "",
            "cover_image_url": row.get("cover") or "",
            "url_title": row.get("url_title") or "",
            "if_cml": row.get("if_cml"),
            "promoted": row.get("promoted"),
            "trend": row.get("trend") or [],
            "related_items": row.get("related_items") or [],
        })
        return r

    def _row_song_analytics(self, row: dict) -> SearchResult:
        sound = row.get("sound") or row
        clip_id = str(sound.get("clip_id") or "")
        title = str(sound.get("title") or "")
        author = str(sound.get("author") or "")
        r = SearchResult(title=title, url=sound.get("link") or "",
                         snippet=f"by {author}" if author else "")
        r.__dict__.update({
            "clip_id": clip_id,
            "song_id": sound.get("song_id"),
            "title": title,
            "author": author,
            "duration_s": sound.get("duration"),
            "country_code": sound.get("country_code"),
            "cover_image_url": sound.get("cover") or "",
            "url_title": sound.get("url_title") or "",
            "longevity": sound.get("longevity"),
            "audience_ages": sound.get("audience_ages") or [],
            "audience_interests": sound.get("audience_interests") or [],
            "audience_countries": sound.get("audience_countries") or [],
            "related_items": sound.get("related_items") or [],
            "trend": sound.get("trend") or [],
        })
        return r

    # --- Creators / Videos ---
    def _row_trending_creator(self, row: dict) -> SearchResult:
        nick = str(row.get("nick_name") or row.get("name") or "")
        username = str(row.get("user_name") or "")
        items = row.get("items") or []
        snippet_parts = []
        if row.get("follower_cnt") is not None:
            snippet_parts.append(f"followers={row['follower_cnt']}")
        if row.get("liked_cnt") is not None:
            snippet_parts.append(f"likes={row['liked_cnt']}")
        if items:
            snippet_parts.append(f"recent_items={len(items)}")
        r = SearchResult(
            title=nick or username,
            url=row.get("tt_link") or (
                f"https://www.tiktok.com/@{username}" if username else ""
            ),
            snippet=" · ".join(snippet_parts),
        )
        r.__dict__.update({
            "tcm_id": row.get("tcm_id"),
            "user_id": row.get("user_id"),
            "username": username,
            "nick_name": nick,
            "follower_cnt": row.get("follower_cnt"),
            "liked_cnt": row.get("liked_cnt"),
            "country_code": row.get("country_code") or "",
            "avatar_url": row.get("avatar_url") or "",
            "tt_link": row.get("tt_link") or "",
            "tcm_link": row.get("tcm_link") or "",
            "items": items,
        })
        return r

    def _row_trending_video(self, row: dict) -> SearchResult:
        vid = str(row.get("item_id") or row.get("id") or "")
        title = str(row.get("title") or vid)
        r = SearchResult(
            title=title[:200],
            url=row.get("item_url") or "",
            snippet=" · ".join(filter(None, [
                row.get("region") or "",
                f"duration={row['duration']}s" if row.get("duration") else "",
            ])),
        )
        r.__dict__.update({
            "video_id": vid,
            "country_code": row.get("country_code") or "",
            "region": row.get("region") or "",
            "duration_s": row.get("duration"),
            "cover_image_url": row.get("cover") or "",
            "item_url": row.get("item_url") or "",
        })
        return r

    # ------------------------------------------------------------------ BaseEngine

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        return self.search(query, limit)
