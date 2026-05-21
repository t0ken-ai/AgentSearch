"""Instagram search adapter smoke test.

Instagram aggressively blocks unauthenticated automation, so this test
does *not* require the direct ``instagram.com/explore/tags/<tag>/`` path
to succeed. It only asks that *one* of the two paths in
:class:`InstagramEngine` returns at least one usable result:

1. **Direct path**  — ``instagram.com/explore/tags/travel/``
2. **Google fallback** — ``site:instagram.com (inurl:/p/ OR inurl:/reel/) #travel``

A "usable result" is one whose URL points at an actual Instagram post or
reel page (``https://www.instagram.com/p/<shortcode>/`` or
``https://www.instagram.com/reel/<shortcode>/``) and that exposes the
structured fields the engine promises (``shortcode`` / ``post_type`` /
``source``).

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_instagram.py
"""

from __future__ import annotations

import logging
import os
import re
import sys
import time
import traceback

# Make sure the AgentSearch project root wins over any older editable install
# of `agent_search` that might be registered in site-packages.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search import core
from agent_search.engines.instagram import InstagramEngine
from agent_search.stealth.enhance import check_blocked


QUERY = "travel"
LIMIT = 8
MAX_ATTEMPTS = 2

# Either /p/<shortcode>/ or /reel/<shortcode>/, both with or without
# trailing slash. Shortcode is Instagram's URL-safe base64 id.
POST_URL_RE = re.compile(
    r"^https://www\.instagram\.com/(p|reel)/[A-Za-z0-9_-]+/?$"
)


def _attempt(engine: InstagramEngine, attempt: int) -> list:
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

    print("=== Instagram search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT} | Max attempts: {MAX_ATTEMPTS}")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = InstagramEngine(page)

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
                "\n=== FAIL === no Instagram results after all attempts "
                "(direct path AND Google fallback both empty)",
                file=sys.stderr,
            )
            return 1

        # Required assertion: at least one result.
        assert len(results) > 0, "expected at least one Instagram result"

        # Sanity checks on individual fields. We only require URL +
        # shortcode + post_type here — the caption / user can be empty
        # because Instagram does not always show them on the grid.
        for r in results:
            assert r.url, f"missing url on result: {r!r}"
            assert POST_URL_RE.match(r.url), (
                f"unexpected URL shape (want /p/<sc>/ or /reel/<sc>/): "
                f"{r.url!r}"
            )
            shortcode = getattr(r, "shortcode", "")
            assert shortcode and re.match(r"^[A-Za-z0-9_-]+$", shortcode), (
                f"missing or malformed shortcode on {r.url!r}: {shortcode!r}"
            )
            post_type = getattr(r, "post_type", "")
            assert post_type in ("post", "reel"), (
                f"post_type must be 'post' or 'reel', got: {post_type!r}"
            )
            source = getattr(r, "source", "")
            assert source in ("instagram", "google"), (
                f"source must be 'instagram' or 'google', got: {source!r}"
            )

        sources = {getattr(r, "source", "") for r in results}
        post_types = {getattr(r, "post_type", "") for r in results}
        print(
            f"\nReturned {len(results)} results "
            f"(sources: {sorted(sources)}, types: {sorted(post_types)})"
        )

        # Verify fallback worked when direct path failed: at minimum, when
        # the only source is "google", the test still passes — that's the
        # fallback contract. We don't assert *both* sources because on most
        # runs only one path will succeed.
        if sources == {"google"}:
            print("(direct path failed; fallback to Google succeeded ✓)")
        elif sources == {"instagram"}:
            print("(direct path succeeded ✓)")

        # Friendly summary.
        print("\n--- Top 5 results ---")
        for i, r in enumerate(results[:5], start=1):
            user = getattr(r, "user", "")
            shortcode = getattr(r, "shortcode", "")
            post_type = getattr(r, "post_type", "")
            likes = getattr(r, "likes", None)
            likes_text = getattr(r, "likes_text", "")
            comments = getattr(r, "comments", None)
            comments_text = getattr(r, "comments_text", "")
            caption = getattr(r, "caption", "")
            source = getattr(r, "source", "")

            title = (r.title or "").replace("\n", " ")
            if len(title) > 120:
                title = title[:120] + "..."
            short_caption = (caption or "").replace("\n", " ")
            if len(short_caption) > 120:
                short_caption = short_caption[:120] + "..."

            print(f"\n[{i}] {title}")
            print(f"    User      : @{user}" if user else "    User      : (none)")
            print(f"    Shortcode : {shortcode or '(none)'}")
            print(f"    Type      : {post_type}")
            if likes_text:
                print(f"    Likes     : {likes_text} ({likes})")
            if comments_text:
                print(f"    Comments  : {comments_text} ({comments})")
            if short_caption:
                print(f"    Caption   : {short_caption}")
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
