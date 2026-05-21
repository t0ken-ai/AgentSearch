"""Netflix search adapter smoke test.

Steps:
1. Launch a headless browser via core.launch().
2. Run NetflixEngine.search("Stranger Things") with up to 3 attempts so
   transient hydration / Google-consent issues don't fail the run.
3. Assert at least one SearchResult comes back with title + url +
   netflix_id, and at least one matches "stranger things".
4. Print the top 5 results, including ``type``, ``year``, ``rating`` and
   ``source``.
5. Close the browser.

Because Netflix's in-site search requires login, we expect almost every
real run to come back via the Google ``site:netflix.com inurl:title``
fallback (``source == "google"``). The test accepts results from either
path so it stays useful if a logged-in profile is ever provided.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_netflix.py
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
from agent_search.engines.netflix import NetflixEngine
from agent_search.stealth.enhance import check_blocked


QUERY = "Stranger Things"
LIMIT = 10
MAX_ATTEMPTS = 3

EXPECTED_SOURCES = {"netflix", "google"}


def _attempt(engine: NetflixEngine, attempt: int) -> list:
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
        print(f"    {sel:<28} -> {n}")

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
        body_len = engine.last_status.get("body_len")
        if body_len is not None:
            print(f"  body length    : {body_len} chars")
        gstatus = engine.last_status.get("google_status")
        if gstatus:
            block = gstatus.get("block_reason")
            if block:
                print(f"  google block   : {block!r}")

    print(f"  results        : {len(results)}")
    return results


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== Netflix search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT} | Max attempts: {MAX_ATTEMPTS}")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = NetflixEngine(page)

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
        assert len(results) > 0, "expected at least one Netflix result"

        # Sanity checks on individual fields.
        for r in results:
            assert r.title, f"missing title: {r!r}"
            assert r.url.startswith("https://www.netflix.com/title/"), (
                f"unexpected URL shape: {r.url!r}"
            )
            netflix_id = getattr(r, "netflix_id", "")
            assert netflix_id and netflix_id.isdigit(), (
                f"missing/invalid netflix_id on result {r.title!r}: "
                f"{netflix_id!r}"
            )
            source = getattr(r, "source", "")
            assert source in EXPECTED_SOURCES, (
                f"unexpected source {source!r} on {r.title!r}"
            )

        # We should have at least one result whose title or snippet mentions
        # "Stranger Things" — the query is specific enough that any other
        # outcome means we caught unrelated noise.
        st_hit = [
            r for r in results
            if "stranger things" in r.title.lower()
            or "stranger things" in (r.snippet or "").lower()
        ]
        assert st_hit, (
            "expected at least one result with 'Stranger Things' in title "
            f"or snippet; got titles: {[r.title for r in results]!r}"
        )

        seen_sources = {getattr(r, "source", "") for r in results}
        seen_types = {getattr(r, "type", "") for r in results}
        print(
            f"\nReturned {len(results)} results "
            f"(sources: {sorted(seen_sources)}, types: {sorted(seen_types)})"
        )

        print("\n--- Top 5 results ---")
        for i, r in enumerate(results[:5], start=1):
            netflix_id = getattr(r, "netflix_id", "")
            rtype = getattr(r, "type", "")
            year = getattr(r, "year", None)
            rating = getattr(r, "rating", "")
            source = getattr(r, "source", "")

            print(f"\n[{i}] {r.title}")
            print(f"    Type     : {rtype or '(unknown)'}")
            print(f"    Year     : {year if year is not None else '(unknown)'}")
            print(f"    Rating   : {rating or '(none)'}")
            print(f"    Source   : {source or '(none)'}")
            print(f"    URL      : {r.url}")
            print(f"    Netflix ID: {netflix_id or '(none)'}")
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
