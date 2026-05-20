"""TikTok search adapter smoke test.

TikTok aggressively blocks unauthenticated automation, so this test does
*not* require the direct ``tiktok.com/search`` path to succeed. It only
asks that *one* of the two paths in :class:`TikTokEngine` returns at least
one usable result:

1. **Direct path**  — ``tiktok.com/search?q=cooking``
2. **Google fallback** — ``site:tiktok.com cooking`` via GoogleEngine

A "usable result" is one whose URL points at an actual TikTok video page
(``https://www.tiktok.com/@<user>/video/<numeric_id>``) and that exposes
the structured fields the engine promises (``author`` / ``video_id``).

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_tiktok.py
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
import traceback

# Make sure the AgentSearch project root wins over any older editable install
# of `cloak_stealth_suite` that might be registered in site-packages.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from cloak_stealth_suite import core
from cloak_stealth_suite.engines.tiktok import TikTokEngine
from cloak_stealth_suite.stealth.enhance import check_blocked


QUERY = "cooking"
LIMIT = 8
MAX_ATTEMPTS = 2

VIDEO_URL_RE = re.compile(
    r"^https://www\.tiktok\.com/@[^/]+/video/\d+$"
)


def _attempt(engine: TikTokEngine, attempt: int) -> list:
    """Run one search and dump diagnostics. Returns the result list."""
    print(f"\n--- attempt {attempt}/{MAX_ATTEMPTS} ---")
    # Bypass BaseEngine retry loop here so we can print diagnostics each time.
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
        mode = engine.last_status.get("mode")
        if mode:
            print(f"  mode           : {mode!r}")
        block_reason = engine.last_status.get("block_reason")
        if block_reason:
            print(f"  block_reason   : {block_reason!r}")
        google_status = engine.last_status.get("google_status")
        if google_status:
            print(f"  google_status  : {google_status!r}")
        body_len = engine.last_status.get("body_len")
        if body_len is not None:
            print(f"  body length    : {body_len} chars")

    print(f"  results        : {len(results)}")
    return results


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== TikTok search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT} | Max attempts: {MAX_ATTEMPTS}")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = TikTokEngine(page)

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
            print(
                "\n=== FAIL === no TikTok results after all attempts "
                "(direct path AND Google fallback both empty)",
                file=sys.stderr,
            )
            return 1

        # Required assertion: at least one result.
        assert len(results) > 0, "expected at least one TikTok result"

        # Sanity checks on individual fields. We only require URL + video_id
        # + author here — the title can be empty if we synthesised it from
        # the author handle.
        for r in results:
            assert r.url, f"missing url on result: {r!r}"
            assert VIDEO_URL_RE.match(r.url), (
                f"unexpected URL shape (want /@user/video/<id>): {r.url!r}"
            )
            video_id = getattr(r, "video_id", "")
            assert video_id and video_id.isdigit(), (
                f"missing or non-numeric video_id on {r.url!r}: {video_id!r}"
            )
            author = getattr(r, "author", "")
            assert author.startswith("@"), (
                f"author should start with '@', got: {author!r}"
            )
            source = getattr(r, "source", "")
            assert source in ("tiktok", "google"), (
                f"source must be 'tiktok' or 'google', got: {source!r}"
            )

        sources = {getattr(r, "source", "") for r in results}
        print(
            f"\nReturned {len(results)} results "
            f"(sources: {sorted(sources)})"
        )

        # Friendly summary.
        print("\n--- Top 5 results ---")
        for i, r in enumerate(results[:5], start=1):
            author = getattr(r, "author", "")
            video_id = getattr(r, "video_id", "")
            likes = getattr(r, "likes", None)
            likes_text = getattr(r, "likes_text", "")
            source = getattr(r, "source", "")

            title = (r.title or "").replace("\n", " ")
            if len(title) > 120:
                title = title[:120] + "..."

            print(f"\n[{i}] {title}")
            print(f"    Author    : {author or '(none)'}")
            print(f"    Video ID  : {video_id or '(none)'}")
            if likes_text:
                print(f"    Likes     : {likes_text} ({likes})")
            print(f"    Source    : {source}")
            print(f"    URL       : {r.url}")

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
