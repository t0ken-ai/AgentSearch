"""Instagram Ad Library engine — thin wrapper over Meta Ad Library.

Background
----------
Instagram ads (Reels, Stories, Feed) are part of the Meta ecosystem and
are exposed by the same ``facebook.com/ads/library`` portal. There is
**no separate Instagram-only library** — to inspect a brand's IG ads
you query the Meta Ad Library and filter by ``publisher_platforms=instagram``.

This engine is therefore a convenience wrapper around
:class:`agent_search.engines.meta_ad_library.MetaAdLibraryEngine` that
defaults the platform filter to Instagram and adds a few IG-specific
helpers:

* ``placement="reels"`` / ``"stories"`` / ``"feed"`` / ``"all"`` —
  upstream doesn't expose a clean placement filter, so we ask for
  ``media_type="VIDEO"`` for Reels/Stories and use the platform filter
  to keep IG-only.
* The returned ``SearchResult`` objects are identical in shape to
  Meta Ad Library output, so downstream consumers can treat both
  engines uniformly.

Modes
-----
``mode="keyword"`` (default)
    Same as Meta Ad Library keyword search but locked to IG.

``mode="advertiser"`` / ``mode="page_url"``
    Same as Meta. ``query`` is a page name / ``page_id:<id>`` /
    Facebook URL.

Returned fields
---------------
Identical to Meta Ad Library — see :mod:`meta_ad_library` for the full
list. The convenience field ``platform="instagram"`` is added.
"""

from __future__ import annotations

from typing import Any, Optional, Sequence

from .base import SearchResult
from .meta_ad_library import MetaAdLibraryEngine


_PLACEMENT_TO_MEDIA_TYPE = {
    "all":     "ALL",
    "reels":   "VIDEO",
    "stories": "VIDEO",
    "feed":    "ALL",
}


class InstagramAdLibraryEngine(MetaAdLibraryEngine):
    """Instagram-only view of the Meta Ad Library."""

    name = "instagram_ad_library"
    max_retries = 2

    def search(  # type: ignore[override]
        self,
        query: str = "",
        limit: int = 20,
        *,
        mode: str = "keyword",
        country: str = "US",
        active_only: Optional[bool] = None,
        status: Optional[str] = None,
        ad_type: str = "all",
        # IG-specific shortcut. Maps to publisher_platforms+media_type.
        placement: str = "all",
        # Allow overriding to query other platforms via the same engine.
        publisher_platforms: Optional[Sequence[str]] = None,
        # All other kwargs pass through to the parent.
        **kwargs: Any,
    ) -> list[SearchResult]:
        """Search Instagram ads.

        :param placement: ``all`` / ``reels`` / ``stories`` / ``feed``.
        :param publisher_platforms: Override platform filter. Default is
            ``["instagram"]``. Pass ``["instagram", "facebook"]`` to
            include cross-posted ads, or any custom subset.
        :param kwargs: All other Meta Ad Library kwargs are forwarded —
            including ``min_impressions / max_impressions / min_spend /
            max_spend / start_date / end_date / languages / has_video /
            has_image / extract_rsoc / sort_by / search_type / exact /
            page_ids``.
        """
        p = (placement or "all").lower()
        if p not in _PLACEMENT_TO_MEDIA_TYPE:
            raise ValueError(
                f"unknown placement {placement!r}; choose one of "
                f"{sorted(_PLACEMENT_TO_MEDIA_TYPE)}"
            )
        # If caller hasn't overridden platforms, lock to IG.
        if publisher_platforms is None:
            publisher_platforms = ["instagram"]

        # If caller hasn't overridden media_type, derive it from placement.
        if "media_type" not in kwargs:
            kwargs["media_type"] = _PLACEMENT_TO_MEDIA_TYPE[p]

        results = super().search(
            query=query,
            limit=limit,
            mode=mode,
            country=country,
            active_only=active_only,
            status=status,
            ad_type=ad_type,
            publisher_platforms=publisher_platforms,
            **kwargs,
        )
        # Tag every result with platform=instagram for downstream use.
        for r in results:
            r.__dict__.setdefault("platform", "instagram")
            r.__dict__.setdefault("placement", p)
        # Update last_status with IG context.
        self.last_status["platform"] = "instagram"
        self.last_status["placement"] = p
        return results
