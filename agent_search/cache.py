"""Small process-safe SQLite cache for repeat public searches."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any


def default_cache_path() -> Path:
    return Path(
        os.environ.get(
            "AGENTSEARCH_CACHE_PATH",
            str(Path.home() / ".cache" / "agentsearch" / "search-cache.sqlite3"),
        )
    )


def cache_enabled() -> bool:
    return os.environ.get("AGENTSEARCH_CACHE", "1") != "0"


def make_cache_key(
    engine: str,
    query: str,
    limit: int,
    options: dict[str, Any] | None,
    partition: str,
) -> str:
    """Hash all inputs that can change results, including browser identity."""
    payload = json.dumps(
        {
            "v": 1,
            "engine": engine,
            "query": query,
            "limit": limit,
            "options": options or {},
            "partition": partition,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class SearchCache:
    """Open a short-lived connection per operation for process fan-out safety."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else default_cache_path()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS search_cache ("
            "key TEXT PRIMARY KEY, payload TEXT NOT NULL, expires REAL NOT NULL)"
        )
        conn.commit()
        return conn

    def get(self, key: str) -> list[dict[str, Any]] | None:
        if not cache_enabled():
            return None
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT payload, expires FROM search_cache WHERE key = ?", (key,)
            ).fetchone()
            if row is None:
                return None
            if float(row[1]) <= time.time():
                conn.execute("DELETE FROM search_cache WHERE key = ?", (key,))
                conn.commit()
                return None
        try:
            payload = json.loads(row[0])
        except (TypeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, list) else None

    def put(self, key: str, payload: list[dict[str, Any]], ttl_s: int) -> None:
        if not cache_enabled() or ttl_s <= 0:
            return
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO search_cache(key, payload, expires) "
                "VALUES (?, ?, ?)",
                (key, encoded, time.time() + ttl_s),
            )
            # Opportunistic cleanup keeps the database bounded without a
            # maintenance thread in CLI and short-lived MCP workers.
            conn.execute("DELETE FROM search_cache WHERE expires <= ?", (time.time(),))
            conn.commit()
