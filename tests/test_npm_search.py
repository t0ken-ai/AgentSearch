"""NPM Registry search adapter smoke test.

Steps:
1. Launch a headless browser via core.launch().
2. Run NpmSearchEngine.search("express").
3. Assert at least one SearchResult comes back, and that name / url / version
   / description fields are populated.
4. Print the top 5 results with version, description, downloads and license.
5. Close the browser.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_npm_search.py
"""

from __future__ import annotations

import logging
import os
import sys
import traceback

# Make sure the AgentSearch project root wins over any older editable install
# of `agent_search` that might be registered in site-packages.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search import core
from agent_search.engines.npm_search import NpmSearchEngine


QUERY = "express"
LIMIT = 10


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== NPM Registry search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT}")

    cfg = core.BrowserConfig(headless=True, humanize=False)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = NpmSearchEngine(page)

        results = engine.search(QUERY, limit=LIMIT)

        print(f"\nReturned {len(results)} results")
        assert len(results) > 0, "expected at least one npm result"

        # Sanity checks: every result needs a name/title, an npmjs.com URL,
        # and a version string ("x.y.z" style).
        for r in results:
            assert r.title, f"missing title (package name): {r!r}"
            assert r.url.startswith("https://www.npmjs.com/package/"), (
                f"unexpected URL shape: {r.url!r}"
            )
            version = getattr(r, "version", "")
            assert version, f"missing version on result {r.title!r}"

        # Sanity: the literal "express" should be in there somewhere — either
        # as an exact match or in the snippet — for query "express".
        exact = next(
            (r for r in results if r.title == "express" or r.title.startswith("express")),
            None,
        )
        assert exact is not None, (
            f"expected at least one 'express'-named package in top {LIMIT} "
            f"results; got titles: {[r.title for r in results]!r}"
        )

        # At least one result should expose downloads + license (the search
        # API returns these for almost every published package).
        with_downloads = [
            r for r in results if getattr(r, "downloads_weekly", 0) > 0
        ]
        with_license = [r for r in results if getattr(r, "license", "")]
        assert with_downloads, "expected at least one result to report weekly downloads"
        assert with_license, "expected at least one result to report a license"

        print("\n--- Top 5 results ---")
        for i, r in enumerate(results[:5], start=1):
            version = getattr(r, "version", "")
            description = getattr(r, "description", "")
            license_str = getattr(r, "license", "")
            weekly = getattr(r, "downloads_weekly", 0)
            monthly = getattr(r, "downloads_monthly", 0)
            dependents = getattr(r, "dependents", 0)
            publisher = getattr(r, "publisher", "")
            keywords = getattr(r, "keywords", []) or []

            print(f"\n[{i}] {r.title}")
            print(f"    URL         : {r.url}")
            print(f"    Version     : {version or '(unknown)'}")
            print(f"    License     : {license_str or '(none)'}")
            print(f"    Downloads   : {weekly:,}/wk  |  {monthly:,}/mo")
            if dependents:
                print(f"    Dependents  : {dependents:,}")
            if publisher:
                print(f"    Publisher   : {publisher}")
            if keywords:
                print(f"    Keywords    : {', '.join(keywords[:6])}")
            desc = (description or "").replace("\n", " ")
            if len(desc) > 220:
                desc = desc[:220] + "..."
            print(f"    Description : {desc or '(none)'}")

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
