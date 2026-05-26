"""Instagram engine multi-mode regression test.

Runs in this order, treating any 1 success as the "this is healthy" signal
(IG aggressively rate-limits unauthenticated automation, so we don't insist
that *every* mode work in every CI run):

1. **post mode** (most stable): given a known reel URL, fetch og meta and
   verify likes/comments/posted_at/image_url all parse. This is the
   structural improvement over the legacy hashtag-only path.
2. **user mode**: hit ``instagram.com/natgeo/`` and verify either the grid
   anchors or the og-description fallback yield a profile result with
   ``followers`` / ``posts`` populated.
3. **hashtag mode** (legacy): ``travel`` should return ≥1 result via the
   IG direct path **or** the Google/DDG SERP fallback. We don't require
   the direct path specifically — that depends on how rate-limited this
   IP is at the moment.

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
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search import core
from agent_search.engines.instagram import InstagramEngine


POST_URL_RE = re.compile(
    r"^https://www\.instagram\.com/(p|reel)/[A-Za-z0-9_-]+/?$"
)
USER_URL_RE = re.compile(
    r"^https://www\.instagram\.com/[A-Za-z0-9_.]+/?$"
)


def _summarise(results: list, label: str) -> None:
    print(f"  {label}: n={len(results)}")
    for i, r in enumerate(results[:3], 1):
        cap = (getattr(r, "caption", "") or "").replace("\n", " ")
        if len(cap) > 80:
            cap = cap[:80] + "..."
        print(
            f"    [{i}] {getattr(r, 'post_type', '?'):7s} "
            f"@{getattr(r, 'user', '') or '-':<22s} "
            f"src={getattr(r, 'source', '?'):<22s} "
            f"likes={getattr(r, 'likes', None)} "
            f"comments={getattr(r, 'comments', None)} "
            f"date={getattr(r, 'posted_at', '') or '-'} "
            f"sc={getattr(r, 'shortcode', '')} :: {cap}"
        )


def _check_post_mode(engine: InstagramEngine) -> bool:
    """post mode must reliably parse og:description on a known reel."""
    url = "https://www.instagram.com/reel/DTfS7SMEk8B/"
    print(f"\n--- POST mode :: {url} ---")
    results = engine.search(url, mode="post", limit=1)
    _summarise(results, "post")
    if not results:
        print("  post mode returned 0 — IG might be blocking right now")
        return False
    r = results[0]
    assert r.url and POST_URL_RE.match(r.url), f"bad url shape: {r.url!r}"
    assert getattr(r, "shortcode", "") == "DTfS7SMEk8B", \
        f"wrong shortcode: {getattr(r, 'shortcode', None)!r}"
    assert getattr(r, "post_type", "") in ("post", "reel"), \
        f"bad post_type: {getattr(r, 'post_type', None)!r}"
    assert getattr(r, "user", ""), "missing user (og:title parse failure?)"
    assert getattr(r, "likes", None) is not None, "missing likes"
    assert getattr(r, "comments", None) is not None, "missing comments"
    assert getattr(r, "posted_at", ""), "missing posted_at"
    assert getattr(r, "image_url", ""), "missing image_url"
    assert getattr(r, "source", "") == "instagram"
    return True


def _check_user_mode(engine: InstagramEngine) -> bool:
    """user mode must yield at least the og-description profile fallback."""
    print("\n--- USER mode :: natgeo ---")
    results = engine.search("natgeo", mode="user", limit=4)
    _summarise(results, "user")
    if not results:
        return False
    r = results[0]
    assert r.url and USER_URL_RE.match(r.url), f"bad user url: {r.url!r}"
    assert getattr(r, "user", "") == "natgeo"
    assert getattr(r, "source", "").startswith("instagram"), \
        f"bad source: {getattr(r, 'source', None)!r}"
    # Either the grid yielded individual posts (with shortcodes) OR the
    # og fallback yielded a single "profile" synthetic — we accept either.
    if getattr(r, "post_type", "") == "profile":
        # Profile-shape: assert og parsed followers / posts.
        followers = getattr(r, "followers", None)
        posts = getattr(r, "profile_posts", None)
        assert followers and followers > 1_000_000, \
            f"natgeo followers should be >1M, got {followers}"
        assert posts and posts > 100, f"natgeo posts should be >100, got {posts}"
    return True


def _check_hashtag_mode(engine: InstagramEngine) -> bool:
    """hashtag mode must return ≥1 result via *any* path."""
    print("\n--- HASHTAG mode :: travel ---")
    results = engine.search("travel", mode="hashtag", limit=6)
    _summarise(results, "hashtag")
    if not results:
        return False
    for r in results:
        assert r.url, "missing url"
        assert POST_URL_RE.match(r.url), f"bad URL shape: {r.url!r}"
        assert getattr(r, "shortcode", ""), f"missing shortcode: {r.url!r}"
        assert getattr(r, "post_type", "") in ("post", "reel"), \
            f"bad post_type: {getattr(r, 'post_type', None)!r}"
        assert getattr(r, "source", "") in (
            "instagram", "google", "duckduckgo"
        ), f"bad source: {getattr(r, 'source', None)!r}"
    return True


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    print("=== Instagram engine regression test (multi-mode) ===")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)

    passed: dict[str, bool] = {}
    try:
        page = core.new_page(browser)
        engine = InstagramEngine(page)

        try:
            passed["post"] = _check_post_mode(engine)
        except Exception:
            traceback.print_exc()
            passed["post"] = False

        try:
            passed["user"] = _check_user_mode(engine)
        except Exception:
            traceback.print_exc()
            passed["user"] = False

        try:
            passed["hashtag"] = _check_hashtag_mode(engine)
        except Exception:
            traceback.print_exc()
            passed["hashtag"] = False

    finally:
        try:
            browser.close()
        except Exception as e:
            print(f"warning: browser.close() raised: {e}", file=sys.stderr)

    print("\n=== mode results ===")
    for mode, ok in passed.items():
        print(f"  {mode:<8s} : {'PASS' if ok else 'FAIL'}")

    # Treat the run as healthy if ≥2 of the 3 modes passed (IG often
    # transient-blocks one mode at a time; this keeps the test useful in
    # CI without becoming a flake).
    n_pass = sum(1 for v in passed.values() if v)
    if n_pass >= 2:
        print(f"\n=== PASS === ({n_pass}/3 modes succeeded)")
        return 0
    print(
        f"\n=== FAIL === only {n_pass}/3 modes succeeded — IG likely "
        f"rate-limiting this IP. Try again after a cooldown.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
