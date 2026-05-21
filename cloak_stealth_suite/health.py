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
    "google",
    "bing",
    "brave",
    "startpage",
    "qwant",
    "ecosia",
]

# Where the health log lives. Override with AGENTSEARCH_HEALTH_PATH for
# tests / read-only environments.
DEFAULT_HEALTH_PATH = Path(
    os.environ.get(
        "AGENTSEARCH_HEALTH_PATH",
        str(Path.home() / ".cache" / "agentsearch" / "health.json"),
    )
)

_LOCK = threading.Lock()


class HealthLog:
    """Tiny JSON-backed sliding window of per-engine search outcomes."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else DEFAULT_HEALTH_PATH
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

    # ------------------------------------------------------------- recording

    def record(self, engine: str, *, ok: bool, count: int = 0, ms: int = 0) -> None:
        """Record a single attempt against an engine.

        ``ok`` True iff the engine returned at least one result without
        raising. ``count`` is the number of results returned. ``ms`` is
        the wall-clock time spent on the attempt.
        """
        with _LOCK:
            slot = self._data.setdefault(engine, {"window": []})
            window: list[dict[str, Any]] = slot.setdefault("window", [])
            window.append({"ts": int(time.time()), "ok": bool(ok), "count": int(count), "ms": int(ms)})
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
                "last_attempt": None,
                "last_ok": None,
            }
        attempts = len(window)
        ok_count = sum(1 for w in window if w.get("ok"))
        return {
            "engine": engine,
            "attempts": attempts,
            "success_rate": round(ok_count / attempts, 3),
            "avg_results": round(sum(w.get("count", 0) for w in window) / attempts, 2),
            "avg_ms": int(sum(w.get("ms", 0) for w in window) / attempts) if attempts else 0,
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
    """Internal: launch a fresh browser, run one engine, return results+meta."""
    from .core import BrowserConfig, launch, new_page  # local import to keep startup cheap
    from .cli import _get_engine

    started = time.time()
    try:
        engine_cls = _get_engine(engine_name)
    except ValueError as e:
        return {"ok": False, "results": [], "error": str(e), "ms": 0}

    browser = None
    try:
        browser = launch(BrowserConfig(headless=headless, humanize=True))
        page = new_page(browser)
        instance = engine_cls(page)
        raw = instance.search(query, limit=limit) or []
        return {
            "ok": bool(raw),
            "results": raw,
            "ms": int((time.time() - started) * 1000),
        }
    except Exception as e:
        return {
            "ok": False,
            "results": [],
            "error": f"{type(e).__name__}: {e}",
            "ms": int((time.time() - started) * 1000),
        }
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


def search_with_fallback(
    query: str,
    *,
    primary: str | None = None,
    limit: int = 10,
    chain: list[str] | None = None,
    headless: bool = True,
    health: HealthLog | None = None,
) -> dict[str, Any]:
    """Try ``primary`` first; on empty/error, walk down the fallback chain.

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

    attempts: list[dict[str, Any]] = []
    for engine_name in try_list:
        out = _run_search_once(engine_name, query, limit, headless)
        attempts.append(
            {
                "engine": engine_name,
                "ok": out["ok"],
                "count": len(out.get("results", [])),
                "ms": out["ms"],
                "error": out.get("error"),
            }
        )
        health.record(
            engine_name,
            ok=out["ok"],
            count=len(out.get("results", [])),
            ms=out["ms"],
        )
        if out["ok"]:
            return {
                "query": query,
                "engine": engine_name,
                "results": [r.__dict__ for r in out["results"]],
                "attempts": attempts,
                "fallback": engine_name != primary if primary else False,
            }

    return {
        "query": query,
        "engine": None,
        "results": [],
        "attempts": attempts,
        "fallback": bool(primary),
    }
