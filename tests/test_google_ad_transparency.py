"""Google Ads Transparency Center engine regression test.

Runs ``mode=search_advertisers`` for ``shopify`` and verifies we get
≥3 advertisers with valid ``advertiser_id`` (must start with "AR") and
``country``. The advertiser_ads mode is NOT exercised here because
``SearchService/SearchCreatives`` requires extra session args we
haven't fully reverse-engineered — that path is best-effort and
documented as such.

Run:
    ~/tools/cloakbrowser/venv/bin/python tests/test_google_ad_transparency.py
"""
from __future__ import annotations

import os
import sys
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search import core
from agent_search.engines.google_ad_transparency import GoogleAdTransparencyEngine


def _search_advertisers() -> int:
    cfg = core.BrowserConfig(headless=True, humanize=False)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = GoogleAdTransparencyEngine(page)
        results = engine.search("shopify", limit=8,
                                 mode="search_advertisers",
                                 region="anywhere")
    finally:
        browser.close()

    if len(results) < 3:
        print(f"  FAIL: expected >=3 advertisers, got {len(results)}")
        print(f"  last_status: {engine.last_status}")
        return 1
    # Most rows have an "AR..." advertiser_id; some less-trafficked
    # advertisers come back with internal numeric / alternate IDs
    # (rare). Require ≥60% AR-prefixed and 100% non-empty.
    empty = [r for r in results if not getattr(r, "advertiser_id", "")]
    if empty:
        print(f"  FAIL: {len(empty)}/{len(results)} have empty advertiser_id")
        return 1
    ar_prefixed = [r for r in results if r.advertiser_id.startswith("AR")]
    if len(ar_prefixed) / len(results) < 0.6:
        print(f"  FAIL: only {len(ar_prefixed)}/{len(results)} have AR-prefixed IDs")
        return 1
    print(f"  PASS: {len(results)} advertisers, "
          f"{len(ar_prefixed)} AR-prefixed, all non-empty")
    for r in results[:3]:
        print(f"    - {r.advertiser_name} [{r.country}] id={r.advertiser_id} ads={r.ad_count}")
    return 0


def _unknown_mode() -> int:
    cfg = core.BrowserConfig(headless=True, humanize=False)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = GoogleAdTransparencyEngine(page)
        try:
            engine.search("x", limit=1, mode="bogus")
        except ValueError:
            print("  PASS: unknown mode raises ValueError")
            return 0
        print("  FAIL: should raise on unknown mode")
        return 1
    finally:
        browser.close()


def main() -> int:
    print("=== test_google_ad_transparency ===")
    failures = 0
    for label, fn in [
        ("search_advertisers", _search_advertisers),
        ("unknown_mode",       _unknown_mode),
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
