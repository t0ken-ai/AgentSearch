"""Qwant adapter test.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_qwant.py

Note: Qwant's hosts are sometimes filtered (DNS poisoning / TCP timeouts)
from networks behind regional firewalls. The test does a TCP preflight to
``www.qwant.com:443`` and, if unreachable, prints SKIP and exits 0 — the
engine module still imports correctly. Re-run from a network where Qwant
is reachable to exercise the full SERP path.
"""

from __future__ import annotations

import logging
import os
import socket
import sys
import time
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search import core
from agent_search.engines.qwant import QwantEngine
from agent_search.stealth.enhance import check_blocked


QUERY = "open source"
LIMIT = 5
MAX_ATTEMPTS = 3


def _qwant_reachable(timeout: float = 4.0) -> bool:
    """TCP-preflight to www.qwant.com:443.

    We only probe the exact host the engine navigates to. Some networks
    let bare ``qwant.com:443`` connect (it lands on a CDN edge that
    doesn't even serve Qwant's cert) while filtering ``www.qwant.com``
    at the TCP layer — which previously made the preflight pass while
    Chromium then hit ``ERR_CONNECTION_TIMED_OUT``.
    """
    try:
        with socket.create_connection(("www.qwant.com", 443), timeout=timeout):
            return True
    except OSError:
        return False


def _attempt(engine: QwantEngine, attempt: int) -> list:
    print(f"\n--- attempt {attempt}/{MAX_ATTEMPTS} ---")
    results = engine._do_search(QUERY, LIMIT)

    page = engine.page
    try:
        title = page.title()
    except Exception as e:
        title = f"<title err: {e}>"
    try:
        url = page.url
    except Exception as e:
        url = f"<url err: {e}>"

    print(f"  page title : {title!r}")
    print(f"  page url   : {url}")

    counts = engine.selector_counts()
    print("  selector counts:")
    for sel, n in counts.items():
        print(f"    {sel:<48} -> {n}")

    blocked_reason = check_blocked(page)
    if blocked_reason:
        print(f"  check_blocked  : {blocked_reason}")

    if engine.last_status:
        ls = engine.last_status
        if ls.get("selector"):
            print(f"  selector hit   : {ls.get('selector')}")
        if ls.get("block_reason"):
            print(f"  block_reason   : {ls.get('block_reason')!r}")
        if ls.get("body_len") is not None:
            print(f"  body length    : {ls.get('body_len')} chars")
        if ls.get("count") is not None:
            print(f"  parsed count   : {ls.get('count')}")

    print(f"  results        : {len(results)}")
    return results


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== Qwant search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT} | Max attempts: {MAX_ATTEMPTS}")

    # Preflight: Qwant hosts are commonly filtered from networks behind
    # regional firewalls (DNS poisoning + TCP-level blocking).
    print("Preflight: probing www.qwant.com:443 ...")
    if not _qwant_reachable():
        print("\n=== SKIP ===")
        print(
            "Qwant TCP preflight failed (host unreachable). "
            "The engine module still imports correctly; rerun on a "
            "network where Qwant is reachable to exercise the SERP path."
        )
        # Still load the engine so the module is exercised.
        try:
            from agent_search.engines.qwant import (
                QwantEngine as _Q,
            )  # noqa: F401
            print("Module import: OK")
        except Exception as e:
            print(f"Module import: FAIL ({e})", file=sys.stderr)
            return 1
        return 0

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = QwantEngine(page)

        results: list = []
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                results = _attempt(engine, attempt)
            except Exception:
                print(f"  attempt {attempt} raised:")
                traceback.print_exc()
                results = []
            if results:
                break
            if attempt < MAX_ATTEMPTS:
                wait = 4 + attempt * 2
                print(f"  no results -- sleeping {wait}s before retry")
                time.sleep(wait)

        if not results:
            print("\n=== FAIL === no results after all attempts", file=sys.stderr)
            return 1

        assert len(results) > 0, "expected at least one Qwant result"

        print(f"\nReturned {len(results)} results")
        print("\n--- Top 5 results ---")
        for i, r in enumerate(results[:5], start=1):
            display_url = getattr(r, "display_url", "") or ""
            print(f"\n[{i}] {r.title}")
            print(f"    URL    : {r.url}")
            if display_url:
                print(f"    Display: {display_url}")
            snippet = (r.snippet or "").replace("\n", " ")
            if len(snippet) > 240:
                snippet = snippet[:240] + "..."
            print(f"    Snippet: {snippet}")

        print("\n=== PASS ===")
        return 0
    except AssertionError as e:
        print(f"\n=== FAIL === assertion: {e}", file=sys.stderr)
        return 1
    except Exception:
        print("\n=== FAIL === unexpected exception:", file=sys.stderr)
        traceback.print_exc()
        return 1
    finally:
        try:
            browser.close()
        except Exception as e:
            print(f"warning: browser.close() raised: {e}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
