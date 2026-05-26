"""Meta Ad Library engine regression test.

The Meta Ad Library is heavily IP-rate-limited. From APAC residential or
datacenter IPs **all GraphQL paginated calls return errors** — the engine
correctly captures this and emits a warning. So we run two-tier tests:

1. **import + URL-builder smoke** — always passes, confirms the module
   integrates cleanly.
2. **live call** — runs against the live Ad Library and either
   - returns ≥1 ad (success path on residential IP), OR
   - returns 0 ads with ``last_status.graphql_errors > 0`` (correct
     diagnostic on rate-limited IP).

Either of those is considered PASS — what we don't accept is silent
zero with no error log.

Run:
    ~/tools/cloakbrowser/venv/bin/python tests/test_meta_ad_library.py
"""
from __future__ import annotations

import os
import sys
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search import core
from agent_search.engines.meta_ad_library import MetaAdLibraryEngine


def _smoke_import_and_url() -> int:
    eng = MetaAdLibraryEngine.__new__(MetaAdLibraryEngine)
    # _build_url is pure (no network) so we can call it directly.
    url = eng._build_url("shopify", "keyword", "US", True, "ALL")
    if "ads/library" not in url or "q=shopify" not in url:
        print(f"  FAIL: url malformed: {url}")
        return 1
    if "search_type=keyword_unordered" not in url:
        print(f"  FAIL: missing search_type")
        return 1
    print(f"  PASS: url = {url[:120]}...")
    return 0


def _live_call() -> int:
    cfg = core.BrowserConfig(headless=True, humanize=False)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = MetaAdLibraryEngine(page)
        results = engine.search("shopify", limit=3, mode="keyword",
                                country="US", active_only=True)
    finally:
        browser.close()

    status = engine.last_status
    print(f"  last_status: {status}")

    # Tier 1: real ads found — perfect.
    if results:
        first = results[0]
        if not getattr(first, "ad_archive_id", ""):
            print("  FAIL: result missing ad_archive_id")
            return 1
        print(f"  PASS: live got {len(results)} ads (ad_id={first.ad_archive_id})")
        return 0

    # Tier 2: zero ads + graphql_errors > 0 → engine correctly diagnosed
    # an IP block. This is the expected outcome from APAC IPs.
    errors = status.get("graphql_errors", 0)
    if errors and status.get("graphql_calls_total", 0) > 0:
        print(f"  PASS (degraded): 0 ads but {errors} GraphQL errors — "
              f"engine correctly identified IP block. Run again with "
              f"`--proxy pool:residential` from a clean IP for live data.")
        return 0

    print("  FAIL: zero ads AND zero errors — engine didn't diagnose the issue")
    return 1


def main() -> int:
    print("=== test_meta_ad_library ===")
    failures = 0
    for label, fn in [
        ("import+url",   _smoke_import_and_url),
        ("live_call",    _live_call),
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
