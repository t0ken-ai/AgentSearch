"""Stable result contract shared by CLI, MCP, HTTP, and engine adapters.

Engine adapters historically attach site-specific fields directly to a
``SearchResult`` instance (for example ``rating`` or ``video_url``). That
extension mechanism is intentionally preserved: rewriting 100+ adapters into
one rigid schema would discard useful source-specific data. Public transports
must, however, serialize through :func:`result_to_dict` instead of depending
on ``__dict__`` so the in-memory implementation can evolve independently.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass
class SearchResult:
    """Common fields returned by every search engine.

    Adapters may add JSON-compatible attributes at runtime. ``to_dict``
    includes those extension fields and returns a defensive copy, which keeps
    the current adapter flexibility while giving external callers a stable
    serialization boundary.
    """

    title: str
    url: str
    snippet: str = ""
    score: int | None = None
    # ISO-8601 when the source exposes a publication date; empty otherwise.
    # Consumers use this field for recency ordering without parsing snippets.
    published_date: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Return base and adapter-specific fields as a defensive copy."""
        return dict(vars(self))


def result_to_dict(result: Any) -> dict[str, Any]:
    """Serialize a result-like object without exposing storage details.

    ``SearchResult`` is the primary input, but image/ad/app result types use
    their own ``to_dict`` methods and some internal paths already hold plain
    mappings. Supporting all three shapes lets public transports share one
    contract while adapters migrate gradually.

    Raises:
        TypeError: If the object cannot produce a mapping payload.
    """
    if isinstance(result, Mapping):
        return dict(result)

    serializer = getattr(result, "to_dict", None)
    if callable(serializer):
        payload = serializer()
        if isinstance(payload, Mapping):
            return dict(payload)
        raise TypeError(
            f"{type(result).__name__}.to_dict() returned "
            f"{type(payload).__name__}, expected a mapping"
        )

    try:
        return dict(vars(result))
    except TypeError as exc:
        raise TypeError(
            f"cannot serialize result of type {type(result).__name__}"
        ) from exc


def result_from_dict(payload: Mapping[str, Any]) -> SearchResult:
    """Rehydrate cached data while preserving adapter extension fields."""
    base_names = {"title", "url", "snippet", "score", "published_date"}
    base = {name: payload.get(name) for name in base_names if name in payload}
    result = SearchResult(
        title=str(base.get("title") or ""),
        url=str(base.get("url") or ""),
        snippet=str(base.get("snippet") or ""),
        score=base.get("score"),
        published_date=str(base.get("published_date") or ""),
    )
    for name, value in payload.items():
        if name not in base_names:
            setattr(result, name, value)
    return result
