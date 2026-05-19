"""GitHub search adapter test.

What it checks:
1. Run GitHubSearchEngine._do_search("headless browser automation",
   limit=5) with up to 3 attempts (bypasses BaseEngine.search retry
   loop so each attempt's diagnostics are visible — same pattern as
   test_hackernews.py / test_stackoverflow.py).
2. After every attempt, print:
     - the page title / URL,
     - which mode was used (gh_api / gh_direct / ddg_site),
     - selector counts for the cross-mode selectors so a parsing
       miss is obvious from a single dump,
     - any block_reason / selector / count / pages_fetched /
       api_total_count / api_incomplete on engine.last_status,
     - check_blocked() reason if any.
3. PASS if at least one repository is returned (assert len(results) > 0).
   FAIL with diagnostics if every attempt is blocked or returns no
   results.
4. Print the top 5 repositories with title (full_name) / url / stars /
   language / description / forks.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_github_search.py
"""

from __future__ import annotations

import logging
import sys
import time
import traceback

from cloak_stealth_suite import core
from cloak_stealth_suite.engines.github_search import GitHubSearchEngine
from cloak_stealth_suite.stealth.enhance import check_blocked


QUERY = "headless browser automation"
LIMIT = 5
MAX_ATTEMPTS = 3
SEARCH_TYPE = "repositories"


def _attempt(engine: GitHubSearchEngine, attempt: int) -> list:
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
    print(f"  search type: {engine.search_type!r}")
    print(f"  last mode  : {engine._last_mode!r}")
    print(f"  pages got  : {engine._pages_fetched}")
    print(f"  api token? : {'yes' if engine.api_token else 'no (anonymous)'}")

    counts = engine.selector_counts()
    print("  selector counts:")
    for sel, n in counts.items():
        print(f"    {sel:<58} -> {n}")

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
        if ls.get("pages_fetched") is not None:
            print(f"  pages_fetched  : {ls.get('pages_fetched')}")
        if ls.get("api_total_count") is not None:
            print(f"  api total      : {ls.get('api_total_count')}")
        if ls.get("api_incomplete") is not None:
            print(f"  api incomplete : {ls.get('api_incomplete')}")
        if ls.get("error"):
            print(f"  last_status error   : {ls.get('error')!r}")

    print(f"  results        : {len(results)}")
    return results


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== GitHub search adapter test ===")
    print(
        f"Query: {QUERY!r} | Type: {SEARCH_TYPE} | "
        f"Limit: {LIMIT} | Max attempts: {MAX_ATTEMPTS}"
    )

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = GitHubSearchEngine(page, search_type=SEARCH_TYPE)

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
                "\n=== FAIL === no repositories after all attempts",
                file=sys.stderr,
            )
            return 1

        # Required assertion.
        assert len(results) > 0, "expected at least one GitHub result"

        print(f"\nReturned {len(results)} results")
        print("\n--- Top 5 repositories ---")
        for i, r in enumerate(results[:5], start=1):
            stars = r.score if r.score is not None else "n/a"
            language = getattr(r, "language", "") or "(unknown)"
            forks = getattr(r, "forks", None)
            forks_str = forks if forks is not None else "n/a"
            full_name = getattr(r, "full_name", "") or "(none)"
            owner = getattr(r, "owner", "") or "(none)"
            updated_at = getattr(r, "updated_at", "") or "(unknown)"
            description = (getattr(r, "description", "") or "").replace(
                "\n", " "
            )
            if len(description) > 200:
                description = description[:200] + "..."
            print(f"\n[{i}] {r.title}")
            print(f"    URL        : {r.url}")
            print(f"    Full name  : {full_name}")
            print(f"    Owner      : {owner}")
            print(f"    Stars      : {stars}")
            print(f"    Forks      : {forks_str}")
            print(f"    Language   : {language}")
            print(f"    Updated    : {updated_at}")
            print(f"    Description: {description}")

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
