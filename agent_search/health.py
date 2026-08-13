"""Engine health tracking and auto-fallback.

Search engines drift: Google starts CAPTCHA-ing your IP, Reddit redesigns
its DOM, a site adds a new bot test. A single failed query against your
preferred engine shouldn't break the agent — it should silently fall
back to the next-best healthy engine.

This module keeps a small per-user health log on disk and exposes
``search_with_fallback`` that picks the best available engine and
retries down a chain on failure.

Storage format (JSON, ~/.cache/agentsearch/health.json)::

    {
      "google":     {"window": [{"ts": 1700000000, "ok": true,  "count": 5, "ms": 1432}, ...]},
      "duckduckgo": {"window": [...]},
      ...
    }

Each engine's window holds at most ``WINDOW_SIZE`` recent attempts; older
ones are evicted FIFO. Stats are computed lazily from the window.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import Counter
from contextlib import contextmanager
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Sliding window size per engine. 50 attempts is enough to compute a
# reasonable success rate without keeping the file unbounded.
WINDOW_SIZE = 50

# Default fallback chain for "general" web queries when the caller didn't
# pin a specific engine. Ordered by historical reliability + speed.
DEFAULT_CHAIN: list[str] = [
    "duckduckgo",
    "brave",
    "startpage",
    "ecosia",
    "google",
    "bing",
]

# Where the health log lives. Override with AGENTSEARCH_HEALTH_PATH for
# tests / read-only environments.
def default_health_path() -> Path:
    """Resolve at call time so tests and long-running hosts can redirect it."""
    return Path(
        os.environ.get(
            "AGENTSEARCH_HEALTH_PATH",
            str(Path.home() / ".cache" / "agentsearch" / "health.json"),
        )
    )


DEFAULT_HEALTH_PATH = default_health_path()

_LOCK = threading.Lock()


@contextmanager
def _file_lock(path: Path):
    """Serialize health read-modify-write across fan-out worker processes."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+b")
    try:
        if os.name == "posix":
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        else:
            import msvcrt

            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        yield
    finally:
        if os.name == "posix":
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        else:
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        handle.close()


class HealthLog:
    """Tiny JSON-backed sliding window of per-engine search outcomes."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else default_health_path()
        self._data: dict[str, dict[str, Any]] = self._load()

    # ------------------------------------------------------------------ I/O

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            with open(self.path, encoding="utf-8") as fh:
                obj = json.load(fh)
            if not isinstance(obj, dict):
                return {}
            return obj
        except FileNotFoundError:
            return {}
        except Exception as e:
            log.warning("[health] load failed: %s — starting fresh", e)
            return {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception as e:
            log.warning("[health] save failed: %s", e)

    def refresh(self) -> "HealthLog":
        """Reload the atomically replaced file after other workers write it."""
        with _LOCK:
            self._data = self._load()
        return self

    # ------------------------------------------------------------- recording

    def record(
        self,
        engine: str,
        *,
        ok: bool,
        count: int = 0,
        ms: int = 0,
        metrics: dict[str, Any] | None = None,
    ) -> None:
        """Record a single attempt against an engine.

        ``ok`` True iff the engine returned at least one result without
        raising. ``count`` is the number of results returned. ``ms`` is
        the wall-clock time spent on the attempt.
        """
        with _LOCK:
            with _file_lock(self.path):
                # Every process may have loaded an older snapshot before it
                # waited for the lock, so reload inside the critical section.
                self._data = self._load()
                slot = self._data.setdefault(engine, {"window": []})
                window: list[dict[str, Any]] = slot.setdefault("window", [])
                row = {
                    "ts": int(time.time()),
                    "ok": bool(ok),
                    "count": int(count),
                    "ms": int(ms),
                }
                if metrics:
                    # Keep the health schema flat and JSON-compatible. Unknown
                    # future metrics are ignored so old logs remain readable.
                    for key in (
                        "transport",
                        "navigation_count",
                        "navigation_ms",
                        "wait_ms",
                        "condition_wait_ms",
                        "blocked_reason",
                        "cache_hit",
                        "deadline_reached",
                    ):
                        if key in metrics:
                            row[key] = metrics[key]
                window.append(row)
                if len(window) > WINDOW_SIZE:
                    del window[: len(window) - WINDOW_SIZE]
                self._save()

    # ----------------------------------------------------------------- stats

    def stats(self, engine: str) -> dict[str, Any]:
        """Aggregate stats for one engine. Returns zeros if no data yet."""
        slot = self._data.get(engine, {})
        window: list[dict[str, Any]] = slot.get("window", [])
        if not window:
            return {
                "engine": engine,
                "attempts": 0,
                "success_rate": None,
                "avg_results": None,
                "avg_ms": None,
                "p50_ms": None,
                "p95_ms": None,
                "avg_navigation_ms": None,
                "avg_wait_ms": None,
                "avg_condition_wait_ms": None,
                "cache_hit_rate": None,
                "deadline_rate": None,
                "block_reasons": {},
                "last_attempt": None,
                "last_ok": None,
            }
        attempts = len(window)
        ok_count = sum(1 for w in window if w.get("ok"))
        durations = sorted(int(w.get("ms", 0)) for w in window)

        def percentile(ratio: float) -> int:
            # Nearest-rank keeps the value observable in this small (<=50)
            # window instead of interpolating a latency no call experienced.
            rank = int(len(durations) * ratio + 0.999) - 1
            return durations[max(0, min(len(durations) - 1, rank))]

        block_reasons = Counter(
            str(w.get("blocked_reason"))
            for w in window
            if w.get("blocked_reason")
        )
        return {
            "engine": engine,
            "attempts": attempts,
            "success_rate": round(ok_count / attempts, 3),
            "avg_results": round(sum(w.get("count", 0) for w in window) / attempts, 2),
            "avg_ms": int(sum(w.get("ms", 0) for w in window) / attempts) if attempts else 0,
            "p50_ms": percentile(0.50),
            "p95_ms": percentile(0.95),
            "avg_navigation_ms": int(
                sum(int(w.get("navigation_ms", 0)) for w in window) / attempts
            ),
            "avg_wait_ms": int(
                sum(int(w.get("wait_ms", 0)) for w in window) / attempts
            ),
            "avg_condition_wait_ms": int(
                sum(int(w.get("condition_wait_ms", 0)) for w in window)
                / attempts
            ),
            "cache_hit_rate": round(
                sum(bool(w.get("cache_hit")) for w in window) / attempts, 3
            ),
            "deadline_rate": round(
                sum(bool(w.get("deadline_reached")) for w in window) / attempts,
                3,
            ),
            "block_reasons": dict(block_reasons.most_common(5)),
            "last_attempt": window[-1].get("ts"),
            "last_ok": bool(window[-1].get("ok")),
        }

    def all_stats(self) -> list[dict[str, Any]]:
        return [self.stats(e) for e in sorted(self._data.keys())]

    # ------------------------------------------------------------- selection

    def score(self, engine: str) -> float:
        """Composite score used to rank engines for fallback selection.

        Higher is better. Combines success rate (primary signal), recent
        ``ok`` (heavy multiplier — a single recent failure docks an
        otherwise healthy engine), and a small bonus for "fast" engines.

        Engines with no history get a neutral score (0.5) so they're
        eligible for selection but lose to anyone with positive evidence.
        """
        s = self.stats(engine)
        if s["attempts"] == 0:
            return 0.5
        sr = s["success_rate"] or 0.0
        recent_ok = 1.0 if s["last_ok"] else 0.4  # recent fail kicks score way down
        speed = 1.0 if (s["avg_ms"] or 0) < 4000 else 0.85
        return sr * recent_ok * speed


# ----------------------------------------------------------- fallback runner


def _run_search_once(engine_name: str, query: str, limit: int, headless: bool):
    """Compatibility wrapper around the transport-aware worker runner."""
    from .multi import _run_one_engine

    payload = _run_one_engine(engine_name, query, limit, headless)
    payload["ms"] = int(float(payload.get("elapsed_s", 0)) * 1000)
    return payload


def search_with_fallback(
    query: str,
    *,
    primary: str | None = None,
    limit: int = 10,
    chain: list[str] | None = None,
    headless: bool = True,
    health: HealthLog | None = None,
    timeout_s: float = 45.0,
    hedge_delay_s: float = 1.25,
) -> dict[str, Any]:
    """Hedge a healthy fallback after a delay and return the first useful hit.

    The ``chain`` is reordered by health score on every call so a
    consistently-flaky engine bubbles down. The user's chosen ``primary``
    is *always* tried first regardless of score (callers know what they
    want); only the *fallback* order adapts to health.

    Returns a dict with:
      * ``query``: the input query
      * ``engine``: the engine that actually produced results (or None on failure)
      * ``results``: list of SearchResult dicts
      * ``attempts``: ordered list of {engine, ok, count, ms, error?} per attempt
      * ``fallback``: True iff primary failed and a backup served the answer
    """
    health = health or HealthLog()
    health.refresh()
    chain = chain or list(DEFAULT_CHAIN)

    # Build the ordered try-list:
    #   1. primary (if given)
    #   2. remaining chain entries, sorted by health score DESC
    try_list: list[str] = []
    seen: set[str] = set()
    if primary:
        try_list.append(primary)
        seen.add(primary)
    rest = sorted((e for e in chain if e not in seen), key=health.score, reverse=True)
    try_list.extend(rest)

    from .multi import race_search

    race = race_search(
        query,
        try_list,
        limit=limit,
        headless=headless,
        timeout_s=timeout_s,
        hedge_delay_s=hedge_delay_s,
        health_path=str(health.path),
    )
    health.refresh()
    attempts: list[dict[str, Any]] = []
    for engine_name, out in race["per_engine"].items():
        attempts.append(
            {
                "engine": engine_name,
                "ok": bool(out.get("ok")),
                "count": len(out.get("results", [])),
                "ms": int(float(out.get("elapsed_s", 0)) * 1000),
                "error": out.get("error"),
                **({"cancelled": True} if out.get("cancelled") else {}),
            }
        )
    winner = race.get("engine")
    if winner:
        return {
            "query": query,
            "engine": winner,
            "results": race["results"],
            "attempts": attempts,
            "fallback": winner != primary if primary else False,
            "elapsed_s": race["elapsed_s"],
        }
    return {
        "query": query,
        "engine": None,
        "results": [],
        "attempts": attempts,
        "fallback": bool(primary),
        "elapsed_s": race["elapsed_s"],
        "deadline_reached": race["deadline_reached"],
    }
