"""Cooperative deadlines and observability for one engine invocation."""

from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator

from .policies import SearchPolicy


class SearchDeadlineExceeded(TimeoutError):
    """Raised when an adapter attempts work after its policy deadline."""


@dataclass
class SearchTrace:
    """Mutable metrics owned by one search invocation."""

    engine: str
    policy: SearchPolicy
    transport: str
    started: float = field(default_factory=time.monotonic)
    attempts: int = 0
    navigation_count: int = 0
    navigation_ms: int = 0
    wait_ms: int = 0
    condition_wait_ms: int = 0
    blocked_reason: str = ""
    cache_hit: bool = False
    deadline_reached: bool = False
    error: str = ""

    @property
    def deadline(self) -> float:
        return self.started + self.policy.deadline_s

    def metrics(self) -> dict[str, Any]:
        """Return JSON-compatible metrics for health records and callers."""
        return {
            "transport": self.transport,
            "attempts": self.attempts,
            "elapsed_ms": int((time.monotonic() - self.started) * 1000),
            "navigation_count": self.navigation_count,
            "navigation_ms": self.navigation_ms,
            "wait_ms": self.wait_ms,
            "condition_wait_ms": self.condition_wait_ms,
            "blocked_reason": self.blocked_reason,
            "cache_hit": self.cache_hit,
            "deadline_reached": self.deadline_reached,
            "error": self.error,
        }


_CURRENT_TRACE: ContextVar[SearchTrace | None] = ContextVar(
    "agentsearch_trace", default=None
)


def current_trace() -> SearchTrace | None:
    return _CURRENT_TRACE.get()


def remaining_seconds() -> float | None:
    trace = current_trace()
    if trace is None:
        return None
    return max(0.0, trace.deadline - time.monotonic())


def ensure_time_remaining() -> None:
    """Fail before starting more work once the policy budget is exhausted."""
    remaining = remaining_seconds()
    if remaining is not None and remaining <= 0:
        trace = current_trace()
        if trace is not None:
            trace.deadline_reached = True
        raise SearchDeadlineExceeded("search policy deadline reached")


@contextmanager
def execution_scope(trace: SearchTrace) -> Iterator[SearchTrace]:
    """Install a trace in the current thread/task for nested helpers."""
    token = _CURRENT_TRACE.set(trace)
    try:
        yield trace
    finally:
        _CURRENT_TRACE.reset(token)
