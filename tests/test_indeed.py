"""Indeed search adapter test.

What it does:
1. Run IndeedEngine.search("python developer") with up to 3 attempts.
2. After each attempt dump diagnostics: page title, page url, selector
   counts, block reason (if any) — same shape as test_linkedin_jobs.py.
3. PASS if at least one result comes back; otherwise FAIL with diagnostics.
4. Print the top 5 results including company, location, salary, URL.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_indeed.py
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

from agent_search import core
from agent_search.engines.indeed import IndeedEngine
from agent_search.stealth.enhance import check_blocked


QUERY = "python developer"
LIMIT = 10
MAX_ATTEMPTS = 3


def _attempt(engine: IndeedEngine, attempt: int) -> list:
    """Run a single search attempt and dump diagnostics. Returns the result list."""
    print(f"\n--- attempt {attempt}/{MAX_ATTEMPTS} ---")
    # Bypass BaseEngine.search()'s retry loop so we can dump per-attempt
    # diagnostics, exactly like test_linkedin_jobs.py.
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
        print(f"    {sel:<48} -> {n}")

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

    print("=== Indeed search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT} | Max attempts: {MAX_ATTEMPTS}")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = IndeedEngine(page)

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

        assert len(results) > 0, "expected at least one Indeed result"

        print(f"\nReturned {len(results)} results (strategy: {engine.last_strategy})")
        print("\n--- Top 5 results ---")
        for i, r in enumerate(results[:5], start=1):
            company = getattr(r, "company", "") or "<no company>"
            location = getattr(r, "location", "") or "<no location>"
            salary = getattr(r, "salary", "") or "<no salary>"
            jk = getattr(r, "jk", "") or "<no jk>"

            print(f"\n[{i}] {r.title}")
            print(f"    Company  : {company}")
            print(f"    Location : {location}")
            print(f"    Salary   : {salary}")
            print(f"    JK       : {jk}")
            print(f"    URL      : {r.url}")
            snippet = (r.snippet or "").replace("\n", " ")
            if len(snippet) > 220:
                snippet = snippet[:220] + "..."
            print(f"    Snippet  : {snippet}")

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
