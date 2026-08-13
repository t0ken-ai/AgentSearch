"""Shared search runner used by CLI, MCP, HTTP, and worker processes."""

from __future__ import annotations

from typing import Any

from .cache import SearchCache, make_cache_key
from .execution import SearchDeadlineExceeded, SearchTrace, execution_scope
from .policies import search_policy
from .results import result_from_dict, result_to_dict


def _record_health(name: str, results, metrics: dict[str, Any]) -> None:
    """Best-effort recording must never turn a successful search into failure."""
    try:
        from .health import HealthLog

        HealthLog().record(
            name,
            ok=bool(results),
            count=len(results),
            ms=int(metrics.get("elapsed_ms", 0)),
            metrics=metrics,
        )
    except Exception:
        pass


def get_cached_search(
    engine_name: str,
    query: str,
    *,
    limit: int,
    options: dict[str, Any] | None,
    cache_partition: str,
    transport: str,
    cache: SearchCache | None = None,
):
    """Return a cached result before a scarce browser session is launched."""
    policy = search_policy(engine_name)
    if not policy.cache_ttl_s:
        return None
    cache = cache or SearchCache()
    key = make_cache_key(
        engine_name,
        query,
        limit,
        dict(options or {}),
        cache_partition,
    )
    try:
        cached = cache.get(key)
    except Exception:
        return None
    if cached is None:
        return None
    trace = SearchTrace(
        engine=engine_name,
        policy=policy,
        transport=transport,
        cache_hit=True,
    )
    results = [result_from_dict(row) for row in cached]
    _record_health(engine_name, results, trace.metrics())
    return results


def execute_search(
    instance,
    query: str,
    *,
    limit: int = 10,
    engine_name: str | None = None,
    options: dict[str, Any] | None = None,
    cache_partition: str = "public",
    cache: SearchCache | None = None,
    use_cache: bool = True,
):
    """Execute one adapter with policy, cache, and metrics applied uniformly.

    Health canaries pass ``use_cache=False`` because a cached success measures
    local storage, not whether the remote engine is currently reachable.
    """
    name = engine_name or getattr(instance, "name", "base")
    policy = search_policy(name)
    cache = cache or SearchCache()
    options = dict(options or {})
    key = make_cache_key(name, query, limit, options, cache_partition)

    trace = SearchTrace(
        engine=name,
        policy=policy,
        transport=getattr(instance, "transport", "browser"),
    )
    cached = None
    if use_cache:
        cached = get_cached_search(
            name,
            query,
            limit=limit,
            options=options,
            cache_partition=cache_partition,
            transport=trace.transport,
            cache=cache,
        )
    if cached is not None:
        results = cached
        trace.cache_hit = True
        instance._agentsearch_metrics = trace.metrics()
        return results

    try:
        with execution_scope(trace):
            # Adapters that override BaseEngine.search still represent one
            # attempt; BaseEngine will update this for each retry it performs.
            trace.attempts = 1
            results = instance.search(query, limit=limit, **options) or []
    except SearchDeadlineExceeded as exc:
        trace.deadline_reached = True
        trace.error = f"{type(exc).__name__}: {exc}"
        instance._agentsearch_metrics = trace.metrics()
        _record_health(name, [], instance._agentsearch_metrics)
        raise
    except Exception as exc:
        trace.error = f"{type(exc).__name__}: {exc}"
        instance._agentsearch_metrics = trace.metrics()
        _record_health(name, [], instance._agentsearch_metrics)
        raise

    payload = [result_to_dict(result) for result in results]
    if use_cache and payload and policy.cache_ttl_s:
        try:
            cache.put(key, payload, policy.cache_ttl_s)
        except Exception:
            pass
    instance._agentsearch_metrics = trace.metrics()
    _record_health(name, results, instance._agentsearch_metrics)
    return results


def search_metrics(instance) -> dict[str, Any]:
    """Return stable metrics even for legacy callers outside execute_search."""
    return dict(getattr(instance, "_agentsearch_metrics", {}) or {})
