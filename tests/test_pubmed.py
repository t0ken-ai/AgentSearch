"""PubMed search adapter smoke test.

Steps:
1. Launch a headless browser via core.launch().
2. Run PubMedEngine.search("machine learning healthcare").
3. Assert at least one SearchResult comes back.
4. Print the top 5 results with PMID + title + URL + snippet.
5. Close the browser.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_pubmed.py
"""

from __future__ import annotations

import logging
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
import traceback

from cloak_stealth_suite import core
from cloak_stealth_suite.engines.pubmed import PubMedEngine


QUERY = "machine learning healthcare"
LIMIT = 10


def _pmid_from_url(url: str) -> str:
    """Pull the trailing PMID out of https://pubmed.ncbi.nlm.nih.gov/<PMID>/."""
    return url.rstrip("/").rsplit("/", 1)[-1]


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== PubMed search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT}")

    cfg = core.BrowserConfig(headless=True, humanize=False)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = PubMedEngine(page)

        results = engine.search(QUERY, limit=LIMIT)

        print(f"\nReturned {len(results)} results")
        assert len(results) > 0, "expected at least one PubMed result"

        print("\n--- Top 5 results ---")
        for i, r in enumerate(results[:5], start=1):
            pmid = _pmid_from_url(r.url)
            print(f"\n[{i}] PMID {pmid}")
            print(f"    Title  : {r.title}")
            print(f"    URL    : {r.url}")
            snippet = (r.snippet or "").replace("\n", " ")
            if len(snippet) > 220:
                snippet = snippet[:220] + "..."
            print(f"    Snippet: {snippet}")

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
