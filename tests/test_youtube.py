"""YouTube search adapter smoke test.

Steps:
1. Launch a headless browser via core.launch().
2. Run YouTubeEngine.search("Python tutorial").
3. Assert at least one SearchResult comes back, and that titles / URLs /
   channel / views fields are populated.
4. Print the top 5 results with channel, views, duration and upload_date.
5. Close the browser.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_youtube.py
"""

from __future__ import annotations

import logging
import os
import sys
import traceback

# Make sure the AgentSearch project root wins over any older editable install
# of `agent_search` that might be registered in site-packages.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search import core
from agent_search.engines.youtube import YouTubeEngine


QUERY = "Python tutorial"
LIMIT = 10


def _format_duration(seconds: int | None) -> str:
    if not seconds:
        return ""
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== YouTube search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT}")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = YouTubeEngine(page)

        results = engine.search(QUERY, limit=LIMIT)

        print(f"\nReturned {len(results)} results")
        if hasattr(engine, "last_status") and engine.last_status:
            print(f"Last status: {engine.last_status}")

        assert len(results) > 0, "expected at least one YouTube result"

        # Sanity checks: every result needs a title, a youtube watch URL, and
        # a non-empty video id.
        for r in results:
            assert r.title, f"missing title: {r!r}"
            assert "youtube.com/watch" in r.url or "youtu.be/" in r.url, (
                f"unexpected URL shape: {r.url!r}"
            )
            video_id = getattr(r, "video_id", "")
            assert video_id, f"missing video_id on result {r.title!r}"

        # At least one result should expose channel + views.
        with_channel = [r for r in results if getattr(r, "channel", "")]
        with_views = [r for r in results if getattr(r, "views", None) is not None]
        assert with_channel, (
            "expected at least one result to have a channel name; "
            f"titles: {[r.title for r in results]!r}"
        )
        assert with_views, (
            "expected at least one result to have parsed view count; "
            f"raw views_text: {[getattr(r, 'views_text', '') for r in results]!r}"
        )

        # Sanity: the literal 'python' should appear in at least one title
        # for the query "Python tutorial".
        with_python = [r for r in results if "python" in r.title.lower()]
        assert with_python, (
            "expected at least one result with 'python' in the title for "
            f"query {QUERY!r}; got: {[r.title for r in results]!r}"
        )

        print("\n--- Top 5 results ---")
        for i, r in enumerate(results[:5], start=1):
            channel = getattr(r, "channel", "")
            views = getattr(r, "views", None)
            views_text = getattr(r, "views_text", "")
            duration = getattr(r, "duration", None)
            duration_text = getattr(r, "duration_text", "")
            upload_date = getattr(r, "upload_date", "")
            video_id = getattr(r, "video_id", "")

            print(f"\n[{i}] {r.title}")
            print(f"    URL          : {r.url}")
            print(f"    Video ID     : {video_id or '(none)'}")
            print(f"    Channel      : {channel or '(none)'}")
            views_str = (
                f"{views:,}" if isinstance(views, int) else "(unknown)"
            )
            print(f"    Views        : {views_str}  (raw: {views_text or '(none)'})")
            dur_str = _format_duration(duration) if duration else "(unknown)"
            print(f"    Duration     : {dur_str}  (raw: {duration_text or '(none)'})")
            print(f"    Upload date  : {upload_date or '(none)'}")
            snippet = (r.snippet or "").replace("\n", " ")
            if len(snippet) > 220:
                snippet = snippet[:220] + "..."
            print(f"    Snippet      : {snippet}")

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
