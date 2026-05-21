"""IMDB title search adapter test.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_imdb.py
"""

from __future__ import annotations

import logging
import os
import sys
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search import core
from agent_search.engines.imdb import ImdbEngine


QUERY = "Inception"
LIMIT = 5


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== IMDB title search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT}")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = ImdbEngine(page)
        results = engine.search(QUERY, limit=LIMIT)

        ls = engine.last_status or {}
        print(f"\nitems_seen: {ls.get('items_seen', 0)}  count: {ls.get('count', 0)}")

        if not results:
            print("\n=== FAIL === no results", file=sys.stderr)
            return 1

        assert len(results) > 0, "expected at least one IMDB result"

        print(f"\nReturned {len(results)} results")
        print("\n--- Top 5 results ---")
        for i, r in enumerate(results[:5], start=1):
            tt = getattr(r, "imdb_id", "") or ""
            year = getattr(r, "year", "") or ""
            ctype = getattr(r, "content_type", "") or ""
            runtime = getattr(r, "runtime", "") or ""
            rating = getattr(r, "rating", "") or ""
            imdb_rating = getattr(r, "imdb_rating", "") or ""
            vote_count = getattr(r, "vote_count", "") or ""
            poster = getattr(r, "image_url", "") or ""
            print(f"\n[{i}] {r.title}")
            print(f"    URL    : {r.url}")
            if poster:
                print(f"    Poster : {poster[:120]}")
            meta = []
            if tt:
                meta.append(tt)
            if year:
                meta.append(year)
            if ctype:
                meta.append(ctype)
            if runtime:
                meta.append(runtime)
            if rating:
                meta.append(rating)
            if imdb_rating:
                meta.append(f"⭐ {imdb_rating}" + (f" ({vote_count})" if vote_count else ""))
            if meta:
                print(f"    Meta   : {' · '.join(meta)}")

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
