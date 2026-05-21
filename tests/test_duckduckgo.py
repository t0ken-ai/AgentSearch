"""DuckDuckGo search adapter smoke test.

Steps:
1. Launch headless browser via core.launch().
2. Run DuckDuckGoEngine.search("Python programming").
3. Assert at least one SearchResult comes back.
4. Print the first 3 results.
5. Close the browser.
"""

from __future__ import annotations

import logging
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
import traceback

from agent_search import core
from agent_search.engines.duckduckgo import DuckDuckGoEngine


QUERY = "Python programming"
LIMIT = 5


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== DuckDuckGo search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT}")

    cfg = core.BrowserConfig(headless=True, humanize=False)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = DuckDuckGoEngine(page)

        results = engine.search(QUERY, limit=LIMIT)

        print(f"\nReturned {len(results)} results")
        assert len(results) > 0, "expected at least one DuckDuckGo result"

        print("\n--- Top 3 results ---")
        for i, r in enumerate(results[:3], start=1):
            print(f"\n[{i}] {r.title}")
            print(f"    URL    : {r.url}")
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
