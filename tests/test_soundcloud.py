"""SoundCloud search adapter test.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_soundcloud.py
"""

from __future__ import annotations

import logging
import os
import sys
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from cloak_stealth_suite import core
from cloak_stealth_suite.engines.soundcloud import SoundCloudEngine


QUERY = "lo-fi"
LIMIT = 5


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== SoundCloud search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT}")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = SoundCloudEngine(page)
        results = engine.search(QUERY, limit=LIMIT)

        ls = engine.last_status or {}
        print(f"\nlis_seen: {ls.get('lis_seen', 0)}  count: {ls.get('count', 0)}")

        if not results:
            print("\n=== FAIL === no results", file=sys.stderr)
            return 1

        assert len(results) > 0, "expected at least one SoundCloud result"

        print(f"\nReturned {len(results)} results")
        print("\n--- Top 5 results ---")
        for i, r in enumerate(results[:5], start=1):
            user = getattr(r, "user", "") or ""
            plays = getattr(r, "plays", "") or ""
            comments = getattr(r, "comments", "") or ""
            posted = getattr(r, "posted", "") or ""
            genre = getattr(r, "genre", "") or ""
            print(f"\n[{i}] {r.title}")
            print(f"    URL    : {r.url}")
            meta = []
            if user:
                meta.append(f"by {user}")
            if plays:
                meta.append(f"▶ {plays}")
            if comments:
                meta.append(f"💬 {comments}")
            if posted:
                meta.append(posted)
            if genre:
                meta.append(genre)
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
