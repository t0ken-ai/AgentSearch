"""Stack Overflow search adapter test.

What it checks:
1. Run StackOverflowEngine._do_search("python async await", limit=5) with
   up to 3 attempts (bypasses BaseEngine.search retry loop so each
   attempt's diagnostics are visible — same pattern as test_reddit.py /
   test_blackhatworld.py).
2. After every attempt, print:
     - the page title / URL,
     - which DOM layout was used (modern / legacy / empty),
     - selector counts for both layouts (modern + legacy + key
       sub-selectors + ``a[rel='next']`` to verify pagination is wired),
     - any block_reason / selector / count / pages_fetched on
       engine.last_status,
     - check_blocked() reason if any.
3. PASS if at least one question is returned (assert len(results) > 0).
   FAIL with diagnostics if every attempt is blocked or returns no
   results.
4. Print the top 5 questions with title / url / votes / tags / excerpt.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_stackoverflow.py
"""

from __future__ import annotations

import logging
import re
import sys
import time
import traceback

from cloak_stealth_suite import core
from cloak_stealth_suite.engines.stackoverflow import StackOverflowEngine
from cloak_stealth_suite.stealth.enhance import check_blocked


QUERY = "python async await"
LIMIT = 5
MAX_ATTEMPTS = 3


def _split_snippet(snippet: str) -> tuple[list[str], str]:
    """Pull the leading ``[tag1, tag2] ·`` chunk out of the snippet field.

    Returns ``(tags, excerpt)`` so the test output can show tags / excerpt
    separately even though the adapter folds them into a single field.
    """
    if not snippet:
        return [], ""
    m = re.match(r"^\s*\[([^\]]*)\]\s*(?:·\s*)?(.*)$", snippet, re.DOTALL)
    if not m:
        return [], snippet
    raw_tags = m.group(1)
    excerpt = m.group(2).strip()
    tags = [t.strip() for t in raw_tags.split(",") if t.strip()]
    return tags, excerpt


def _attempt(engine: StackOverflowEngine, attempt: int) -> list:
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
    print(f"  layout used: {engine._last_layout!r}")
    print(f"  pages got  : {engine._pages_fetched}")

    counts = engine.selector_counts()
    print("  selector counts:")
    for sel, n in counts.items():
        print(f"    {sel:<58} -> {n}")

    blocked_reason = check_blocked(page)
    if blocked_reason:
        print(f"  check_blocked  : {blocked_reason}")

    if engine.last_status:
        ls = engine.last_status
        if ls.get("layout"):
            print(f"  last_status layout  : {ls.get('layout')}")
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
        if ls.get("api_quota_remaining") is not None:
            print(f"  api quota left : {ls.get('api_quota_remaining')}")
        if ls.get("api_has_more") is not None:
            print(f"  api has_more   : {ls.get('api_has_more')}")
        if ls.get("mode"):
            print(f"  last_status mode    : {ls.get('mode')}")

    print(f"  results        : {len(results)}")
    return results


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== Stack Overflow search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT} | Max attempts: {MAX_ATTEMPTS}")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = StackOverflowEngine(page)

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
                "\n=== FAIL === no questions after all attempts",
                file=sys.stderr,
            )
            return 1

        # Required assertion.
        assert len(results) > 0, "expected at least one Stack Overflow result"

        print(f"\nReturned {len(results)} results")
        print("\n--- Top 5 questions ---")
        for i, r in enumerate(results[:5], start=1):
            tags, excerpt = _split_snippet(r.snippet or "")
            votes = r.score if r.score is not None else "n/a"
            tag_str = ", ".join(tags) if tags else "(none)"
            print(f"\n[{i}] {r.title}")
            print(f"    URL    : {r.url}")
            print(f"    Votes  : {votes}")
            print(f"    Tags   : {tag_str}")
            short_excerpt = excerpt.replace("\n", " ")
            if len(short_excerpt) > 240:
                short_excerpt = short_excerpt[:240] + "..."
            print(f"    Excerpt: {short_excerpt}")

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
