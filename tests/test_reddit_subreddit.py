"""Reddit subreddit JSON-API adapter test.

What it checks:
1. Run RedditSubredditEngine.fetch("Python", limit=5, sort="hot") with
   up to 3 attempts, bypassing BaseEngine.search retry loop so that
   per-attempt diagnostics are visible.
2. After every attempt, print:
     - the page title / URL,
     - body length / host / sort the engine landed on,
     - any block_reason on engine.last_status,
     - check_blocked() reason if any.
3. PASS if at least one result is returned (assert len(results) > 0).
4. Print the top 5 results with title / score / num_comments / author.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_reddit_subreddit.py
"""

from __future__ import annotations

import logging
import os
import sys
import time
import traceback

# Make sure the AgentSearch project root wins over any older editable
# install of agent_search that might be registered in
# site-packages.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search import core
from agent_search.engines.reddit_subreddit import RedditSubredditEngine
from agent_search.stealth.enhance import check_blocked


SUBREDDIT = "Python"
LIMIT = 5
SORT = "hot"
MAX_ATTEMPTS = 3


def _attempt(engine: RedditSubredditEngine, attempt: int) -> list:
    print(f"\n--- attempt {attempt}/{MAX_ATTEMPTS} ---")
    engine.sort = SORT
    results = engine._do_search(SUBREDDIT, LIMIT)

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

    blocked_reason = check_blocked(page)
    if blocked_reason:
        print(f"  check_blocked  : {blocked_reason}")

    if engine.last_status:
        ls = engine.last_status
        if ls.get("host"):
            print(f"  host           : {ls.get('host')}")
        if ls.get("sort"):
            print(f"  sort           : {ls.get('sort')}")
        if ls.get("body_len") is not None:
            print(f"  body length    : {ls.get('body_len')} chars")
        if ls.get("block_reason"):
            print(f"  block_reason   : {ls.get('block_reason')!r}")
        if ls.get("count") is not None:
            print(f"  parsed count   : {ls.get('count')}")

    print(f"  results        : {len(results)}")
    return results


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== Reddit subreddit JSON adapter test ===")
    print(
        f"Subreddit: r/{SUBREDDIT} | Sort: {SORT} | "
        f"Limit: {LIMIT} | Max attempts: {MAX_ATTEMPTS}"
    )

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = RedditSubredditEngine(page)

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
            print(
                "\n=== FAIL === no results after all attempts",
                file=sys.stderr,
            )
            return 1

        assert len(results) > 0, "expected at least one Reddit post"

        print(f"\nReturned {len(results)} results")
        print("\n--- Top 5 posts ---")
        for i, r in enumerate(results[:5], start=1):
            author = getattr(r, "author", "") or ""
            score = getattr(r, "score", None)
            num_comments = getattr(r, "num_comments", None)
            sub = getattr(r, "subreddit", "") or ""
            print(f"\n[{i}] {r.title}")
            print(f"    URL    : {r.url}")
            if sub:
                print(f"    Sub    : {sub}")
            if author:
                print(f"    Author : u/{author}")
            extra: list[str] = []
            if score is not None:
                extra.append(f"score={score}")
            if num_comments is not None:
                extra.append(f"comments={num_comments}")
            if extra:
                print(f"    Stats  : {' | '.join(extra)}")
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
