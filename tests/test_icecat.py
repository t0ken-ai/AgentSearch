"""Icecat search adapter smoke test.

What it does:
1. Run IcecatEngine.search("iPhone 16") with up to 3 attempts (the SPA is
   slow on first paint so we leave the BaseEngine retry loop in place).
2. Assert at least one SearchResult comes back.
3. Print the top 5 results — title, URL, brand, category, image, specs,
   product_code, icecat_id — so it doubles as a scrape sanity-check.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_icecat.py
"""

from __future__ import annotations

import logging
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
import time
import traceback

from agent_search import core
from agent_search.engines.icecat import IcecatEngine
from agent_search.stealth.enhance import check_blocked


QUERY = "iPhone 16"
LIMIT = 10
MAX_ATTEMPTS = 3


def _attempt(engine: IcecatEngine, attempt: int) -> list:
    """Run a single search attempt and dump diagnostics. Returns the result list."""
    print(f"\n--- attempt {attempt}/{MAX_ATTEMPTS} ---")
    # Bypass BaseEngine.search()'s retry loop so we can dump per-attempt
    # diagnostics, exactly like test_amazon.py.
    results = engine._do_search(QUERY, LIMIT)

    page = engine.page
    try:
        title = page.title()
    except Exception as e:
        title = f"<title err: {e}>"
    try:
        url = page.url
    except Exception as e:
        url = f"<url err: {e}>"

    print(f"  page title  : {title!r}")
    print(f"  page url    : {url}")

    # Count selectors so we can tell whether the SPA mounted.
    for sel in (
        "[class*='mainPart']",
        "[class*='product-item']",
        "a[class*='descriptionTitle']",
        "a[href*='/p/']",
    ):
        try:
            n = page.evaluate("(s) => document.querySelectorAll(s).length", sel)
        except Exception as e:
            n = f"<err {e}>"
        print(f"    {sel:<40s} -> {n}")

    blocked_reason = check_blocked(page)
    if blocked_reason:
        print(f"  check_blocked   : {blocked_reason}")

    print(f"  results         : {len(results)}")
    return results


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== Icecat search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT} | Max attempts: {MAX_ATTEMPTS}")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = IcecatEngine(page)

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
            print("\n=== FAIL === no results after all attempts", file=sys.stderr)
            return 1

        assert len(results) > 0, "expected at least one Icecat result"

        print(f"\nReturned {len(results)} results")
        print("\n--- Top 5 results ---")
        for i, r in enumerate(results[:5], start=1):
            brand = getattr(r, "brand", "") or "<no brand>"
            category = getattr(r, "category", "") or "<no category>"
            image_url = getattr(r, "image_url", "") or "<no image>"
            specs = getattr(r, "specs", "") or "<no specs>"
            product_code = getattr(r, "product_code", "") or "<no code>"
            icecat_id = getattr(r, "icecat_id", "") or "<no id>"

            print(f"\n[{i}] {r.title}")
            print(f"    URL          : {r.url}")
            print(f"    Brand        : {brand}")
            print(f"    Category     : {category}")
            print(f"    Product Code : {product_code}")
            print(f"    Icecat ID    : {icecat_id}")
            print(f"    Image        : {image_url}")
            specs_clean = specs.replace("\n", " ")
            if len(specs_clean) > 200:
                specs_clean = specs_clean[:200] + "..."
            print(f"    Specs        : {specs_clean}")

        # The task asks specifically for brand + category in the printout —
        # also assert at least one result actually has those fields populated.
        with_brand = sum(1 for r in results if getattr(r, "brand", ""))
        with_cat = sum(1 for r in results if getattr(r, "category", ""))
        print(f"\n  results with brand    : {with_brand}/{len(results)}")
        print(f"  results with category : {with_cat}/{len(results)}")
        assert with_brand > 0, "expected at least one result to have a brand"
        assert with_cat > 0, "expected at least one result to have a category"

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
