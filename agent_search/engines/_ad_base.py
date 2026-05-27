"""Cross-platform Ad-record schema for downstream consumers.

The four ad engines (``meta_ad_library``, ``instagram_ad_library``,
``tiktok_ad_library``, ``tiktok_creative_center``, ``google_ad_transparency``)
each return :class:`SearchResult` objects with a rich set of platform-specific
fields. Downstream code that wants a uniform view across platforms can pass
each :class:`SearchResult` through :func:`to_ad_record` to obtain a flat
:class:`AdRecord` dict with a stable shape.

This is intentionally **lossy by design** — only the fields meaningful for
cross-platform analysis are mapped. The original :class:`SearchResult` is
preserved in ``AdRecord["raw"]`` so callers can still drill down to
platform-specific fields when needed.

This module is also the natural place to add a media downloader and
exporters (JSONL / CSV / SQLite) in future iterations.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class AdRecord:
    """Cross-platform unified schema for one ad creative.

    Field semantics:
        platform: ``meta`` / ``instagram`` / ``tiktok_cc`` / ``tiktok_lib`` /
                  ``google_atc``.
        ad_id:    Unique within the platform. For Meta this is
                  ``ad_archive_id``; for TikTok CC it's the material ``id``;
                  for Google ATC it's the ``creative_id``.
        advertiser_name / advertiser_id:
                  The page (Meta), brand (TikTok CC), or advertiser
                  (Google ATC) that ran the ad.
        country:  ISO-3166 alpha-2 of the primary delivery region.
        first_seen_iso / last_seen_iso:
                  ``YYYY-MM-DD`` strings; optional.
        days_running:
                  Best-effort integer; optional.
        media_urls:
                  List of CDN URLs for image / video creatives; the first
                  entry is the most representative.
        landing_url:
                  Where the ad sends users on click.
        copy_text:
                  Headline + body, concatenated. ``headline`` /
                  ``description`` / ``body`` separately preserved in
                  ``raw`` if needed.
        cta_text:
                  Standardized CTA (``LEARN_MORE``, ``SHOP_NOW``, ...).
        impressions_lower / impressions_upper:
                  Bounded estimates from the platform; political-only on
                  Meta, free for TikTok CC.
        spend_lower / spend_upper / currency:
                  Bounded spend estimates; political-only on Meta.
        score:
                  Optional engagement signal — CTR % from TikTok / Google
                  click-through rate / nothing for Meta.
        raw:      The original :class:`SearchResult` ``__dict__`` so no
                  data is lost.
    """

    platform: str
    ad_id: str
    advertiser_name: str = ""
    advertiser_id: str = ""
    country: str = ""
    first_seen_iso: str = ""
    last_seen_iso: str = ""
    days_running: Optional[int] = None
    is_active: Optional[bool] = None
    media_urls: list[str] = field(default_factory=list)
    landing_url: str = ""
    copy_text: str = ""
    cta_text: str = ""
    impressions_lower: Optional[int] = None
    impressions_upper: Optional[int] = None
    spend_lower: Optional[int] = None
    spend_upper: Optional[int] = None
    currency: str = ""
    score: Optional[float] = None
    raw: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _coerce_iso(value: Any) -> str:
    if not value:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float)):
        try:
            from datetime import datetime, timezone
            ts = float(value)
            # epoch seconds vs ms heuristic
            if ts > 1e12:
                ts /= 1000.0
            return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            return ""
    return str(value)


def to_ad_record(result: Any, *, platform: Optional[str] = None) -> AdRecord:
    """Convert any ad engine's :class:`SearchResult` into an :class:`AdRecord`.

    The platform is inferred from typical fields when not explicitly given:
        * ``ad_archive_id``       → Meta / Instagram
        * ``material_id`` / ``ad_id`` + ``brand_name`` → TikTok CC
        * ``ad_id`` + ``advertiser_name`` (no brand_name) → TikTok Library
        * ``advertiser_id`` + ``creative_id`` → Google ATC

    The conversion is a single pass over a small set of well-known field
    names; unknown fields land in ``raw``.
    """
    d = dict(result.__dict__) if not isinstance(result, dict) else dict(result)

    # Pick platform.
    p = platform or d.get("platform")
    if not p:
        if d.get("ad_archive_id"):
            # Only label as instagram when the engine itself flagged so;
            # publisher_platforms can include both facebook and instagram
            # for cross-posted ads, in which case we keep "meta".
            p = "meta"
        elif d.get("creative_id") and d.get("advertiser_id"):
            p = "google_atc"
        elif (d.get("result_type") in ("advertiser", "domain",
                                        "advertiser_by_domain")
              or (isinstance(d.get("advertiser_id"), str)
                  and d["advertiser_id"].startswith("AR"))):
            # Google ATC search_advertisers / domain results: an
            # advertiser_id starting with AR is the canonical
            # Google ATC marker, even when no creative_id is present.
            p = "google_atc"
        elif d.get("brand_name") and d.get("ad_id"):
            p = "tiktok_cc"
        elif d.get("ad_id") and d.get("advertiser_name"):
            p = "tiktok_lib"
        else:
            p = "unknown"

    # Pick ad_id.
    ad_id = (
        d.get("ad_archive_id")
        or d.get("creative_id")
        or d.get("ad_id")
        or ""
    )

    # Advertiser.
    advertiser_name = (
        d.get("advertiser_name")
        or d.get("brand_name")
        or d.get("page_name")
        or ""
    )
    advertiser_id = (
        d.get("advertiser_id")
        or d.get("page_id")
        or ""
    )

    # Dates.
    first_iso = (
        d.get("first_seen_iso")
        or _coerce_iso(d.get("first_seen_ms") or d.get("start_date") or d.get("first_shown"))
    )
    last_iso = (
        d.get("last_seen_iso")
        or _coerce_iso(d.get("last_seen_ms") or d.get("end_date") or d.get("last_shown"))
    )

    # Media URLs.
    media_urls: list[str] = []
    for key in ("video_url", "image_urls", "video_urls", "image_url",
                "cover_image_url", "preview_url"):
        v = d.get(key)
        if isinstance(v, list):
            media_urls.extend(s for s in v if isinstance(s, str) and s)
        elif isinstance(v, str) and v:
            media_urls.append(v)
        elif isinstance(v, dict):
            media_urls.extend(s for s in v.values() if isinstance(s, str) and s)
    # Dedup preserving order.
    seen = set()
    media_urls = [u for u in media_urls if not (u in seen or seen.add(u))]

    # Copy text.
    copy_parts = [
        d.get("title", ""),
        d.get("headline", ""),
        d.get("body_text", ""),
        d.get("description", ""),
        d.get("text_summary", ""),
        d.get("text", ""),
    ]
    copy_text = " · ".join(s for s in copy_parts if s).strip()

    # Landing URL.
    landing_url = (
        d.get("destination_url")
        or d.get("link_url")
        or d.get("landing_page")
        or ""
    )

    # CTA.
    cta_text = d.get("cta_text") or ""

    # Score (engagement signal).
    score: Optional[float] = None
    for key in ("ctr", "score"):
        v = d.get(key)
        if isinstance(v, (int, float)):
            score = float(v)
            break

    return AdRecord(
        platform=p,
        ad_id=str(ad_id),
        advertiser_name=str(advertiser_name),
        advertiser_id=str(advertiser_id),
        country=str(d.get("country") or d.get("country_code") or d.get("region") or ""),
        first_seen_iso=first_iso,
        last_seen_iso=last_iso,
        days_running=d.get("days_running"),
        is_active=d.get("is_active"),
        media_urls=media_urls,
        landing_url=landing_url,
        copy_text=copy_text,
        cta_text=cta_text,
        impressions_lower=d.get("impressions_lower"),
        impressions_upper=d.get("impressions_upper"),
        spend_lower=d.get("spend_lower"),
        spend_upper=d.get("spend_upper"),
        currency=d.get("currency") or "",
        score=score,
        raw=d,
    )
