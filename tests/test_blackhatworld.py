"""BlackHatWorld search adapter test.

What it checks:
1. Run BlackHatWorldEngine._do_search("SEO tools", limit=5) with up to 3
   attempts (bypasses BaseEngine.search retry loop so each attempt's
   diagnostics are visible).
2. After every attempt, print:
     - the page title / URL,
     - which mode was last attempted (bhw_direct / google_site / ddg_site),
     - selector counts for that mode plus generic diagnostics
       (h3.contentRow-title a, .contentRow-snippet, a.username,
        #search h3, #rso h3, .result__a),
     - any block_reason / selector / count set on engine.last_status.
3. PASS if at least one post is returned (assert len(results) > 0).
   FAIL with diagnostics if every mode is blocked or returns no results.
4. Print the top 5 posts with title / url / snippet.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_blackhatworld.py
"""

from __future__ import annotations

import logging
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
import time
import traceback

from cloak_stealth_suite import core
from cloak_stealth_suite.engines.blackhatworld import BlackHatWorldEngine
from cloak_stealth_suite.stealth.enhance import check_blocked


QUERY = "SEO tools"
LIMIT = 5
MAX_ATTEMPTS = 3


def _attempt(engine: BlackHatWorldEngine, attempt: int) -> list:
    """Run a single search attempt and dump diagnostics. Returns results."""
    print(f"\n--- attempt {attempt}/{MAX_ATTEMPTS} ---")
    results = engine._do_search(QUERY, LIMIT)  # bypass BaseEngine retry loop

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
    print(f"  last mode  : {engine._last_mode}")

    counts = engine.selector_counts()
    print("  selector counts:")
    for sel, n in counts.items():
        print(f"    {sel:<48} -> {n}")

    blocked_reason = check_blocked(page)
    if blocked_reason:
        print(f"  check_blocked  : {blocked_reason}")

    if engine.last_status:
        ls = engine.last_status
        if ls.get("mode"):
            print(f"  last_status mode    : {ls.get('mode')}")
        if ls.get("selector"):
            print(f"  last_status selector: {ls.get('selector')}")
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

    print("=== BlackHatWorld search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT} | Max attempts: {MAX_ATTEMPTS}")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = BlackHatWorldEngine(page)

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
                wait = 5 + attempt * 3
                print(f"  no results -- sleeping {wait}s before retry")
                time.sleep(wait)

        if not results:
            print(
                "\n=== FAIL === no posts after all attempts "
                "(BHW direct + Google site + DDG site all empty)",
                file=sys.stderr,
            )
            return 1

        # Required assertion.
        assert len(results) > 0, "expected at least one BlackHatWorld post"

        print(f"\nReturned {len(results)} results")
        print("\n--- Top 5 posts ---")
        for i, r in enumerate(results[:5], start=1):
            print(f"\n[{i}] {r.title}")
            print(f"    URL    : {r.url}")
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
