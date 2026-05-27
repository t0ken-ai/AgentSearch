"""Live integration test for the 5 ad-library engines.

Drives a real browser through a US-residential proxy (Fluxisp) so the
target sites don't see APAC IPs and rate-limit us. Reads the proxy URL
from the ``FLUXISP_PROXY`` env var so credentials never land in the repo.

Run::

    export FLUXISP_PROXY="http://USER:PASS@us-eu.fluxisp.com:5000"
    ~/tools/cloakbrowser/venv/bin/python tests/test_ads_live.py

What's covered:

1. **meta_ad_library** — keyword="shopify", country=US.
2. **instagram_ad_library** — keyword="sephora", placement=reels.
3. **tiktok_creative_center** — mode="top_ads", period=7, country=US.
4. **google_ad_transparency** — keyword="nike", region=anywhere.
5. **tiktok_ad_library** — skipped (EU/UK only; the proxy egress is US).

Each engine is allowed up to ``per_engine_seconds`` to return data. We
print the top 1-3 results and confirm the major fields are populated.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search import core
from agent_search.engines.meta_ad_library import MetaAdLibraryEngine
from agent_search.engines.instagram_ad_library import InstagramAdLibraryEngine
from agent_search.engines.tiktok_creative_center import TikTokCreativeCenterEngine
from agent_search.engines.google_ad_transparency import GoogleAdTransparencyEngine


def _cfg() -> core.BrowserConfig:
    proxy = os.environ.get("FLUXISP_PROXY")
    if not proxy:
        print("FATAL: FLUXISP_PROXY env var is unset.")
        print("  Set it like:")
        print('    export FLUXISP_PROXY="http://USER:PASS@us-eu.fluxisp.com:5000"')
        sys.exit(2)
    return core.BrowserConfig(headless=True, humanize=False, proxy=proxy)


def _summary(r) -> str:
    """Compact one-line summary of a result for logging."""
    parts = [f"title={r.title[:60]!r}"]
    for k in ("ad_archive_id", "ad_id", "creative_id", "advertiser_id",
             "advertiser_name", "page_name", "brand_name",
             "video_url", "image_urls", "ctr", "country", "country_code"):
        v = getattr(r, k, None)
        if v in (None, "", [], {}):
            continue
        if isinstance(v, list):
            v = f"[{len(v)} items]"
        elif isinstance(v, str) and len(v) > 60:
            v = v[:60] + "…"
        parts.append(f"{k}={v}")
    return " | ".join(parts)


def t_meta() -> int:
    cfg = _cfg()
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        eng = MetaAdLibraryEngine(page)
        results = eng.search("shopify", limit=3, mode="keyword",
                             country="US", status="active")
    finally:
        browser.close()

    print(f"  status: {eng.last_status}")
    if not results:
        print(f"  FAIL: 0 results")
        return 1
    print(f"  PASS: {len(results)} ads")
    for r in results[:2]:
        print(f"    - {_summary(r)}")
    return 0


def t_instagram() -> int:
    cfg = _cfg()
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        eng = InstagramAdLibraryEngine(page)
        results = eng.search("sephora", limit=3, mode="keyword",
                             country="US", placement="reels")
    finally:
        browser.close()

    print(f"  status: {eng.last_status}")
    if not results:
        print(f"  FAIL: 0 results")
        return 1
    # Confirm platform tag is set.
    if results[0].__dict__.get("platform") != "instagram":
        print(f"  FAIL: platform tag missing on result")
        return 1
    print(f"  PASS: {len(results)} IG ads (placement={results[0].__dict__.get('placement')})")
    for r in results[:2]:
        print(f"    - {_summary(r)}")
    return 0


def t_tiktok_cc_top_ads() -> int:
    cfg = _cfg()
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        eng = TikTokCreativeCenterEngine(page)
        results = eng.search("", limit=5, mode="top_ads",
                             period=7, country_code="US",
                             order_by="for_you")
    finally:
        browser.close()

    print(f"  status: {eng.last_status}")
    if not results:
        print(f"  FAIL: 0 results")
        return 1
    sample = results[0]
    if not getattr(sample, "ad_id", "") or not getattr(sample, "video_url", ""):
        print(f"  FAIL: missing ad_id/video_url on first result: {_summary(sample)}")
        return 1
    print(f"  PASS: {len(results)} top ads")
    for r in results[:2]:
        print(f"    - {_summary(r)}")
    return 0


def t_tiktok_cc_trending_hashtags() -> int:
    """A second TT mode to verify multi-mode dispatch."""
    cfg = _cfg()
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        eng = TikTokCreativeCenterEngine(page)
        results = eng.search("", limit=5, mode="trending_hashtags",
                             period=7, country_code="US")
    finally:
        browser.close()

    print(f"  status: {eng.last_status}")
    if not results:
        print(f"  FAIL: 0 results")
        return 1
    print(f"  PASS: {len(results)} trending hashtags")
    for r in results[:3]:
        print(f"    - #{r.__dict__.get('hashtag')} rank={r.__dict__.get('rank')} "
              f"posts={r.__dict__.get('publish_cnt')}")
    return 0


def t_google_advertisers() -> int:
    cfg = _cfg()
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        eng = GoogleAdTransparencyEngine(page)
        results = eng.search("nike", limit=5, mode="search_advertisers",
                             region="anywhere")
    finally:
        browser.close()

    print(f"  status: {eng.last_status}")
    if not results:
        print(f"  FAIL: 0 results")
        return 1
    ar = [r for r in results if getattr(r, "advertiser_id", "").startswith("AR")]
    print(f"  PASS: {len(results)} results, {len(ar)} AR-prefixed advertisers")
    for r in results[:3]:
        rt = r.__dict__.get("result_type", "?")
        if rt == "advertiser":
            print(f"    - {r.advertiser_name} [{r.country}] "
                  f"id={r.advertiser_id} ads={r.ad_count}-{r.ad_count_max}")
        else:
            print(f"    - domain: {r.__dict__.get('domain')}")
    return 0


def main() -> int:
    print("=== ad live integration ===")
    print(f"FLUXISP_PROXY (host only): "
          f"{os.environ.get('FLUXISP_PROXY', 'UNSET').split('@')[-1]}")
    cases = [
        ("meta_ad_library",                 t_meta),
        ("instagram_ad_library",            t_instagram),
        ("tiktok_cc.top_ads",               t_tiktok_cc_top_ads),
        ("tiktok_cc.trending_hashtags",     t_tiktok_cc_trending_hashtags),
        ("google.search_advertisers",       t_google_advertisers),
    ]
    failures = 0
    for label, fn in cases:
        print(f"\n[{label}]")
        t0 = time.time()
        try:
            failures += fn()
        except Exception:
            failures += 1
            traceback.print_exc()
        print(f"  ({time.time() - t0:.1f}s)")
    print(f"\n{'PASS' if failures == 0 else f'{failures} FAIL'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
