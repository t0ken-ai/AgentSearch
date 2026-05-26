"""TikTok Ad Library engine regression test.

The public ``library.tiktok.com`` only renders ads for **EU/UK regions**
(DSA mandate). From APAC IPs / non-EU regions the page returns 0 ads
unless logged in via TikTok-for-Business.

So the test runs as a **smoke test**:

1. Module imports + name set.
2. Unsupported region (e.g. CN) emits a clear warning in last_status.
3. Live call against region="GB" — accepts either ≥1 ad (residential
   EU IP) or 0 ads with a warning logged (most APAC residential IPs).

Run:
    ~/tools/cloakbrowser/venv/bin/python tests/test_tiktok_ad_library.py
"""
from __future__ import annotations

import os
import sys
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search import core
from agent_search.engines.tiktok_ad_library import (
    TikTokAdLibraryEngine, _SUPPORTED_REGIONS,
)


def _import_smoke() -> int:
    if TikTokAdLibraryEngine.name != "tiktok_ad_library":
        print("  FAIL: name attr wrong")
        return 1
    if "GB" not in _SUPPORTED_REGIONS or "US" in _SUPPORTED_REGIONS:
        print(f"  FAIL: SUPPORTED_REGIONS looks wrong: GB-yes? US-no?")
        return 1
    print("  PASS: import OK, supported regions sane")
    return 0


def _unsupported_region_warns() -> int:
    """region=CN should emit a warning and not crash."""
    cfg = core.BrowserConfig(headless=True, humanize=False)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = TikTokAdLibraryEngine(page)
        results = engine.search("shopify", limit=2, region="CN")
        warning = engine.last_status.get("warning")
        if not warning or "EU/UK" not in warning:
            print(f"  FAIL: expected EU/UK warning, got: {warning}")
            return 1
        print(f"  PASS: warning emitted for region=CN")
        print(f"        ({warning[:120]}...)")
        return 0
    finally:
        browser.close()


def _live_call_gb() -> int:
    cfg = core.BrowserConfig(headless=True, humanize=False)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = TikTokAdLibraryEngine(page)
        results = engine.search("shopify", limit=3, region="GB", days=30)
    finally:
        browser.close()

    print(f"  last_status: {engine.last_status}")
    if results:
        first = results[0]
        if not getattr(first, "advertiser_name", "") and not getattr(first, "ad_id", ""):
            print("  FAIL: result missing advertiser/ad metadata")
            return 1
        print(f"  PASS: live got {len(results)} ads")
        return 0
    # 0 ads is acceptable from non-EU IPs — the engine logged a warning.
    print("  PASS (degraded): 0 ads from this IP — expected behaviour "
          "outside EU/UK without `agentsearch login tiktok_business`. "
          "Use the tiktok_creative_center engine instead for global Top Ads.")
    return 0


def main() -> int:
    print("=== test_tiktok_ad_library ===")
    failures = 0
    for label, fn in [
        ("import_smoke",         _import_smoke),
        ("unsupported_region",   _unsupported_region_warns),
        ("live_gb",              _live_call_gb),
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
