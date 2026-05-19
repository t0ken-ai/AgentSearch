"""Reddit search adapter test.

What it checks:
1. Run RedditEngine.search("Python") with up to 3 attempts.
2. After every attempt, print the count for every result selector
   (.search-result-link variants + .thing fallback selectors + a.search-title /
   .search-score / .thing).
3. If the page got rate-limited / shows a Cloudflare gate / login wall,
   print the page title and URL so the failure mode is obvious.
4. PASS if at least one post is returned; otherwise FAIL with diagnostics.
5. Print the top 5 posts with title / url / score / snippet.
"""

from __future__ import annotations

import logging
import sys
import time
import traceback

from cloak_stealth_suite import core
from cloak_stealth_suite.engines.reddit import RedditEngine
from cloak_stealth_suite.stealth.enhance import check_blocked


QUERY = "Python"
LIMIT = 5
MAX_ATTEMPTS = 3


def _attempt(engine: RedditEngine, attempt: int) -> list:
    """Run a single search attempt and dump diagnostics. Returns the result list."""
    print(f"\n--- attempt {attempt}/{MAX_ATTEMPTS} ---")
    results = engine._do_search(QUERY, LIMIT)  # bypass BaseEngine retry loop here

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
        print(
            f"  body length    : {engine.last_status.get('body_len')} chars"
        )

    print(f"  results        : {len(results)}")
    return results


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== Reddit search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT} | Max attempts: {MAX_ATTEMPTS}")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = RedditEngine(page)

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
            print("\n=== FAIL === no results after all attempts", file=sys.stderr)
            return 1

        # Required assertion.
        assert len(results) > 0, "expected at least one Reddit post"

        print(f"\nReturned {len(results)} results")
        print("\n--- Top 5 posts ---")
        for i, r in enumerate(results[:5], start=1):
            print(f"\n[{i}] {r.title}")
            print(f"    URL    : {r.url}")
            score = r.score if r.score is not None else "n/a"
            print(f"    Score  : {score}")
            snippet = (r.snippet or "").replace("\n", " ")
            if len(snippet) > 200:
                snippet = snippet[:200] + "..."
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
