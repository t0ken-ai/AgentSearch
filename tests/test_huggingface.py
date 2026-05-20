"""HuggingFace search adapter test (REST API).

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_huggingface.py
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
from cloak_stealth_suite.engines.huggingface import HuggingFaceEngine


QUERY = "llama"
LIMIT = 5


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== HuggingFace search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT}")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = HuggingFaceEngine(page)
        results = engine.search(QUERY, limit=LIMIT)

        ls = engine.last_status or {}
        print(f"\nresult_count: {ls.get('result_count', 0)}  HTTP: {ls.get('http_status', '-')}")

        if not results:
            print("\n=== FAIL === no results", file=sys.stderr)
            return 1

        assert len(results) > 0, "expected at least one HF model"

        print(f"\nReturned {len(results)} results")
        print("\n--- Top 5 results ---")
        for i, r in enumerate(results[:5], start=1):
            mid = getattr(r, "model_id", "") or ""
            author = getattr(r, "author", "") or ""
            downloads = getattr(r, "downloads", 0) or 0
            likes = getattr(r, "likes", 0) or 0
            pipeline_tag = getattr(r, "pipeline_tag", "") or ""
            library = getattr(r, "library", "") or ""
            tags = getattr(r, "tags", []) or []
            print(f"\n[{i}] {r.title}")
            print(f"    URL    : {r.url}")
            meta = []
            if author:
                meta.append(f"by {author}")
            if pipeline_tag:
                meta.append(pipeline_tag)
            if library:
                meta.append(library)
            if downloads:
                meta.append(f"⬇ {downloads:,}")
            if likes:
                meta.append(f"♥ {likes:,}")
            if meta:
                print(f"    Meta   : {' · '.join(meta)}")
            if tags:
                print(f"    Tags   : {', '.join(t for t in tags[:7] if isinstance(t, str))}")

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
