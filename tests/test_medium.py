"""Medium search adapter test.

What it checks:
1. Run MediumSearchEngine._do_search("machine learning tutorial",
   limit=5) with up to 3 attempts (bypasses BaseEngine.search retry
   loop so each attempt's diagnostics are visible — same pattern as
   test_github_search.py / test_hackernews.py).
2. After every attempt, print:
     - the page title / URL,
     - which mode was used (medium_direct / ddg_site),
     - selector counts for the cross-mode selectors so a parsing
       miss is obvious from a single dump,
     - any block_reason / selector / count on engine.last_status,
     - check_blocked() reason if any.
3. PASS if at least one article is returned (assert len(results) > 0).
   FAIL with diagnostics if every attempt is blocked or returns no
   results.
4. Print the top 5 articles with title / url / author / publication /
   read time / claps / member-only badge / published_at.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_medium.py
"""

from __future__ import annotations

import logging
import sys
import time
import traceback

from cloak_stealth_suite import core
from cloak_stealth_suite.engines.medium import MediumSearchEngine
from cloak_stealth_suite.stealth.enhance import check_blocked


QUERY = "machine learning tutorial"
LIMIT = 5
MAX_ATTEMPTS = 3


def _attempt(engine: MediumSearchEngine, attempt: int) -> list:
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
    print(f"  pages got  : {engine._pages_fetched}")

    counts = engine.selector_counts()
    print("  selector counts:")
    for sel, n in counts.items():
        print(f"    {sel:<42} -> {n}")

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
        if ls.get("error"):
            print(f"  last_status error   : {ls.get('error')!r}")

    print(f"  results        : {len(results)}")
    return results


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== Medium search adapter test ===")
    print(
        f"Query: {QUERY!r} | Limit: {LIMIT} | "
        f"Max attempts: {MAX_ATTEMPTS}"
    )

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = MediumSearchEngine(page)

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
                "\n=== FAIL === no articles after all attempts",
                file=sys.stderr,
            )
            return 1

        # Required assertion.
        assert len(results) > 0, "expected at least one Medium result"

        print(f"\nReturned {len(results)} results")
        print("\n--- Top 5 articles ---")
        for i, r in enumerate(results[:5], start=1):
            author = getattr(r, "author", "") or "(unknown)"
            publication = getattr(r, "publication", "") or "(none)"
            read_time = getattr(r, "read_time", "") or "(unknown)"
            claps = getattr(r, "claps", None)
            claps_str = claps if claps is not None else "n/a"
            member_only = getattr(r, "member_only", False)
            published_at = getattr(r, "published_at", "") or "(unknown)"
            print(f"\n[{i}] {r.title}")
            print(f"    URL         : {r.url}")
            print(f"    Author      : {author}")
            print(f"    Publication : {publication}")
            print(f"    Read time   : {read_time}")
            print(f"    Claps       : {claps_str}")
            print(f"    Member-only : {'yes' if member_only else 'no'}")
            print(f"    Published   : {published_at}")
            if r.snippet:
                snip = r.snippet.replace("\n", " ")
                if len(snip) > 200:
                    snip = snip[:200] + "..."
                print(f"    Snippet     : {snip}")

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
