"""Douyin (抖音) search adapter test (with Google site: fallback).

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_douyin.py
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
from cloak_stealth_suite.engines.douyin import DouyinEngine


QUERY = "美食"
LIMIT = 5


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== Douyin search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT}")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = DouyinEngine(page)
        results = engine.search(QUERY, limit=LIMIT)

        ls = engine.last_status or {}
        print(f"\nMode: {ls.get('mode', 'unknown')}")
        if ls.get("body_len") is not None:
            print(f"Direct body length: {ls['body_len']}")
        if ls.get("anchors_seen") is not None:
            print(f"Direct video/note anchors: {ls['anchors_seen']}")
        if ls.get("google_attempts"):
            print("Google attempts:")
            for a in ls["google_attempts"]:
                print(f"  - {a['query']!r}: {a['organic']} organic")
        if ls.get("bing_attempts"):
            print("Bing attempts:")
            for a in ls["bing_attempts"]:
                print(f"  - {a['query']!r}: {a['organic']} organic")

        if not results:
            print("\n=== FAIL === no results", file=sys.stderr)
            return 1

        assert len(results) > 0, "expected at least one Douyin result"

        print(f"\nReturned {len(results)} results")
        print("\n--- Top 5 results ---")
        for i, r in enumerate(results[:5], start=1):
            vid = getattr(r, "video_id", "") or ""
            ctype = getattr(r, "content_type", "") or ""
            user = getattr(r, "user", "") or ""
            likes = getattr(r, "likes", "") or ""
            source = getattr(r, "source", "") or ""
            print(f"\n[{i}] {r.title}")
            print(f"    URL    : {r.url}")
            meta = []
            if ctype:
                meta.append(ctype)
            if vid:
                meta.append(f"id={vid}")
            if user:
                meta.append(f"@{user}")
            if likes:
                meta.append(f"♡{likes}")
            if source:
                meta.append(f"src={source}")
            if meta:
                print(f"    Meta   : {' · '.join(meta)}")
            snippet = (r.snippet or "").replace("\n", " ")
            if len(snippet) > 200:
                snippet = snippet[:200] + "…"
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
