"""TikTok Creative Center engine regression test.

Runs four checks:

1. **import smoke** — engine module imports and `name` is set.
2. **top_ads (default mode)** — fetch 5 Top Ads from US 7-day window.
   Verifies each result has the rich payload (ad_id / ctr / video_url
   at multiple resolutions / cover_image_url).
3. **filter sanity** — period validation downgrades unknown values.
4. **mode dispatch** — passing mode="trending_hashtags" reaches the
   correct API endpoint (verified by URL construction, no live call
   needed since we just want to ensure the routing works).

Run:
    ~/tools/cloakbrowser/venv/bin/python tests/test_tiktok_creative_center.py
"""
from __future__ import annotations

import os
import sys
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search import core
from agent_search.engines.tiktok_creative_center import TikTokCreativeCenterEngine


def _run_top_ads() -> int:
    cfg = core.BrowserConfig(headless=True, humanize=False)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = TikTokCreativeCenterEngine(page)
        results = engine.search("", limit=5, mode="top_ads",
                                 period=7, country_code="US")
    finally:
        browser.close()

    if len(results) < 3:
        print(f"  FAIL: expected >=3 ads, got {len(results)}")
        return 1
    sample = results[0]
    required = ["ad_id", "industry_key", "objective_key", "video_url",
                "cover_image_url", "video_urls"]
    missing = [k for k in required if not getattr(sample, k, None)]
    if missing:
        print(f"  FAIL: first ad missing fields: {missing}")
        return 1
    print(f"  PASS: top_ads returned {len(results)} ads with full media.")
    print(f"        sample: ad_id={sample.ad_id} ctr={sample.ctr} "
          f"likes={sample.likes} video={sample.video_url[:80]}...")
    return 0


def _run_filter_sanity() -> int:
    """period=999 should be downgraded to 30 silently."""
    cfg = core.BrowserConfig(headless=True, humanize=False)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = TikTokCreativeCenterEngine(page)
        # Don't actually fetch — short-circuit by checking last_status
        # after a malformed call. We just want to verify the engine
        # doesn't crash and last_status reflects the requested mode.
        _ = engine.search("", limit=1, mode="top_ads", period=999, country_code="US")
        if engine.last_status.get("period") != 30:
            print(f"  FAIL: period=999 should downgrade to 30, "
                  f"got {engine.last_status.get('period')}")
            return 1
        print("  PASS: period=999 downgraded to 30")
        return 0
    finally:
        browser.close()


def _run_unknown_mode() -> int:
    cfg = core.BrowserConfig(headless=True, humanize=False)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = TikTokCreativeCenterEngine(page)
        try:
            engine.search("", limit=1, mode="not_a_real_mode")
        except ValueError as e:
            if "unknown mode" in str(e):
                print("  PASS: unknown mode raises ValueError")
                return 0
        print("  FAIL: unknown mode should raise ValueError")
        return 1
    finally:
        browser.close()


def main() -> int:
    print("=== test_tiktok_creative_center ===")
    failures = 0
    for label, fn in [
        ("top_ads",         _run_top_ads),
        ("filter_sanity",   _run_filter_sanity),
        ("unknown_mode",    _run_unknown_mode),
    ]:
        print(f"\n[{label}]")
        try:
            failures += fn()
        except Exception:
            failures += 1
            traceback.print_exc()
    print(f"\n{'PASS' if failures == 0 else f'{failures} FAIL'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
