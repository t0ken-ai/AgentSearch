"""Meta Ad Library engine regression test.

The Meta Ad Library is heavily IP-rate-limited. From APAC residential or
datacenter IPs **all GraphQL paginated calls return errors** — the engine
correctly captures this and emits a warning. So we run two-tier tests:

1. **import + URL-builder smoke** — always passes, confirms the module
   integrates cleanly. Also exercises the new helpers
   (``_extract_page_id_from_url``, ``_extract_rsoc_from_url``,
   ``_to_epoch``, ``_parse_range_text``).
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
from agent_search.engines.meta_ad_library import (
    MetaAdLibraryEngine,
    _extract_page_id_from_url,
    _extract_rsoc_from_url,
    _parse_range_text,
    _to_epoch,
)


def _smoke_helpers() -> int:
    """Pure-function smoke covering URL parsing, RSOC, epoch, range."""
    fail = 0

    # url -> page_id
    cases = [
        ("https://www.facebook.com/ads/library/?view_all_page_id=123456", "123456"),
        ("https://www.facebook.com/profile.php?id=987654", "987654"),
        ("https://www.facebook.com/123456", "123456"),
        ("https://www.facebook.com/CocaCola", None),       # vanity
        ("https://example.com/123456", None),              # non-fb
        ("123456", "123456"),                              # bare id
    ]
    for url, expected in cases:
        got = _extract_page_id_from_url(url)
        if got != expected:
            print(f"  FAIL extract_page_id({url!r}) -> {got!r}, expected {expected!r}")
            fail += 1

    # RSOC
    rsoc1 = _extract_rsoc_from_url(
        "https://example.com/?q=cheap+shoes&utm_term=running+shoes"
    )
    if "cheap shoes" not in rsoc1 or "running shoes" not in rsoc1:
        print(f"  FAIL rsoc1 = {rsoc1!r}")
        fail += 1
    rsoc2 = _extract_rsoc_from_url(
        "https://example.com/?forceKeyA=running+shoes&forceKeyB=trail+runners"
    )
    if "running shoes" not in rsoc2 or "trail runners" not in rsoc2:
        print(f"  FAIL rsoc2 = {rsoc2!r}")
        fail += 1
    # forceKey takes priority — q should be ignored when forceKey present
    rsoc3 = _extract_rsoc_from_url(
        "https://example.com/?q=should_not_appear&forceKeyA=only+this"
    )
    if "should_not_appear" in rsoc3 or "only this" not in rsoc3:
        print(f"  FAIL rsoc3 (forceKey priority) = {rsoc3!r}")
        fail += 1

    # epoch
    if _to_epoch("2024-01-01") is None or _to_epoch(1700000000) != 1700000000 \
            or _to_epoch(None) is not None:
        print("  FAIL _to_epoch")
        fail += 1

    # range
    if _parse_range_text("1K-5K") != (1000, 5000) \
            or _parse_range_text("$9K-$10K") != (9000, 10000) \
            or _parse_range_text(">1M") != (1_000_000, None) \
            or _parse_range_text("") != (None, None):
        print("  FAIL _parse_range_text")
        fail += 1

    if fail == 0:
        print("  PASS: helpers")
    return fail


def _smoke_url_builder() -> int:
    """Verify the rich _build_url emits all configured params."""
    eng = MetaAdLibraryEngine.__new__(MetaAdLibraryEngine)

    # Default keyword search
    url = eng._build_url(
        query="shopify", mode="keyword", country="US",
        status="active", ad_type="all", media_type="ALL",
        search_type="keyword_unordered", sort_by=None,
        page_ids=[], publisher_platforms=None, start_date=None,
    )
    expected_fragments = [
        "ads/library", "q=shopify", "active_status=active",
        "search_type=keyword_unordered", "ad_type=all", "country=US",
        "media_type=all",
    ]
    for frag in expected_fragments:
        if frag not in url:
            print(f"  FAIL: keyword url missing {frag!r}: {url}")
            return 1

    # Page mode
    url2 = eng._build_url(
        query="", mode="advertiser", country="GB",
        status="all", ad_type="all", media_type="ALL",
        search_type="page", sort_by=None,
        page_ids=["12345"], publisher_platforms=["facebook", "instagram"],
        start_date=None,
    )
    if "view_all_page_id=12345" not in url2:
        print(f"  FAIL: page url missing view_all_page_id: {url2}")
        return 1
    if "publisher_platforms" not in url2:
        print(f"  FAIL: page url missing publisher_platforms: {url2}")
        return 1
    if "country=GB" not in url2 or "active_status=all" not in url2:
        print(f"  FAIL: page url missing country/status: {url2}")
        return 1

    # Political ads + sort
    url3 = eng._build_url(
        query="climate", mode="keyword", country="US",
        status="active", ad_type="political_and_issue_ads", media_type="VIDEO",
        search_type="keyword_exact_phrase", sort_by="impressions_monthly_grouped",
        page_ids=[], publisher_platforms=None, start_date="2024-01-01",
    )
    for frag in [
        "ad_type=political_and_issue_ads",
        "search_type=keyword_exact_phrase",
        "sort_data[mode]=impressions_monthly_grouped",
        "media_type=video",
        "start_date[min]=",
    ]:
        if frag not in url3:
            print(f"  FAIL: political url missing {frag!r}: {url3}")
            return 1

    print(f"  PASS: url builder, last url = {url3[:160]}...")
    return 0


def _smoke_param_validation() -> int:
    """Engine should reject invalid parameters with ValueError."""
    eng = MetaAdLibraryEngine.__new__(MetaAdLibraryEngine)
    eng.page = None  # guarded — won't be touched before validation
    eng.last_status = {}

    # Bad ad_type
    try:
        eng.search("test", limit=1, ad_type="bogus")
    except ValueError:
        pass
    except Exception as e:
        print(f"  FAIL: ad_type validation raised wrong exception: {e!r}")
        return 1
    else:
        print("  FAIL: ad_type=bogus should have raised ValueError")
        return 1

    # Bad mode
    try:
        eng.search("test", limit=1, mode="invalid_mode")
    except ValueError:
        pass
    except Exception as e:
        print(f"  FAIL: mode validation raised wrong exception: {e!r}")
        return 1
    else:
        print("  FAIL: mode=invalid_mode should have raised ValueError")
        return 1

    print("  PASS: param validation")
    return 0


def _smoke_dedup() -> int:
    """_collect_ads should dedup by collation_id then archive_id."""
    eng = MetaAdLibraryEngine.__new__(MetaAdLibraryEngine)
    fake_body = {
        "data": {
            "ad_library_main": {
                "search_results_connection": {
                    "edges": [
                        # Same collation_id, two different archive_ids
                        {"node": {
                            "collation_id": "C1",
                            "collated_results": [
                                {"ad_archive_id": "A1", "page_name": "Brand X",
                                 "body": {"text": "first"}},
                                {"ad_archive_id": "A2", "page_name": "Brand X",
                                 "body": {"text": "second"}},
                            ],
                        }},
                        # Different collation, unique archive
                        {"node": {
                            "collation_id": "C2",
                            "collated_results": [
                                {"ad_archive_id": "A3", "page_name": "Brand Y",
                                 "body": {"text": "third"}},
                            ],
                        }},
                    ]
                }
            }
        }
    }
    ads = eng._collect_ads([fake_body])
    if len(ads) != 2:
        print(f"  FAIL: dedup expected 2 (one per collation), got {len(ads)}: "
              f"{[a.get('ad_archive_id') for a in ads]}")
        return 1
    if {a.get("collation_id") for a in ads} != {"C1", "C2"}:
        print(f"  FAIL: collation ids wrong: {[a.get('collation_id') for a in ads]}")
        return 1
    print("  PASS: dedup by collation_id")
    return 0


def _smoke_field_extraction() -> int:
    """_ad_to_result should extract all the new fields when present."""
    eng = MetaAdLibraryEngine.__new__(MetaAdLibraryEngine)
    raw_ad = {
        "ad_archive_id": "999",
        "collation_id": "COL-1",
        "collation_count": 3,
        "page_name": "TestBrand",
        "page_id": "777",
        "page_like_count": 12345,
        "page_is_verified": True,
        "page_categories": {"cat1": "Software", "cat2": "Tech"},
        "is_active": True,
        "ad_status": "ACTIVE",
        "start_date": 1700000000,
        "end_date": 1700100000,
        "currency": "USD",
        "spend": {"lower_bound": 1000, "upper_bound": 5000},
        "impressions_with_index": {"lower_bound": 50_000, "upper_bound": 100_000},
        "reach": {"lower_bound": 25_000, "upper_bound": 60_000},
        "estimated_audience_size": {"lower_bound": 1_000_000, "upper_bound": 5_000_000},
        "demographic_distribution": [
            {"age": "18-24", "gender": "female", "percentage": 0.3},
            {"age": "25-34", "gender": "male", "percentage": 0.7},
        ],
        "delivery_by_region": [
            {"region": "California", "percentage": 0.5},
            {"region": "Texas", "percentage": 0.5},
        ],
        "publisher_platforms": ["facebook", "instagram"],
        "languages": ["en"],
        "funding_entity": "ACME PAC",
        "disclaimer": "Paid for by ACME",
        "bylines": ["John Doe"],
        "beneficiary_payers": [{"name": "ACME"}],
        "categories": ["politics"],
        "body": {"text": "Vote yes on prop 99"},
        "title": "Important message",
        "link_url": "https://example.com/?utm_term=climate+action&forceKeyA=clean+energy",
        "cta_text": "LEARN_MORE",
        "cta_type": "LEARN_MORE",
        "videos": [{"video_hd_url": "https://v/1.mp4", "video_sd_url": "https://v/1-sd.mp4",
                    "video_preview_image_url": "https://v/1.jpg"}],
        "images": [{"original_image_url": "https://i/1.jpg",
                    "resized_image_url": "https://i/1-thumb.jpg"}],
    }
    r = eng._ad_to_result(raw_ad, "US", extract_rsoc=True)

    checks = [
        ("ad_archive_id", "999"),
        ("collation_id", "COL-1"),
        ("collation_count", 3),
        ("page_name", "TestBrand"),
        ("page_like_count", 12345),
        ("page_verified", True),
        ("currency", "USD"),
        ("spend_lower", 1000), ("spend_upper", 5000),
        ("impressions_lower", 50_000), ("impressions_upper", 100_000),
        ("reach_lower", 25_000), ("reach_upper", 60_000),
        ("estimated_audience_size_lower", 1_000_000),
        ("funding_entity", "ACME PAC"),
        ("disclaimer", "Paid for by ACME"),
        ("ad_status", "ACTIVE"),
        ("body_text", "Vote yes on prop 99"),
        ("cta_text", "LEARN_MORE"),
    ]
    fail = 0
    for k, expected in checks:
        got = getattr(r, k, None)
        if got != expected:
            print(f"  FAIL {k}: got {got!r}, expected {expected!r}")
            fail += 1

    # list/dict fields
    if len(r.age_gender_distribution) != 2 or len(r.region_distribution) != 2:
        print(f"  FAIL distributions: ag={r.age_gender_distribution}, reg={r.region_distribution}")
        fail += 1
    if r.publisher_platforms != ["facebook", "instagram"]:
        print(f"  FAIL publisher_platforms: {r.publisher_platforms}")
        fail += 1
    if "Software" not in r.page_categories or "Tech" not in r.page_categories:
        print(f"  FAIL page_categories: {r.page_categories}")
        fail += 1
    if not r.video_url or not r.image_urls:
        print(f"  FAIL media: video_url={r.video_url} image_urls={r.image_urls}")
        fail += 1
    # RSOC: forceKeyA wins → only "clean energy"
    if r.rsoc_keywords != ["clean energy"]:
        print(f"  FAIL rsoc_keywords: {r.rsoc_keywords}")
        fail += 1

    if fail == 0:
        print("  PASS: field extraction")
    return fail


def _smoke_client_filters() -> int:
    """Filtering should drop ads that fall outside spend/impression bounds."""
    eng = MetaAdLibraryEngine.__new__(MetaAdLibraryEngine)
    ads = [
        {"ad_archive_id": "low", "spend": {"lower_bound": 100, "upper_bound": 500}},
        {"ad_archive_id": "mid", "spend": {"lower_bound": 1000, "upper_bound": 5000}},
        {"ad_archive_id": "hi",  "spend": {"lower_bound": 10000, "upper_bound": 50000}},
    ]
    out = eng._apply_filters(
        ads,
        min_impressions=None, max_impressions=None,
        min_spend=1000, max_spend=10000,
        start_date=None, end_date=None,
        languages=None, has_video=None, has_image=None,
    )
    ids = {a.get("ad_archive_id") for a in out}
    if ids != {"mid", "hi"}:
        print(f"  FAIL filter min_spend: kept {ids}, expected {{'mid', 'hi'}}")
        return 1
    print("  PASS: client filters")
    return 0


def _live_call() -> int:
    cfg = core.BrowserConfig(headless=True, humanize=False)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = MetaAdLibraryEngine(page)
        results = engine.search("shopify", limit=3, mode="keyword",
                                country="US", status="active")
    finally:
        browser.close()

    status = engine.last_status
    print(f"  last_status: {status}")

    if results:
        first = results[0]
        if not getattr(first, "ad_archive_id", ""):
            print("  FAIL: result missing ad_archive_id")
            return 1
        print(f"  PASS: live got {len(results)} ads (ad_id={first.ad_archive_id})")
        return 0

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
        ("helpers",        _smoke_helpers),
        ("url_builder",    _smoke_url_builder),
        ("param_validate", _smoke_param_validation),
        ("dedup",          _smoke_dedup),
        ("field_extract",  _smoke_field_extraction),
        ("client_filters", _smoke_client_filters),
        ("live_call",      _live_call),
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
