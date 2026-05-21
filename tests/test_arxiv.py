"""arXiv search adapter test.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_arxiv.py
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
from agent_search.engines.arxiv import ArxivEngine


QUERY = "transformer language model"
LIMIT = 5


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== arXiv search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT}")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = ArxivEngine(page)
        results = engine.search(QUERY, limit=LIMIT)

        ls = engine.last_status or {}
        print(f"\nEntries returned by API: {ls.get('entries', 0)}")
        print(f"HTTP status: {ls.get('http_status', '-')}")

        if not results:
            print("\n=== FAIL === no results", file=sys.stderr)
            return 1

        assert len(results) > 0, "expected at least one arXiv result"

        print(f"\nReturned {len(results)} results")
        print("\n--- Top 5 results ---")
        for i, r in enumerate(results[:5], start=1):
            arxiv_id = getattr(r, "arxiv_id", "") or ""
            authors = getattr(r, "authors", "") or ""
            cats = getattr(r, "categories", "") or ""
            published = getattr(r, "published", "") or ""
            pdf_url = getattr(r, "pdf_url", "") or ""
            print(f"\n[{i}] {r.title}")
            print(f"    URL    : {r.url}")
            if pdf_url:
                print(f"    PDF    : {pdf_url}")
            meta = []
            if arxiv_id:
                meta.append(f"id={arxiv_id}")
            if cats:
                meta.append(f"cat={cats}")
            if published:
                meta.append(published)
            if meta:
                print(f"    Meta   : {' · '.join(meta)}")
            if authors:
                print(f"    Authors: {authors[:140]}{'…' if len(authors) > 140 else ''}")

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
