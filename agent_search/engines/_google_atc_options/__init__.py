"""Google Ads Transparency Center option helpers.

Exposes the ISO-3166 → Google internal ``region_num`` mapping needed for
``SearchCreatives`` RPCs. The region dictionary is sourced from
<https://github.com/block-town/google-ads-transparency-mcp> (MIT-licensed),
which collected the values directly from the ATC frontend.

Public API:
    REGIONS      → dict[str, dict] (ISO code → {"1": region_num, "Region": label})
    region_num(code) → int | None
    valid_codes() → set[str]
"""

from __future__ import annotations

from typing import Optional

from .regions import REGIONS  # noqa: F401  -- re-export


def region_num(code: str) -> Optional[int]:
    """Return the Google internal region_num for an ISO-3166 alpha-2 code.

    Returns ``None`` for ``anywhere`` (engine-level sentinel) or unknown
    codes. Codes are case-insensitive.
    """
    if not code:
        return None
    c = code.upper().strip()
    if c in ("ANYWHERE", "ALL", ""):
        return None
    entry = REGIONS.get(c)
    if not entry:
        return None
    return entry.get("1")


def valid_codes() -> set[str]:
    """Return the full set of supported ISO codes."""
    return set(REGIONS.keys())


def region_label(code: str) -> Optional[str]:
    """Return the human-readable region label for an ISO code."""
    if not code:
        return None
    entry = REGIONS.get(code.upper().strip())
    if not entry:
        return None
    return entry.get("Region")
