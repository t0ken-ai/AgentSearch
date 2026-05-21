"""Bilibili (B站) search adapter test.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_bilibili.py
"""

from __future__ import annotations

import logging
import os
import sys
import time
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search import core
from agent_search.engines.bilibili import BilibiliEngine
from agent_search.stealth.enhance import check_blocked


QUERY = "Python 入门"
LIMIT = 5
MAX_ATTEMPTS = 3


def _attempt(engine: BilibiliEngine, attempt: int) -> list:
    print(f"\n--- attempt {attempt}/{MAX_ATTEMPTS} ---")
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

    print(f"  page title : {title!r}")
    print(f"  page url   : {url}")

    counts = engine.selector_counts()
    print("  selector counts:")
    for sel, n in counts.items():
        print(f"    {sel:<48} -> {n}")

    blocked_reason = check_blocked(page)
    if blocked_reason:
        print(f"  check_blocked  : {blocked_reason}")

    if engine.last_status:
        ls = engine.last_status
        if ls.get("body_len") is not None:
            print(f"  body length    : {ls.get('body_len')} chars")
        if ls.get("cards_seen") is not None:
            print(f"  cards seen     : {ls.get('cards_seen')}")
        if ls.get("block_reason"):
            print(f"  block_reason   : {ls.get('block_reason')!r}")
        if ls.get("count") is not None:
            print(f"  parsed count   : {ls.get('count')}")

    print(f"  results        : {len(results)}")
    return results


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== Bilibili search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT} | Max attempts: {MAX_ATTEMPTS}")

    cfg = core.BrowserConfig(
        headless=True,
        humanize=True,
        locale="zh-CN",
        timezone="Asia/Shanghai",
    )
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = BilibiliEngine(page)

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
                wait = 5 + attempt * 2
                print(f"  no results -- sleeping {wait}s before retry")
                time.sleep(wait)

        if not results:
            print(
                "\n=== FAIL === no results after all attempts",
                file=sys.stderr,
            )
            return 1

        assert len(results) > 0, "expected at least one Bilibili video"

        print(f"\nReturned {len(results)} results")
        print("\n--- Top 5 videos ---")
        for i, r in enumerate(results[:5], start=1):
            author = getattr(r, "author", "") or ""
            play = getattr(r, "play_count", "") or ""
            danmaku = getattr(r, "danmaku_count", "") or ""
            duration = getattr(r, "duration", "") or ""
            print(f"\n[{i}] {r.title}")
            print(f"    URL    : {r.url}")
            if author:
                print(f"    UP主   : {author}")
            stats = []
            if play:
                stats.append(f"播放={play}")
            if danmaku:
                stats.append(f"弹幕={danmaku}")
            if duration:
                stats.append(f"时长={duration}")
            if stats:
                print(f"    Stats  : {' | '.join(stats)}")
            snippet = (r.snippet or "").replace("\n", " ")
            if len(snippet) > 240:
                snippet = snippet[:240] + "..."
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
