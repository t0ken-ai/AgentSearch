"""Yandex search adapter test.

What it checks:
1. Run YandexEngine._do_search("Python web framework", limit=5) with up
   to 3 attempts (bypasses BaseEngine.search retry loop so each
   attempt's diagnostics are visible — same pattern as test_bing.py /
   test_producthunt.py).
2. After every attempt, print:
     - the page title / URL,
     - which mode was last used (yandex_int / yandex_ru),
     - selector counts for the cross-mode result-row selectors,
     - any block_reason / selector / count flag on engine.last_status,
     - check_blocked() reason if any.
3. PASS if at least one result is returned (assert len(results) > 0).
   FAIL with diagnostics if every attempt is captcha'd or returns
   nothing.
4. Print the top 5 results with title / url / snippet.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_yandex.py
"""

from __future__ import annotations

import logging
import sys
import time
import traceback

from cloak_stealth_suite import core
from cloak_stealth_suite.engines.yandex import YandexEngine
from cloak_stealth_suite.stealth.enhance import check_blocked


QUERY = "Python web framework"
LIMIT = 5
MAX_ATTEMPTS = 3


def _attempt(engine: YandexEngine, attempt: int) -> list:
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
    print(f"  last mode  : {engine._last_mode!r}")

    counts = engine.selector_counts()
    print("  selector counts:")
    for sel, n in counts.items():
        print(f"    {sel:<32} -> {n}")

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

    print("=== Yandex search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT} | Max attempts: {MAX_ATTEMPTS}")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = YandexEngine(page)

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
                wait = 5 + attempt * 2
                print(f"  no results -- sleeping {wait}s before retry")
                time.sleep(wait)

        if not results:
            print(
                "\n=== FAIL === no results after all attempts "
                "(yandex_int + yandex_ru both empty)",
                file=sys.stderr,
            )
            return 1

        # Required assertion.
        assert len(results) > 0, "expected at least one Yandex result"

        print(f"\nReturned {len(results)} results")
        print("\n--- Top 5 results ---")
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
