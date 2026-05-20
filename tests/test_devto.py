"""dev.to search adapter smoke test.

Steps:
1. Launch a headless browser via core.launch().
2. Run DevToEngine.search("rust async").
3. Assert at least one SearchResult comes back, and that titles / URLs /
   author / tags fields are populated.
4. Print the top 5 results with author, tags, reactions and reading time.
5. Close the browser.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_devto.py
"""

from __future__ import annotations

import logging
import os
import sys
import traceback

# Make sure the AgentSearch project root wins over any older editable install
# of `cloak_stealth_suite` that might be registered in site-packages.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from cloak_stealth_suite import core
from cloak_stealth_suite.engines.devto import DevToEngine


QUERY = "rust async"
LIMIT = 10


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== dev.to search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT}")

    cfg = core.BrowserConfig(headless=True, humanize=False)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = DevToEngine(page)

        results = engine.search(QUERY, limit=LIMIT)

        print(f"\nReturned {len(results)} results")
        assert len(results) > 0, "expected at least one dev.to result"

        # Sanity checks.
        for r in results:
            assert r.title, f"missing title: {r!r}"
            assert r.url.startswith("https://dev.to/"), (
                f"unexpected URL shape: {r.url!r}"
            )

        # At least one result should expose author and tags metadata.
        with_author = [r for r in results if getattr(r, "author", "")]
        with_tags = [r for r in results if getattr(r, "tags", [])]
        assert with_author, "expected at least one result to have an author"
        assert with_tags, "expected at least one result to have tags"

        print("\n--- Top 5 results ---")
        for i, r in enumerate(results[:5], start=1):
            author = getattr(r, "author", "")
            tags = getattr(r, "tags", [])
            reactions = getattr(r, "reactions_count", 0)
            reading_time = getattr(r, "reading_time", 0)

            print(f"\n[{i}] {r.title}")
            print(f"    URL          : {r.url}")
            print(f"    Author       : {author or '(none)'}")
            print(f"    Tags         : {', '.join(tags) if tags else '(none)'}")
            print(f"    Reactions    : {reactions}")
            print(f"    Reading time : {reading_time} min")

            snippet = (r.snippet or "").replace("\n", " ")
            if len(snippet) > 220:
                snippet = snippet[:220] + "..."
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
