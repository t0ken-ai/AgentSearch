"""Pixabay search adapter test.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_pixabay.py
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
from cloak_stealth_suite.engines.pixabay import PixabayEngine


QUERY = "ocean"
LIMIT = 5


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== Pixabay search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT}")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = PixabayEngine(page)
        results = engine.search(QUERY, limit=LIMIT)

        ls = engine.last_status or {}
        print(f"\nAnchors seen: {ls.get('anchors_seen', 0)}")

        if not results:
            print("\n=== FAIL === no results", file=sys.stderr)
            return 1

        assert len(results) > 0, "expected at least one Pixabay result"

        print(f"\nReturned {len(results)} results")
        print("\n--- Top 5 results ---")
        for i, r in enumerate(results[:5], start=1):
            ctype = getattr(r, "content_type", "") or ""
            user = getattr(r, "user", "") or ""
            views = getattr(r, "views", "") or ""
            photo_id = getattr(r, "photo_id", "") or ""
            image_url = getattr(r, "image_url", "") or ""
            alt = getattr(r, "alt_text", "") or ""
            print(f"\n[{i}] {r.title}")
            print(f"    URL    : {r.url}")
            if image_url:
                print(f"    Image  : {image_url[:120]}")
            meta = []
            if ctype:
                meta.append(ctype)
            if photo_id:
                meta.append(f"id={photo_id}")
            if user:
                meta.append(f"by {user}")
            if views:
                meta.append(f"views={views}")
            if meta:
                print(f"    Meta   : {' · '.join(meta)}")
            if alt:
                print(f"    Alt    : {alt[:120]}")

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
