"""Amazon search adapter test.

What it does:
1. Run AmazonEngine.search("mechanical keyboard") with up to 3 attempts.
2. After each attempt dump diagnostics: page title, page url, host strategy,
   selector counts, block reason (if any) — same shape as test_indeed.py.
3. PASS if at least one result comes back; otherwise FAIL with diagnostics.
4. Print the top 5 results including price, rating, reviews_count, image_url
   and ASIN.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_amazon.py
"""

from __future__ import annotations

import logging
import sys
import time
import traceback

from cloak_stealth_suite import core
from cloak_stealth_suite.engines.amazon import AmazonEngine
from cloak_stealth_suite.stealth.enhance import check_blocked


QUERY = "mechanical keyboard"
LIMIT = 10
MAX_ATTEMPTS = 3


def _attempt(engine: AmazonEngine, attempt: int) -> list:
    """Run a single search attempt and dump diagnostics. Returns the result list."""
    print(f"\n--- attempt {attempt}/{MAX_ATTEMPTS} ---")
    # Bypass BaseEngine.search()'s retry loop so we can dump per-attempt
    # diagnostics, exactly like test_indeed.py.
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

    print(f"  page title  : {title!r}")
    print(f"  page url    : {url}")
    print(f"  strategy    : {engine.last_strategy or '<none>'}")

    counts = engine.selector_counts()
    print("  selector counts:")
    for sel, n in counts.items():
        print(f"    {sel:<60} -> {n}")

    blocked_reason = check_blocked(page)
    if blocked_reason:
        print(f"  check_blocked   : {blocked_reason}")
    if engine.last_status:
        block_reason = engine.last_status.get("block_reason")
        if block_reason:
            print(f"  block_reason    : {block_reason!r}")
        body_len = engine.last_status.get("body_len")
        if body_len is not None:
            print(f"  body length     : {body_len} chars")

    print(f"  results         : {len(results)}")
    return results


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== Amazon search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT} | Max attempts: {MAX_ATTEMPTS}")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = AmazonEngine(page)

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

        assert len(results) > 0, "expected at least one Amazon result"

        print(f"\nReturned {len(results)} results (host: {engine.last_strategy})")
        print("\n--- Top 5 results ---")
        for i, r in enumerate(results[:5], start=1):
            price = getattr(r, "price", "") or "<no price>"
            rating = getattr(r, "rating", "") or "<no rating>"
            reviews_count = getattr(r, "reviews_count", "") or "<no reviews>"
            image_url = getattr(r, "image_url", "") or "<no image>"
            asin = getattr(r, "asin", "") or "<no asin>"

            print(f"\n[{i}] {r.title}")
            print(f"    URL          : {r.url}")
            print(f"    Price        : {price}")
            print(f"    Rating       : {rating}")
            print(f"    Reviews      : {reviews_count}")
            print(f"    Image        : {image_url}")
            print(f"    ASIN         : {asin}")
            snippet = (r.snippet or "").replace("\n", " ")
            if len(snippet) > 200:
                snippet = snippet[:200] + "..."
            print(f"    Snippet      : {snippet}")

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
