"""Apple Podcasts search adapter test (iTunes Search API).

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_apple_podcasts.py
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
from cloak_stealth_suite.engines.apple_podcasts import ApplePodcastsEngine


QUERY = "Lex Fridman"
LIMIT = 5


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== Apple Podcasts search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT}")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = ApplePodcastsEngine(page)
        results = engine.search(QUERY, limit=LIMIT)

        ls = engine.last_status or {}
        print(f"\nresult_count: {ls.get('result_count', 0)}  HTTP: {ls.get('http_status', '-')}")

        if not results:
            print("\n=== FAIL === no results", file=sys.stderr)
            return 1

        assert len(results) > 0, "expected at least one Apple Podcasts result"

        print(f"\nReturned {len(results)} results")
        print("\n--- Top 5 results ---")
        for i, r in enumerate(results[:5], start=1):
            ctype = getattr(r, "content_type", "") or ""
            artist = getattr(r, "artist", "") or ""
            genre = getattr(r, "genre", "") or ""
            tid = getattr(r, "track_id", "") or ""
            feed_url = getattr(r, "feed_url", "") or ""
            release = getattr(r, "release_date", "") or ""
            print(f"\n[{i}] {r.title}")
            print(f"    URL    : {r.url}")
            if feed_url:
                print(f"    Feed   : {feed_url[:120]}")
            meta = []
            if ctype:
                meta.append(ctype)
            if tid:
                meta.append(f"id={tid}")
            if artist:
                meta.append(f"by {artist}")
            if genre:
                meta.append(genre)
            if release:
                meta.append(release)
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
