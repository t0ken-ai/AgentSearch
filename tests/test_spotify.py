"""Spotify Web search adapter smoke test.

Steps:
1. Launch a headless browser via core.launch().
2. Run SpotifyEngine.search("Beatles") with up to 3 attempts so transient
   hydration / consent issues don't fail the run.
3. Assert at least one SearchResult comes back with title + url + entity id.
4. Print the top 5 results, including ``artist`` and ``type``.
5. Close the browser.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_spotify.py
"""

from __future__ import annotations

import logging
import os
import sys
import time
import traceback

# Make sure the AgentSearch project root wins over any older editable install
# of `agent_search` that might be registered in site-packages.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search import core
from agent_search.engines.spotify import SpotifyEngine
from agent_search.stealth.enhance import check_blocked


QUERY = "Beatles"
LIMIT = 10
MAX_ATTEMPTS = 3

# Set of entity types we expect to see for an artist query like "Beatles".
EXPECTED_TYPES = {"song", "album", "artist", "playlist"}


def _attempt(engine: SpotifyEngine, attempt: int) -> list:
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
        block_reason = engine.last_status.get("block_reason")
        if block_reason:
            print(f"  block_reason   : {block_reason!r}")
        print(f"  body length    : {engine.last_status.get('body_len')} chars")

    print(f"  results        : {len(results)}")
    return results


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== Spotify search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT} | Max attempts: {MAX_ATTEMPTS}")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = SpotifyEngine(page)

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
            print("\n=== FAIL === no results after all attempts", file=sys.stderr)
            return 1

        # Required assertion: at least one result.
        assert len(results) > 0, "expected at least one Spotify result"

        # Sanity checks on individual fields.
        for r in results:
            assert r.title, f"missing title: {r!r}"
            assert r.url.startswith("https://open.spotify.com/"), (
                f"unexpected URL shape: {r.url!r}"
            )
            entity_id = getattr(r, "entity_id", "")
            assert entity_id, f"missing entity_id on result {r.title!r}"
            rtype = getattr(r, "type", "")
            assert rtype, f"missing type on result {r.title!r}"

        # We should see at least one of the common entity types for a popular
        # artist query. We don't require *every* type because the page may be
        # truncated for unauthenticated visitors.
        seen_types = {getattr(r, "type", "") for r in results}
        assert seen_types & EXPECTED_TYPES, (
            f"expected at least one of {EXPECTED_TYPES} in result types, "
            f"got: {seen_types!r}"
        )

        # The query 'Beatles' should match somewhere — title for an artist /
        # song / album hit, or the artist string for a track.
        beatle = [
            r for r in results
            if "beatles" in r.title.lower()
            or "beatles" in (getattr(r, "artist", "") or "").lower()
        ]
        assert beatle, (
            "expected at least one result with 'Beatles' in title or artist; "
            f"got titles: {[r.title for r in results]!r}"
        )

        print(f"\nReturned {len(results)} results (types: {sorted(seen_types)})")

        print("\n--- Top 5 results ---")
        for i, r in enumerate(results[:5], start=1):
            rtype = getattr(r, "type", "")
            artist = getattr(r, "artist", "")
            album = getattr(r, "album", "")
            entity_id = getattr(r, "entity_id", "")

            print(f"\n[{i}] {r.title}")
            print(f"    Type     : {rtype or '(none)'}")
            print(f"    Artist   : {artist or '(none)'}")
            if rtype == "song":
                print(f"    Album    : {album or '(none)'}")
            print(f"    URL      : {r.url}")
            print(f"    Entity ID: {entity_id or '(none)'}")
            snippet = (r.snippet or "").replace("\n", " ")
            if len(snippet) > 200:
                snippet = snippet[:200] + "..."
            print(f"    Snippet  : {snippet}")

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
