"""1337x torrent search adapter smoke test.

Steps:
1. Launch a headless browser via core.launch().
2. Run Torrent1337xEngine.search("ubuntu").
3. Assert at least one SearchResult comes back.
4. Print the top 5 results, including the seeders / size extension fields.
5. Close the browser.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_1337x.py
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
from agent_search.engines.torrent_1337x import Torrent1337xEngine


QUERY = "ubuntu"
LIMIT = 10


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== 1337x torrent search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT}")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = Torrent1337xEngine(page)

        results = engine.search(QUERY, limit=LIMIT)

        print(f"\nReturned {len(results)} results")
        if not results:
            print(f"engine.last_status = {engine.last_status!r}")
        assert len(results) > 0, "expected at least one 1337x result"

        print("\n--- Top 5 results ---")
        for i, r in enumerate(results[:5], start=1):
            seeders = getattr(r, "seeders", "—")
            leechers = getattr(r, "leechers", "—")
            size = getattr(r, "size", "") or "—"
            uploader = getattr(r, "uploader", "") or "—"
            print(f"\n[{i}] {r.title}")
            print(f"    URL     : {r.url}")
            print(f"    Seeders : {seeders}")
            print(f"    Leechers: {leechers}")
            print(f"    Size    : {size}")
            print(f"    Uploader: {uploader}")
            snippet = (r.snippet or "").replace("\n", " ")
            if len(snippet) > 200:
                snippet = snippet[:200] + "..."
            print(f"    Snippet : {snippet}")

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
