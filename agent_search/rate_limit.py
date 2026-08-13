"""Cross-process courtesy gates for public API adapters."""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

from .cache import default_cache_path
from .execution import (
    SearchDeadlineExceeded,
    current_trace,
    remaining_seconds,
)


def _rate_limit_dir() -> Path:
    return Path(
        os.environ.get(
            "AGENTSEARCH_RATE_LIMIT_DIR",
            str(default_cache_path().parent / "rate-limits"),
        )
    )


def _lock(handle) -> None:
    if os.name == "posix":
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return
    import msvcrt

    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)


def _unlock(handle) -> None:
    if os.name == "posix":
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return
    import msvcrt

    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def wait_for_request_slot(key: str, interval_s: float) -> None:
    """Reserve a host-wide request start slot without serial fixed sleeps.

    A short file lock reserves future wall-clock slots, then releases before
    sleeping. This keeps independent CLI/MCP workers polite without holding a
    process lock for the full interval. Stale timestamps more than one hour in
    the future are discarded to recover from manual clock changes.
    """
    interval = max(0.0, float(interval_s))
    if interval <= 0:
        return
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
    root = _rate_limit_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{digest}.slot"
    handle = open(path, "a+b")
    acquired = False
    try:
        if handle.tell() == 0:
            handle.write(b"0\n")
            handle.flush()
        _lock(handle)
        acquired = True
        now = time.time()
        handle.seek(0)
        try:
            next_slot = float(handle.read().decode("ascii").strip() or "0")
        except (UnicodeDecodeError, ValueError):
            next_slot = 0.0
        if next_slot > now + 3600:
            next_slot = now
        slot = max(now, next_slot)
        delay = max(0.0, slot - now)
        remaining = remaining_seconds()
        if remaining is not None and delay >= remaining:
            trace = current_trace()
            if trace is not None:
                trace.deadline_reached = True
            raise SearchDeadlineExceeded(
                f"{key} courtesy-rate slot exceeds search deadline"
            )
        handle.seek(0)
        handle.truncate()
        handle.write(f"{slot + interval:.6f}\n".encode("ascii"))
        handle.flush()
    finally:
        try:
            if acquired:
                _unlock(handle)
        finally:
            handle.close()

    trace = current_trace()
    if trace is not None:
        trace.wait_ms += int(delay * 1000)
    if delay > 0:
        time.sleep(delay)
