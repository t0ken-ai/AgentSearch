"""Xiaoyuzhou (小宇宙FM) podcast search adapter test.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_xiaoyuzhou.py
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
from cloak_stealth_suite.engines.xiaoyuzhou import XiaoyuzhouEngine


QUERY = "AI"
LIMIT = 5


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== Xiaoyuzhou (小宇宙FM) search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT}")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = XiaoyuzhouEngine(page)
        results = engine.search(QUERY, limit=LIMIT)

        ls = engine.last_status or {}
        print(f"\nMode: {ls.get('mode', 'unknown')}")
        if ls.get("body_len") is not None:
            print(f"Direct body length: {ls['body_len']}")
        for src in ("duckduckgo_attempts", "bing_attempts", "google_attempts"):
            if ls.get(src):
                print(f"{src}:")
                for a in ls[src]:
                    print(f"  - {a['query']!r}: {a['organic']} organic")

        if not results:
            print("\n=== FAIL === no results", file=sys.stderr)
            return 1

        assert len(results) > 0, "expected at least one Xiaoyuzhou result"

        print(f"\nReturned {len(results)} results")
        print("\n--- Top 5 results ---")
        for i, r in enumerate(results[:5], start=1):
            ctype = getattr(r, "content_type", "") or ""
            xid = getattr(r, "xyz_id", "") or ""
            podcast_name = getattr(r, "podcast_name", "") or ""
            source = getattr(r, "source", "") or ""
            print(f"\n[{i}] {r.title}")
            print(f"    URL    : {r.url}")
            meta = []
            if ctype:
                meta.append(ctype)
            if xid:
                meta.append(f"id={xid[:12]}…")
            if podcast_name:
                meta.append(f"podcast={podcast_name}")
            if source:
                meta.append(f"src={source}")
            if meta:
                print(f"    Meta   : {' · '.join(meta)}")
            snippet = (r.snippet or "").replace("\n", " ")
            if len(snippet) > 200:
                snippet = snippet[:200] + "…"
            if snippet:
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
