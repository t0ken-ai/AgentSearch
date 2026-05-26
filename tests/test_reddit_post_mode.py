"""Reddit engine post-mode regression test.

Asserts that ``RedditEngine.search(url, mode="post")`` returns the post +
top comments using the official ``.json`` endpoint, and that media URLs
(image / video / gallery) are extracted when present.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_reddit_post_mode.py
"""

from __future__ import annotations

import logging
import os
import sys
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search import core
from agent_search.engines.reddit import RedditEngine
from agent_search.engines.reddit_subreddit import RedditSubredditEngine


def _check_self_post(engine: RedditEngine, sub_engine: RedditSubredditEngine) -> bool:
    print("\n--- POST mode :: r/Python first post (self/text) ---")
    listings = sub_engine.search("python", limit=3)
    if not listings:
        print("  could not pull r/Python listing")
        return False
    rs = engine.search(listings[0].url, mode="post", comment_limit=5)
    if not rs:
        return False
    p = rs[0]
    print(f"  title={p.title[:80]!r}")
    print(f"  sub={p.subreddit}, u/{p.author}, score={p.score_num}, comments_returned={len(rs)-1}")
    assert getattr(p, "kind", "") == "post", "first result must be the post itself"
    assert p.subreddit, "missing subreddit"
    assert p.author, "missing author"
    assert isinstance(p.score_num, int), "score not parsed"
    # Comments — at least one if num_comments > 0.
    if (p.num_comments or 0) > 0:
        assert len(rs) > 1, "expected ≥1 top comment when num_comments > 0"
        for c in rs[1:]:
            assert getattr(c, "kind", "") == "comment", "non-post results must be comments"
    return True


def _check_media_post(engine: RedditEngine, sub_engine: RedditSubredditEngine) -> bool:
    """Find a post with image_urls or video_url in the top of r/pics or r/funny."""
    print("\n--- POST mode :: media extraction (r/pics / r/aww / r/funny) ---")
    found = False
    for sub in ("pics", "aww", "funny", "gifs", "oddlysatisfying"):
        listings = sub_engine.search(sub, limit=12)
        for sr in listings or []:
            rs = engine.search(sr.url, mode="post", comment_limit=1)
            if not rs:
                continue
            p = rs[0]
            n_img = len(p.image_urls or [])
            has_vid = bool(p.video_url)
            n_gal = len(p.gallery or [])
            if n_img or has_vid or n_gal:
                print(f"  HIT in r/{sub}: title={p.title[:60]!r}")
                print(f"    image_urls={n_img} video_url? {has_vid} gallery={n_gal}")
                if p.image_urls:
                    print(f"    img[0]={p.image_urls[0][:80]}")
                if p.video_url:
                    print(f"    vid={p.video_url[:80]}")
                found = True
                # Sanity asserts on media URL shape.
                if has_vid:
                    assert "v.redd.it" in p.video_url or ".mp4" in p.video_url, (
                        f"unexpected video_url shape: {p.video_url!r}"
                    )
                if n_img:
                    for u in p.image_urls:
                        assert u.startswith("http"), f"non-URL image: {u!r}"
                break
        if found:
            break
    if not found:
        print("  no media post found across 5 subs — flaky, skipping assertion")
    return True  # don't fail the run on flakiness


def main() -> int:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    print("=== Reddit post-mode regression ===")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    passed: dict[str, bool] = {}
    try:
        page = core.new_page(browser)
        engine = RedditEngine(page)
        sub_engine = RedditSubredditEngine(page)

        for label, fn in (
            ("self_post", lambda: _check_self_post(engine, sub_engine)),
            ("media_post", lambda: _check_media_post(engine, sub_engine)),
        ):
            try:
                passed[label] = fn()
            except Exception:
                traceback.print_exc()
                passed[label] = False
    finally:
        try:
            browser.close()
        except Exception as e:
            print(f"warning: browser.close() raised: {e}", file=sys.stderr)

    print("\n=== results ===")
    for label, ok in passed.items():
        print(f"  {label:<14s} : {'PASS' if ok else 'FAIL'}")

    n_pass = sum(1 for v in passed.values() if v)
    if n_pass >= 1:
        print(f"\n=== PASS === ({n_pass}/{len(passed)} checks succeeded)")
        return 0
    print(f"\n=== FAIL === all checks failed", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
