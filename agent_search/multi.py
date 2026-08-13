"""Parallel multi-engine search ("fan-out + merge").

A typical agent research turn needs hits from several complementary engines
(``google`` for freshness, ``reddit`` for opinion, ``arxiv`` for papers,
``stackoverflow`` for code). Calling them sequentially via the single-engine
``search`` command means launching N Chromiums and waiting end-to-end for
each — a ~10-15s tax for three engines.

This module runs engines in isolated worker processes. Direct-HTTP adapters
run concurrently; browser workers also obey the configured host-capacity
budget (four by default in keyless mode). Each browser worker owns its process,
which preserves Playwright affinity and lets the supervisor terminate a stuck
browser at the requested deadline. Thread pools cannot provide that guarantee:
Python cannot cancel a running thread, and ``ThreadPoolExecutor`` waits for its
workers during shutdown.
"""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
import signal
import sys
import time
from collections import deque
from collections.abc import Callable
from multiprocessing.connection import wait
from typing import Any
from urllib.parse import urlparse, urlunparse

from .core import BrowserConfig, environment_proxy_url, launch, new_page
from .policies import (
    browser_concurrency_limit,
    engine_uses_browser,
    max_parallelism,
)
from .results import result_to_dict
from .runtime import execute_search, get_cached_search, search_metrics

log = logging.getLogger(__name__)

EngineRunner = Callable[[str, str, int, bool], dict[str, Any]]
BrowserClassifier = Callable[[str], bool]


def _engine_requires_browser(engine_name: str) -> bool:
    """Resolve transport in the supervisor before spending a worker slot."""
    from .cli import _get_engine

    try:
        return engine_uses_browser(_get_engine(engine_name))
    except ValueError:
        return False


def _start_method_for_main(
    main_file: str | None,
    *,
    interactive: bool = False,
) -> str:
    """Choose a safe multiprocessing bootstrap for the calling environment.

    ``spawn`` is required for normal CLI/MCP execution because it never
    inherits Playwright's greenlet state. Python cannot spawn from notebooks,
    ``python -c``, or stdin, however: their synthetic ``<stdin>`` path cannot
    be imported in the child. POSIX callers in that narrow interactive case
    fall back to ``fork``; Windows has no equivalent and retains ``spawn`` so
    it returns a clear worker-start error instead of selecting an unavailable
    method.
    """
    if os.name == "posix" and (
        interactive or not main_file or not os.path.isfile(main_file)
    ):
        return "fork"
    return "spawn"


def _run_one_engine(
    engine_name: str,
    query: str,
    limit: int,
    headless: bool,
) -> dict[str, Any]:
    """Run a single engine end-to-end. Always returns a dict, never raises."""
    # Imports are deferred so the helper can be re-used in subprocess
    # contexts (e.g. multiprocessing) without pulling Playwright at import
    # time.
    from .cli import _get_engine  # noqa: WPS433

    started = time.time()
    try:
        engine_cls = _get_engine(engine_name)
    except ValueError as e:
        return {
            "engine": engine_name,
            "ok": False,
            "error": str(e),
            "count": 0,
            "results": [],
            "elapsed_s": round(time.time() - started, 2),
        }

    browser = None
    try:
        effective_proxy = environment_proxy_url()
        if engine_uses_browser(engine_cls):
            from .identity import resolve_identity

            planned = resolve_identity(
                engine_name=getattr(engine_cls, "name", engine_name),
                proxy=effective_proxy,
            )
            cached = get_cached_search(
                getattr(engine_cls, "name", engine_name),
                query,
                limit=limit,
                options=None,
                cache_partition=planned.cache_partition,
                transport="browser",
            )
            if cached is not None:
                return {
                    "engine": engine_name,
                    "ok": True,
                    "count": len(cached),
                    "results": [result_to_dict(r) for r in cached],
                    "elapsed_s": round(time.time() - started, 2),
                    "metrics": {"transport": "browser", "cache_hit": True},
                }
            browser = launch(BrowserConfig(
                headless=headless,
                engine_name=getattr(engine_cls, "name", engine_name),
                proxy=effective_proxy,
            ))
            instance = engine_cls(new_page(browser))
            identity = getattr(browser, "_agentsearch_identity", None)
            partition = identity.cache_partition if identity else "browser"
        else:
            instance = engine_cls(None)
            if hasattr(instance, "set_proxy"):
                instance.set_proxy(effective_proxy)
            from .identity import http_cache_partition

            partition = http_cache_partition(
                getattr(engine_cls, "name", engine_name),
                effective_proxy,
            )
        raw = execute_search(
            instance,
            query,
            limit=limit,
            engine_name=getattr(engine_cls, "name", engine_name),
            cache_partition=partition,
        )
        return {
            "engine": engine_name,
            "ok": True,
            "count": len(raw),
            "results": [result_to_dict(r) for r in raw],
            "elapsed_s": round(time.time() - started, 2),
            "metrics": search_metrics(instance),
        }
    except Exception as e:  # noqa: BLE001 — we explicitly want the union of failures
        log.warning("[multi] engine %s failed: %s", engine_name, e)
        return {
            "engine": engine_name,
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "count": 0,
            "results": [],
            "elapsed_s": round(time.time() - started, 2),
        }
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


def _run_one_image_engine(
    engine_name: str,
    query: str,
    limit: int,
    headless: bool,
) -> dict[str, Any]:
    """Run one image adapter with the same worker contract as web search."""
    from .cli import _get_engine  # noqa: WPS433

    started = time.time()
    try:
        engine_cls = _get_engine(engine_name)
    except ValueError as exc:
        return {
            "engine": engine_name,
            "ok": False,
            "error": str(exc),
            "count": 0,
            "results": [],
            "elapsed_s": round(time.time() - started, 2),
        }

    if not getattr(engine_cls, "is_image_engine", False):
        return {
            "engine": engine_name,
            "ok": False,
            "error": "not an image engine",
            "count": 0,
            "results": [],
            "elapsed_s": round(time.time() - started, 2),
        }

    browser = None
    try:
        from .identity import resolve_identity

        effective_proxy = environment_proxy_url()
        planned = resolve_identity(
            engine_name=getattr(engine_cls, "name", engine_name),
            proxy=effective_proxy,
        )
        cached = get_cached_search(
            getattr(engine_cls, "name", engine_name),
            query,
            limit=limit,
            options=None,
            cache_partition=planned.cache_partition,
            transport="browser",
        )
        if cached is not None:
            return {
                "engine": engine_name,
                "ok": True,
                "count": len(cached),
                "results": [result_to_dict(result) for result in cached],
                "elapsed_s": round(time.time() - started, 2),
                "metrics": {"transport": "browser", "cache_hit": True},
            }
        browser = launch(BrowserConfig(
            headless=headless,
            engine_name=getattr(engine_cls, "name", engine_name),
            proxy=effective_proxy,
        ))
        page = new_page(browser)
        instance = engine_cls(page)
        identity = getattr(browser, "_agentsearch_identity", None)
        raw = execute_search(
            instance,
            query,
            limit=limit,
            engine_name=getattr(engine_cls, "name", engine_name),
            cache_partition=(identity.cache_partition if identity else "browser"),
        )
        return {
            "engine": engine_name,
            "ok": True,
            "count": len(raw),
            "results": [result_to_dict(result) for result in raw],
            "elapsed_s": round(time.time() - started, 2),
            "metrics": search_metrics(instance),
        }
    except Exception as exc:  # adapters and browser transports fail alike
        log.warning("[multi] image engine %s failed: %s", engine_name, exc)
        return {
            "engine": engine_name,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "count": 0,
            "results": [],
            "elapsed_s": round(time.time() - started, 2),
        }
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


def _worker_process(
    connection,
    runner: EngineRunner,
    engine_name: str,
    query: str,
    limit: int,
    headless: bool,
    health_path: str | None,
) -> None:
    """Run one engine in an isolated process and send one result payload.

    On POSIX the worker starts a new process group before launching Chromium.
    The parent records whether that isolation succeeded, so a timeout can
    terminate both Python and any browser descendants without risking the
    parent's process group.
    """
    group_isolated = False
    if health_path:
        # Runtime health recording happens inside this worker. Passing the
        # caller's path preserves custom HealthLog instances without parent
        # and child both recording the same attempt.
        os.environ["AGENTSEARCH_HEALTH_PATH"] = health_path
    if os.name == "posix" and hasattr(os, "setsid"):
        try:
            os.setsid()
            group_isolated = True
        except OSError:
            # Process-level termination remains available as a fallback.
            pass

    try:
        connection.send(("ready", group_isolated))
        try:
            payload = runner(engine_name, query, limit, headless)
        except BaseException as exc:  # child must always report a payload
            payload = {
                "engine": engine_name,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "count": 0,
                "results": [],
                "elapsed_s": 0.0,
            }
        connection.send(("result", payload))
    except (BrokenPipeError, EOFError, OSError):
        # The parent may close the pipe while enforcing the deadline.
        pass
    finally:
        connection.close()


def _signal_worker(process, *, group_isolated: bool, force: bool) -> None:
    """Signal one worker, including browser descendants when isolated."""
    if not process.is_alive():
        return

    if group_isolated and os.name == "posix" and process.pid:
        try:
            os.killpg(process.pid, signal.SIGKILL if force else signal.SIGTERM)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    if force:
        process.kill()
    else:
        process.terminate()


def _terminate_workers(states: list[dict[str, Any]]) -> None:
    """Stop workers in parallel under one bounded cleanup budget.

    Signals are sent to every process before waiting. This matters at the hard
    deadline: waiting two seconds per worker would turn an eight-engine fan-out
    into sixteen seconds of unadvertised tail latency.
    """
    for state in states:
        _signal_worker(
            state["process"],
            group_isolated=state["group_isolated"],
            force=False,
        )

    graceful_deadline = time.monotonic() + 2.0
    for state in states:
        remaining = max(0.0, graceful_deadline - time.monotonic())
        state["process"].join(timeout=remaining)

    survivors = [state for state in states if state["process"].is_alive()]
    for state in survivors:
        _signal_worker(
            state["process"],
            group_isolated=state["group_isolated"],
            force=True,
        )

    force_deadline = time.monotonic() + 1.0
    for state in survivors:
        remaining = max(0.0, force_deadline - time.monotonic())
        state["process"].join(timeout=remaining)


def _terminate_worker(process, *, group_isolated: bool) -> None:
    """Stop one worker through the same bounded cleanup path."""
    _terminate_workers([{
        "process": process,
        "group_isolated": group_isolated,
    }])


def _timeout_result(engine_name: str, timeout_s: float) -> dict[str, Any]:
    """Return the stable per-engine shape used when the deadline wins."""
    return {
        "engine": engine_name,
        "ok": False,
        "error": f"timeout after {timeout_s:g}s",
        "count": 0,
        "results": [],
        "elapsed_s": round(timeout_s, 2),
        "timed_out": True,
    }


def _cancelled_result(engine_name: str, winner: str) -> dict[str, Any]:
    """Return an explicit hedge cancellation instead of mislabeling timeout."""
    return {
        "engine": engine_name,
        "ok": False,
        "error": f"cancelled after {winner} returned results",
        "count": 0,
        "results": [],
        "elapsed_s": 0.0,
        "cancelled": True,
    }


def _run_process_fanout(
    engine_names: list[str],
    query: str,
    limit: int,
    headless: bool,
    timeout_s: float,
    max_workers: int,
    *,
    runner: EngineRunner = _run_one_engine,
    browser_classifier: BrowserClassifier | None = None,
    max_browser_workers: int | None = None,
    hedge_delay_s: float = 0.0,
    stop_on_first_success: bool = False,
    health_path: str | None = None,
) -> tuple[dict[str, dict[str, Any]], bool]:
    """Supervise isolated engine workers until completion or deadline.

    ``runner`` is injectable for offline tests; production passes one of the
    module-level web/image runners. Normal file-backed entry points use
    ``spawn`` so no Playwright/greenlet state is inherited. Interactive POSIX
    callers use the documented ``fork`` fallback because Python cannot spawn
    from a synthetic ``<stdin>`` or notebook path.
    """
    main_module = sys.modules.get("__main__")
    main_file = getattr(main_module, "__file__", None)
    interactive = hasattr(sys, "ps1") or "ipykernel" in sys.modules
    context = mp.get_context(
        _start_method_for_main(main_file, interactive=interactive)
    )
    pending = deque(engine_names)
    active: dict[str, dict[str, Any]] = {}
    per_engine: dict[str, dict[str, Any]] = {}
    deadline = time.monotonic() + max(0.0, timeout_s)
    browser_budget = max_browser_workers or browser_concurrency_limit()
    next_hedge_at = time.monotonic()
    winner: str | None = None

    def start_available() -> None:
        nonlocal next_hedge_at
        while pending and len(active) < max_workers and time.monotonic() < deadline:
            if hedge_delay_s > 0 and active and time.monotonic() < next_hedge_at:
                break
            active_browsers = sum(
                bool(state.get("uses_browser")) for state in active.values()
            )
            selected_index = None
            selected_uses_browser = False
            for index, candidate in enumerate(pending):
                uses_browser = bool(
                    browser_classifier and browser_classifier(candidate)
                )
                if not uses_browser or active_browsers < browser_budget:
                    selected_index = index
                    selected_uses_browser = uses_browser
                    break
            if selected_index is None:
                break
            pending.rotate(-selected_index)
            name = pending.popleft()
            pending.rotate(selected_index)
            recv_conn, send_conn = context.Pipe(duplex=False)
            process = context.Process(
                target=_worker_process,
                args=(
                    send_conn,
                    runner,
                    name,
                    query,
                    limit,
                    headless,
                    health_path,
                ),
                name=f"agentsearch-{name}",
            )
            try:
                process.start()
            except Exception as exc:
                recv_conn.close()
                send_conn.close()
                per_engine[name] = {
                    "engine": name,
                    "ok": False,
                    "error": f"worker start failed: {type(exc).__name__}: {exc}",
                    "count": 0,
                    "results": [],
                    "elapsed_s": 0.0,
                }
                continue
            send_conn.close()
            active[name] = {
                "process": process,
                "connection": recv_conn,
                "group_isolated": False,
                "uses_browser": selected_uses_browser,
            }
            if hedge_delay_s > 0:
                next_hedge_at = time.monotonic() + hedge_delay_s
                break

    start_available()
    while active or pending:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break

        start_available()
        connections = [state["connection"] for state in active.values()]
        if not connections:
            continue

        # Poll periodically so a child that exits before sending a payload is
        # reported promptly instead of consuming the entire query deadline.
        ready_connections = wait(connections, timeout=min(remaining, 0.1))
        for connection in ready_connections:
            name = next(
                key for key, state in active.items()
                if state["connection"] is connection
            )
            state = active[name]
            try:
                message_type, payload = connection.recv()
            except (EOFError, OSError):
                message_type, payload = "closed", None

            if message_type == "ready":
                state["group_isolated"] = bool(payload)
                continue
            if message_type == "result":
                per_engine[name] = payload
            else:
                per_engine[name] = {
                    "engine": name,
                    "ok": False,
                    "error": "worker exited without a result",
                    "count": 0,
                    "results": [],
                    "elapsed_s": 0.0,
                }

            connection.close()
            process = state["process"]
            process.join(timeout=1.0)
            if process.is_alive():
                _terminate_worker(
                    process, group_isolated=state["group_isolated"]
                )
            del active[name]
            if (
                stop_on_first_success
                and per_engine[name].get("ok")
                and per_engine[name].get("count")
            ):
                per_engine[name]["winner"] = True
                winner = name
                break

        if winner is not None:
            _terminate_workers(list(active.values()))
            for name, state in list(active.items()):
                state["connection"].close()
                per_engine[name] = _cancelled_result(name, winner)
            for name in pending:
                per_engine[name] = _cancelled_result(name, winner)
            active.clear()
            pending.clear()
            break

        # Detect crashes whose pipe never produced a readable payload.
        for name, state in list(active.items()):
            process = state["process"]
            connection = state["connection"]
            if process.is_alive() or connection.poll():
                continue
            process.join(timeout=0.2)
            connection.close()
            per_engine[name] = {
                "engine": name,
                "ok": False,
                "error": f"worker exited with code {process.exitcode}",
                "count": 0,
                "results": [],
                "elapsed_s": 0.0,
            }
            del active[name]

    deadline_reached = bool(active or pending)
    if deadline_reached:
        _terminate_workers(list(active.values()))
        for name, state in list(active.items()):
            state["connection"].close()
            per_engine[name] = _timeout_result(name, timeout_s)
        for name in pending:
            per_engine[name] = _timeout_result(name, timeout_s)

    # Preserve request order regardless of worker completion order.
    ordered = {name: per_engine[name] for name in engine_names}
    return ordered, deadline_reached


def _normalize_url(u: str) -> str:
    """Normalise a URL so equivalent variants dedupe correctly.

    * lowercase scheme + host
    * strip a single trailing slash from the path
    * drop fragments (``#anchor``)
    * keep query as-is (rearranging it would lose meaning for SERPs)
    """
    if not u:
        return u
    try:
        p = urlparse(u)
    except Exception:
        return u
    scheme = (p.scheme or "").lower()
    netloc = (p.netloc or "").lower()
    path = p.path or ""
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return urlunparse((scheme, netloc, path, p.params, p.query, ""))


def merge_results(per_engine: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge per-engine results into one URL-deduped list.

    Each output item carries an ``engines`` list naming every engine that
    surfaced the URL (so the agent can see consensus signals). Results are
    sorted by ``len(engines) DESC, score DESC`` so consensus + high-score
    hits float to the top.
    """
    by_url: dict[str, dict[str, Any]] = {}
    for engine_name, payload in per_engine.items():
        for r in payload.get("results", []):
            url = r.get("url") or ""
            key = _normalize_url(url)
            if not key:
                continue
            existing = by_url.get(key)
            if existing is None:
                copy = dict(r)
                copy["engines"] = [engine_name]
                by_url[key] = copy
                continue
            # Merge: prefer the longer snippet; concatenate engine list.
            if engine_name not in existing["engines"]:
                existing["engines"].append(engine_name)
            new_snip = r.get("snippet") or ""
            old_snip = existing.get("snippet") or ""
            if len(new_snip) > len(old_snip):
                existing["snippet"] = new_snip
            # Prefer the higher score if both present.
            new_score = r.get("score")
            old_score = existing.get("score")
            if isinstance(new_score, (int, float)) and (
                old_score is None or new_score > old_score
            ):
                existing["score"] = new_score

    def sort_key(item: dict[str, Any]):
        engines_count = len(item.get("engines") or [])
        score = item.get("score")
        score = score if isinstance(score, (int, float)) else 0
        return (-engines_count, -score)

    return sorted(by_url.values(), key=sort_key)


def search_many(
    query: str,
    engines: list[str],
    *,
    limit: int = 5,
    headless: bool = True,
    timeout_s: float = 90,
    max_workers: int | None = None,
) -> dict[str, Any]:
    """Run ``engines`` in parallel and return their combined output.

    Returns a dict with:
      * ``query``:        the original query
      * ``engines``:      the list of engines requested
      * ``per_engine``:   {engine_name: {ok, count, results, elapsed_s, ...}}
      * ``merged``:       URL-deduped list (see :func:`merge_results`)
      * ``elapsed_s``:    total wall-clock time for the whole fan-out
      * ``successful``:   how many engines returned at least one result
      * ``timed_out``:    how many engines exceeded the hard deadline
    """
    if not engines:
        return {
            "query": query,
            "engines": [],
            "per_engine": {},
            "merged": [],
            "elapsed_s": 0.0,
            "successful": 0,
            "timed_out": 0,
            "deadline_reached": False,
        }

    # De-duplicate engine names while preserving order (so a user passing
    # "google,google,reddit" still gets two unique workers).
    seen = set()
    unique = []
    for e in engines:
        if e not in seen:
            unique.append(e)
            seen.add(e)
    engines = unique

    workers = max_workers or min(len(engines), 8)
    workers = max_parallelism(workers)
    workers = max(1, min(int(workers), len(engines)))
    timeout_s = max(0.0, float(timeout_s))
    started = time.time()
    per_engine, deadline_reached = _run_process_fanout(
        engines,
        query,
        limit,
        headless,
        timeout_s,
        workers,
        browser_classifier=_engine_requires_browser,
    )

    merged = merge_results(per_engine)

    return {
        "query": query,
        "engines": engines,
        "per_engine": per_engine,
        "merged": merged,
        "elapsed_s": round(time.time() - started, 2),
        "successful": sum(1 for v in per_engine.values() if v.get("ok") and v.get("count")),
        "timed_out": sum(1 for v in per_engine.values() if v.get("timed_out")),
        "deadline_reached": deadline_reached,
    }


def race_search(
    query: str,
    engines: list[str],
    *,
    limit: int = 10,
    headless: bool = True,
    timeout_s: float = 45.0,
    hedge_delay_s: float = 1.25,
    max_workers: int = 3,
    health_path: str | None = None,
) -> dict[str, Any]:
    """Hedge fallbacks and stop remaining workers on the first useful result.

    The next engine starts only after ``hedge_delay_s`` while the previous one
    is still pending. Browser launches still honor the host capacity limit;
    direct HTTP adapters can race without consuming Chromium resources.
    """
    unique = list(dict.fromkeys(engines))
    if not unique:
        return {
            "query": query,
            "engine": None,
            "results": [],
            "per_engine": {},
            "deadline_reached": False,
        }
    started = time.time()
    per_engine, deadline_reached = _run_process_fanout(
        unique,
        query,
        max(1, int(limit)),
        headless,
        max(0.0, float(timeout_s)),
        max(1, min(int(max_workers), len(unique))),
        browser_classifier=_engine_requires_browser,
        hedge_delay_s=max(0.0, float(hedge_delay_s)),
        stop_on_first_success=True,
        health_path=health_path,
    )
    winner = next(
        (name for name, payload in per_engine.items() if payload.get("winner")),
        None,
    )
    return {
        "query": query,
        "engine": winner,
        "results": per_engine[winner]["results"] if winner else [],
        "per_engine": per_engine,
        "deadline_reached": deadline_reached,
        "elapsed_s": round(time.time() - started, 2),
    }


def search_images_many(
    query: str,
    engines: list[str],
    *,
    limit: int = 10,
    headless: bool = True,
    timeout_s: float = 90,
    max_workers: int | None = None,
) -> dict[str, Any]:
    """Run image adapters behind the same enforceable fan-out deadline.

    Image adapters launch Chromium just like ordinary search adapters, so a
    thread-pool timeout has the same cancellation problem. Keeping this path
    on the shared process supervisor ensures MCP callers are not held open by
    a browser that hangs after the advertised deadline.
    """
    if not engines:
        return {
            "query": query,
            "engines": [],
            "per_engine": {},
            "merged": [],
            "elapsed_s": 0.0,
            "successful": 0,
            "timed_out": 0,
            "deadline_reached": False,
        }

    unique = list(dict.fromkeys(engines))
    workers = max_workers or min(len(unique), 6)
    workers = max_parallelism(workers)
    workers = max(1, min(int(workers), len(unique)))
    timeout_s = max(0.0, float(timeout_s))
    started = time.time()
    per_engine, deadline_reached = _run_process_fanout(
        unique,
        query,
        max(1, min(int(limit), 50)),
        headless,
        timeout_s,
        workers,
        runner=_run_one_image_engine,
        browser_classifier=lambda _name: True,
    )

    # Image URLs are often signed or contain meaningful resize parameters;
    # unlike result-page URLs, normalizing their query string can change the
    # asset. Exact URL equality is therefore the safe de-duplication key.
    merged: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    for engine_name in unique:
        for result in per_engine[engine_name].get("results", []):
            image_url = result.get("image_url") or ""
            if not image_url or image_url in seen_urls:
                continue
            seen_urls.add(image_url)
            merged.append(result)

    return {
        "query": query,
        "engines": unique,
        "per_engine": per_engine,
        "merged": merged,
        "elapsed_s": round(time.time() - started, 2),
        "successful": sum(
            1 for value in per_engine.values()
            if value.get("ok") and value.get("count")
        ),
        "timed_out": sum(
            1 for value in per_engine.values() if value.get("timed_out")
        ),
        "deadline_reached": deadline_reached,
    }
