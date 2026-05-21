"""Pexels search adapter test.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_pexels.py
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
from agent_search.engines.pexels import PexelsEngine


QUERY = "sunset"
LIMIT = 5


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== Pexels search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT}")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = PexelsEngine(page)
        results = engine.search(QUERY, limit=LIMIT)

        ls = engine.last_status or {}
        print(f"\nArticles seen: {ls.get('articles_seen', 0)}")

        if not results:
            print("\n=== FAIL === no results", file=sys.stderr)
            return 1

        assert len(results) > 0, "expected at least one Pexels result"

        print(f"\nReturned {len(results)} results")
        print("\n--- Top 5 results ---")
        for i, r in enumerate(results[:5], start=1):
            photographer = getattr(r, "photographer", "") or ""
            slug = getattr(r, "photographer_slug", "") or ""
            photo_id = getattr(r, "photo_id", "") or ""
            image_url = getattr(r, "image_url", "") or ""
            alt = getattr(r, "alt_text", "") or ""
            print(f"\n[{i}] {r.title}")
            print(f"    URL    : {r.url}")
            if image_url:
                print(f"    Image  : {image_url[:120]}")
            meta = []
            if photographer:
                meta.append(f"by {photographer}")
            if slug:
                meta.append(f"slug={slug}")
            if photo_id:
                meta.append(f"id={photo_id}")
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
