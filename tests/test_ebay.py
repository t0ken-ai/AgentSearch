"""eBay search adapter test.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_ebay.py
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
from agent_search.engines.ebay import EbayEngine


QUERY = "mechanical keyboard"
LIMIT = 5


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== eBay search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT}")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = EbayEngine(page)
        results = engine.search(QUERY, limit=LIMIT)

        ls = engine.last_status or {}
        print(f"\nlis_seen: {ls.get('lis_seen', 0)}  count: {ls.get('count', 0)}")

        if not results:
            print("\n=== FAIL === no results", file=sys.stderr)
            return 1

        assert len(results) > 0, "expected at least one eBay result"

        print(f"\nReturned {len(results)} results")
        print("\n--- Top 5 results ---")
        for i, r in enumerate(results[:5], start=1):
            iid = getattr(r, "item_id", "") or ""
            price = getattr(r, "price", "") or ""
            cond = getattr(r, "condition", "") or ""
            shipping = getattr(r, "shipping", "") or ""
            location = getattr(r, "location", "") or ""
            seller = getattr(r, "seller", "") or ""
            feedback = getattr(r, "feedback", "") or ""
            img = getattr(r, "image_url", "") or ""
            print(f"\n[{i}] {r.title}")
            print(f"    URL    : {r.url}")
            if img:
                print(f"    Image  : {img[:120]}")
            meta = []
            if iid:
                meta.append(f"id={iid}")
            if price:
                meta.append(price)
            if cond:
                meta.append(cond)
            if shipping:
                meta.append(shipping)
            if location:
                meta.append(location)
            if seller:
                meta.append(f"by {seller}")
            if feedback:
                meta.append(feedback)
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
