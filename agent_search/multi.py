"""Parallel multi-engine search ("fan-out + merge").

A typical agent research turn needs hits from several complementary engines
(``google`` for freshness, ``reddit`` for opinion, ``arxiv`` for papers,
``stackoverflow`` for code). Calling them sequentially via the single-engine
``search`` command means launching N Chromiums and waiting end-to-end for
each — a ~10-15s tax for three engines.

This module runs the engines concurrently, each in its own thread with its
own browser instance. Total wall-clock time becomes ``max(individual_time)``
instead of ``sum(individual_time)``, plus optional URL-level deduplication
so the agent sees a clean merged feed.

Threading note: Playwright's *sync* API is not thread-safe within a single
``Browser`` instance, so each worker launches its own browser. The startup
cost is paid in parallel and amortised across the whole batch.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
from urllib.parse import urlparse, urlunparse

from .core import BrowserConfig, launch, new_page

log = logging.getLogger(__name__)


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
        browser = launch(BrowserConfig(headless=headless, humanize=True))
        page = new_page(browser)
        instance = engine_cls(page)
        raw = instance.search(query, limit=limit) or []
        return {
            "engine": engine_name,
            "ok": True,
            "count": len(raw),
            "results": [r.__dict__ for r in raw],
            "elapsed_s": round(time.time() - started, 2),
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
    timeout_s: int = 90,
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
    """
    if not engines:
        return {
            "query": query,
            "engines": [],
            "per_engine": {},
            "merged": [],
            "elapsed_s": 0.0,
            "successful": 0,
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
    started = time.time()

    per_engine: dict[str, dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = {
            ex.submit(_run_one_engine, name, query, limit, headless): name
            for name in engines
        }
        for fut in as_completed(futures, timeout=timeout_s):
            name = futures[fut]
            try:
                per_engine[name] = fut.result(timeout=1)
            except Exception as e:
                per_engine[name] = {
                    "engine": name,
                    "ok": False,
                    "error": f"{type(e).__name__}: {e}",
                    "count": 0,
                    "results": [],
                }

    # Make sure every requested engine is represented even if its future
    # didn't complete (timeout / cancellation).
    for name in engines:
        per_engine.setdefault(
            name,
            {"engine": name, "ok": False, "error": "timeout", "count": 0, "results": []},
        )

    merged = merge_results(per_engine)

    return {
        "query": query,
        "engines": engines,
        "per_engine": per_engine,
        "merged": merged,
        "elapsed_s": round(time.time() - started, 2),
        "successful": sum(1 for v in per_engine.values() if v.get("ok") and v.get("count")),
    }
