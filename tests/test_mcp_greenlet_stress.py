"""Regression test for the MCP greenlet-thread bug.

Pre-fix, every MCP tool callback used ``await asyncio.to_thread(_run)``,
which dispatches to asyncio's default ``ThreadPoolExecutor`` (worker
count = ``min(32, cpu_count + 4)``). Under load that pool spawns new
worker threads, but Playwright's sync API is greenlet-bound to whichever
thread first launched the browser — calling ``Page.goto`` / etc. from a
different worker raises::

    greenlet.error: cannot switch to a different thread

The fix routes every browser-touching callback through a single-worker
``ThreadPoolExecutor`` (``_BROWSER_EXECUTOR``) via the ``_to_browser_thread``
helper, and ``BrowserPool.page()`` asserts the caller's thread matches
the launch thread to make any future regression fail loudly.

This test asserts two complementary properties:

1. **Thread-id guard fires** — calling ``_pool.page()`` directly from a
   non-launching thread raises a clear ``RuntimeError`` mentioning
   ``_to_browser_thread`` (so a future maintainer who reintroduces
   ``asyncio.to_thread`` gets an obvious diagnosis instead of a flaky
   greenlet stack trace at 3am).

2. **Concurrent extracts succeed** — kicking off 8 parallel
   ``extract``-shaped operations through ``_to_browser_thread`` returns
   8 OK results with zero greenlet errors. Pre-fix this would have
   crashed at least one of them on most machines.

Run:
    ~/tools/cloakbrowser/venv/bin/python tests/test_mcp_greenlet_stress.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search import mcp_server as srv
from agent_search.extract import extract_page


# A small, reliable, banner-less URL — keeps the stress test fast and
# isolated from network flakiness on real ad-portals.
TEST_URL = "https://example.com"
CONCURRENCY = 8


# --------------------------------------------------------------- case 1

def case_thread_guard() -> int:
    """Calling _pool.page() from a foreign thread must raise RuntimeError."""
    print("[case_thread_guard] launching browser on the dedicated worker")

    # Make sure the browser is up — submit a no-op via the executor so
    # _start() runs there and records the right thread id.
    def _warmup():
        # Force the lazy launch by acquiring + releasing a page.
        page = srv._pool.page()
        page.close()
        return threading.get_ident()

    fut = srv._BROWSER_EXECUTOR.submit(_warmup)
    launch_tid = fut.result(timeout=30)
    print(f"  browser launched on thread id {launch_tid}")
    print(f"  _pool._browser_thread_id    = {srv._pool._browser_thread_id}")

    if srv._pool._browser_thread_id != launch_tid:
        print("  FAIL: pool didn't record the launching thread id")
        return 1

    # Now call page() from this (main) thread — should raise.
    main_tid = threading.get_ident()
    if main_tid == launch_tid:
        print("  SKIP: main thread happened to be the worker — can't test")
        return 0

    try:
        srv._pool.page()
    except RuntimeError as e:
        msg = str(e)
        if "_to_browser_thread" not in msg:
            print(f"  FAIL: error message doesn't mention _to_browser_thread: {msg!r}")
            return 1
        if str(launch_tid) not in msg or str(main_tid) not in msg:
            print(f"  FAIL: error message doesn't include both thread ids: {msg!r}")
            return 1
        print("  PASS — guard fires with a clear message")
        print(f"    error: {msg}")
        return 0
    except Exception as e:
        print(f"  FAIL: expected RuntimeError, got {type(e).__name__}: {e}")
        return 1
    else:
        print("  FAIL: page() did not raise — guard is broken")
        return 1


# --------------------------------------------------------------- case 2

async def _extract_one(idx: int) -> dict:
    """Mirrors the shape of MCP extract(): submits _run via the helper."""
    def _run():
        page = srv._pool.page()
        try:
            return extract_page(
                page, url=TEST_URL,
                paginate=False, max_scrolls=0,
                include_links=False, include_images=False,
                timeout_ms=20000,
            )
        finally:
            try:
                page.close()
            except Exception:
                pass

    started = time.time()
    try:
        rec = await srv._to_browser_thread(_run)
        rec["_idx"] = idx
        rec["_elapsed_s"] = round(time.time() - started, 2)
        return rec
    except Exception as e:
        return {
            "_idx": idx,
            "status": "error",
            "error": f"{type(e).__name__}: {e}",
            "_elapsed_s": round(time.time() - started, 2),
        }


def case_concurrent_extracts() -> int:
    """Fire CONCURRENCY parallel extracts through _to_browser_thread."""
    print(f"[case_concurrent_extracts] firing {CONCURRENCY} parallel "
          f"extracts at {TEST_URL}")

    async def _go():
        return await asyncio.gather(*[
            _extract_one(i) for i in range(CONCURRENCY)
        ])

    results = asyncio.run(_go())

    failures = 0
    greenlet_errors = 0
    for r in results:
        idx = r.get("_idx")
        status = r.get("status")
        err = r.get("error") or ""
        elapsed = r.get("_elapsed_s")
        wc = r.get("word_count") or 0
        print(f"  [{idx}] status={status!r:<8} word_count={wc:<5} "
              f"elapsed={elapsed}s")
        if status != "ok":
            failures += 1
            print(f"       error: {err}")
            if "greenlet" in err.lower() or "different thread" in err.lower():
                greenlet_errors += 1

    if greenlet_errors:
        print(f"  FAIL: {greenlet_errors} greenlet/thread errors — "
              f"the bug we set out to fix is back")
        return 1
    if failures:
        print(f"  FAIL: {failures} extracts failed (no greenlet errors, "
              f"but still broken)")
        return 1
    print("  PASS — all concurrent extracts completed cleanly")
    return 0


# --------------------------------------------------------------- runner

def main() -> int:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== mcp_server greenlet-stress regression ===\n")
    failures = 0

    for label, fn in [
        ("thread_guard",         case_thread_guard),
        ("concurrent_extracts",  case_concurrent_extracts),
    ]:
        print(f"--- {label} ---")
        try:
            failures += fn()
        except Exception:
            failures += 1
            traceback.print_exc()
        print()

    # Tear down through the dedicated worker, like main() does.
    try:
        srv._BROWSER_EXECUTOR.submit(srv._pool.shutdown).result(timeout=10)
    except Exception as e:
        print(f"  shutdown raised: {e}")
    finally:
        srv._BROWSER_EXECUTOR.shutdown(wait=True, cancel_futures=True)

    print(f"{'PASS' if failures == 0 else f'{failures} FAIL'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
