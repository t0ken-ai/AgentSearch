"""Internet Archive search adapter smoke test.

Steps:
1. Launch a headless browser via core.launch().
2. Run ArchiveOrgEngine.search("NASA Apollo").
3. Assert at least one SearchResult comes back.
4. Print the top 5 results with title, URL, mediatype, date and snippet.
5. Close the browser.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_archive_org.py
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
from cloak_stealth_suite.engines.archive_org import ArchiveOrgEngine


QUERY = "NASA Apollo"
LIMIT = 10


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== Internet Archive search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT}")

    cfg = core.BrowserConfig(headless=True, humanize=False)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = ArchiveOrgEngine(page)

        results = engine.search(QUERY, limit=LIMIT)

        print(f"\nReturned {len(results)} results")
        assert len(results) > 0, "expected at least one Internet Archive result"

        # Sanity: every result should have a stable details URL.
        for r in results:
            assert r.url.startswith("https://archive.org/details/"), (
                f"unexpected URL shape: {r.url!r}"
            )

        # Sanity: query terms should appear somewhere in the returned text.
        all_text = " ".join(
            f"{r.title} {r.snippet}" for r in results
        ).lower()
        assert "nasa" in all_text or "apollo" in all_text, (
            "expected 'NASA' or 'Apollo' to appear in IA results for "
            f"query {QUERY!r}; got: {all_text[:300]!r}"
        )

        print("\n--- Top 5 results ---")
        for i, r in enumerate(results[:5], start=1):
            identifier = getattr(r, "identifier", "")
            media_type = getattr(r, "media_type", "")
            date = getattr(r, "date", "")
            creator = getattr(r, "creator", "")

            print(f"\n[{i}] {r.title}")
            print(f"    Identifier : {identifier}")
            print(f"    URL        : {r.url}")
            print(f"    MediaType  : {media_type}")
            print(f"    Date       : {date}")
            if creator:
                print(f"    Creator    : {creator}")
            snippet = (r.snippet or "").replace("\n", " ")
            if len(snippet) > 220:
                snippet = snippet[:220] + "..."
            print(f"    Snippet    : {snippet}")

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
