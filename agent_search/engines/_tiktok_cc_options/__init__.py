"""TikTok Creative Center enum option loader.

These JSON files (sourced from the lofe-w/tiktok-creative-center-scraper-public
repo) hold the canonical lookup tables for industry IDs, country codes,
period buckets, sort metrics, etc. The TikTok Creative Center backend
expects these exact ID values, so we keep them as data rather than
hardcoded constants.

Public API:
    load_options(name)  → list[dict]   (raw JSON payload)
    valid_ids(name)     → set[str]     (set of valid ``id`` values)
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_DIR = Path(__file__).resolve().parent


@lru_cache(maxsize=None)
def load_options(name: str) -> list[dict[str, Any]]:
    """Load an enum table by file basename (without ``.json``).

    Returns ``[]`` for unknown names so callers don't have to guard.
    """
    fp = _DIR / f"{name}.json"
    if not fp.is_file():
        return []
    try:
        with fp.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except Exception:
        return []


def valid_ids(name: str) -> set[str]:
    """Return the set of valid ``id`` values from an enum table."""
    return {str(item.get("id")) for item in load_options(name) if "id" in item}
