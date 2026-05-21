"""Wolfram|Alpha search adapter test.

What it checks:
1. Run WolframEngine.search("population of China") with up to 3 attempts.
2. After every attempt, dump the page title / URL, selector counts, and
   a "block_reason" if Wolfram showed one — so failure modes are obvious.
3. PASS if at least one pod (e.g. "Input interpretation" / "Result") is
   returned; print every pod (title + URL + snippet).

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_wolfram.py
"""

from __future__ import annotations

import logging
import os
import sys
import time
import traceback

# Make sure the AgentSearch project root wins over any older editable install
# of `agent_search` that might be registered in site-packages.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search import core
from agent_search.engines.wolfram import WolframEngine
from agent_search.stealth.enhance import check_blocked


QUERY = "population of China"
LIMIT = 10
MAX_ATTEMPTS = 3


def _attempt(engine: WolframEngine, attempt: int) -> list:
    """Run one search and dump diagnostics. Returns the result list."""
    print(f"\n--- attempt {attempt}/{MAX_ATTEMPTS} ---")
    # Bypass BaseEngine retry loop here so we can print diagnostics each time.
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
        block_reason = engine.last_status.get("block_reason")
        if block_reason:
            print(f"  block_reason   : {block_reason!r}")
        print(f"  body length    : {engine.last_status.get('body_len')} chars")

    print(f"  results        : {len(results)}")
    return results


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== Wolfram|Alpha search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT} | Max attempts: {MAX_ATTEMPTS}")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = WolframEngine(page)

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

        # Required assertion: page loaded and at least one pod parsed.
        assert len(results) > 0, "expected at least one Wolfram|Alpha pod"

        # Sanity check that this looks like the right query — Wolfram echoes
        # the query in the "Input interpretation" pod.
        all_text = " ".join(
            f"{r.title} {r.snippet}" for r in results
        ).lower()
        assert "china" in all_text, (
            "expected 'China' to appear somewhere in Wolfram's output for "
            f"query {QUERY!r}; got: {all_text[:300]!r}"
        )

        print(f"\nReturned {len(results)} pods")
        print("\n--- All pods ---")
        for i, r in enumerate(results, start=1):
            print(f"\n[{i}] {r.title}")
            print(f"    URL    : {r.url}")
            snippet = (r.snippet or "").replace("\n", " ")
            if len(snippet) > 280:
                snippet = snippet[:280] + "..."
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
