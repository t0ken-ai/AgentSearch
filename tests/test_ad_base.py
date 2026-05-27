"""Cross-platform AdRecord schema regression test.

Ensures :func:`to_ad_record` correctly normalises :class:`SearchResult`
objects from each of the five ad engines into a uniform
:class:`AdRecord` shape.

Run:
    ~/tools/cloakbrowser/venv/bin/python tests/test_ad_base.py
"""
from __future__ import annotations

import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search.engines.base import SearchResult
from agent_search.engines._ad_base import AdRecord, to_ad_record


def _meta_result() -> SearchResult:
    r = SearchResult(title="Test ad", url="https://fb.com/ads/library/?id=99",
                     snippet="x")
    r.__dict__.update({
        "ad_archive_id": "99",
        "page_name": "Brand X",
        "page_id": "777",
        "is_active": True,
        "start_date": 1700000000,
        "end_date": 1702592000,
        "days_running": 30,
        "country": "US",
        "title": "Big sale",
        "body_text": "50% off everything",
        "cta_text": "SHOP_NOW",
        "link_url": "https://example.com/sale",
        "image_urls": ["https://i/1.jpg", "https://i/2.jpg"],
        "video_url": "https://v/1.mp4",
        "video_urls": ["https://v/1.mp4"],
        "publisher_platforms": ["facebook", "instagram"],
        "spend_lower": 1000, "spend_upper": 5000,
        "impressions_lower": 50_000, "impressions_upper": 100_000,
        "currency": "USD",
    })
    return r


def _ig_result() -> SearchResult:
    r = _meta_result()
    r.__dict__["platform"] = "instagram"
    r.__dict__["placement"] = "reels"
    return r


def _tiktok_cc_result() -> SearchResult:
    r = SearchResult(title="TT ad", url="https://x", snippet="")
    r.__dict__.update({
        "ad_id": "999",
        "brand_name": "Starbucks",
        "industry_key": "label_23116000000",
        "ctr": 0.34,
        "video_url": "https://v/720p.mp4",
        "video_urls": {"720p": "https://v/720p.mp4", "540p": "https://v/540p.mp4"},
        "cover_image_url": "https://c/cover.jpg",
        "country_code": "US",
    })
    return r


def _tiktok_lib_result() -> SearchResult:
    r = SearchResult(title="TT lib", url="https://library.tiktok.com/x", snippet="")
    r.__dict__.update({
        "ad_id": "C-123",
        "advertiser_name": "Acme",
        "advertiser_id": "ADV_1",
        "first_shown": 1700000000000,
        "last_shown": 1702592000000,
        "region": "GB",
        "text": "Buy now",
        "video_url": "https://v.tiktok/x.mp4",
        "image_url": "https://i.tiktok/x.jpg",
    })
    return r


def _google_result() -> SearchResult:
    r = SearchResult(title="G ad", url="https://atc/x", snippet="")
    r.__dict__.update({
        "advertiser_id": "AR1",
        "creative_id": "CR1",
        "format": "text",
        "format_int": 1,
        "first_seen_iso": "2023-11-14",
        "last_seen_iso": "2023-12-14",
        "days_running": 30,
        "country": "US",
        "headline": "Get 50% off",
        "description": "Free shipping",
        "destination_url": "https://example.com",
        "region": "US",
    })
    return r


def test_google_search_advertisers() -> int:
    """SearchSuggestions output (no creative_id, just AR-prefixed
    advertiser_id) should still infer platform=google_atc."""
    r = SearchResult(title="Coinbase", url="https://atc/x", snippet="")
    r.__dict__.update({
        "advertiser_id": "AR07375484949278752769",
        "advertiser_name": "Coinbase",
        "country": "US",
        "ad_count": 3,
        "ad_count_max": 3,
        "result_type": "advertiser",
        "region": "US",
    })
    rec = to_ad_record(r)
    if rec.platform != "google_atc":
        print(f"  FAIL: search_advertisers → platform={rec.platform!r}")
        return 1
    if not rec.advertiser_id.startswith("AR"):
        print(f"  FAIL: lost advertiser_id: {rec.advertiser_id}")
        return 1
    print("  PASS: google search_advertisers → google_atc")
    return 0


def test_google_domain_result() -> int:
    """domain mode result also has no creative_id; result_type='domain'
    should not flip platform to anything other than google_atc."""
    r = SearchResult(title="nike.com", url="https://atc", snippet="")
    r.__dict__.update({
        "domain": "nike.com",
        "result_type": "domain",
        "region": "US",
    })
    rec = to_ad_record(r)
    if rec.platform != "google_atc":
        print(f"  FAIL: domain typehead → platform={rec.platform!r}")
        return 1
    print("  PASS: google domain typehead → google_atc")
    return 0


def test_meta() -> int:
    rec = to_ad_record(_meta_result())
    fail = 0
    if rec.platform != "meta":
        print(f"  FAIL platform = {rec.platform}, expected 'meta'"); fail += 1
    if rec.ad_id != "99":
        print(f"  FAIL ad_id = {rec.ad_id}"); fail += 1
    if rec.advertiser_name != "Brand X" or rec.advertiser_id != "777":
        print(f"  FAIL advertiser: {rec.advertiser_name} / {rec.advertiser_id}"); fail += 1
    if rec.country != "US":
        print(f"  FAIL country = {rec.country}"); fail += 1
    if rec.first_seen_iso != "2023-11-14":
        print(f"  FAIL first_iso = {rec.first_seen_iso}"); fail += 1
    if rec.spend_lower != 1000 or rec.impressions_upper != 100_000:
        print("  FAIL impression/spend"); fail += 1
    if "https://i/1.jpg" not in rec.media_urls or "https://v/1.mp4" not in rec.media_urls:
        print(f"  FAIL media_urls = {rec.media_urls}"); fail += 1
    if rec.landing_url != "https://example.com/sale":
        print(f"  FAIL landing = {rec.landing_url}"); fail += 1
    if "Big sale" not in rec.copy_text or "50% off" not in rec.copy_text:
        print(f"  FAIL copy_text = {rec.copy_text}"); fail += 1
    if rec.cta_text != "SHOP_NOW":
        print(f"  FAIL cta = {rec.cta_text}"); fail += 1
    if fail == 0:
        print("  PASS: meta")
    return fail


def test_ig() -> int:
    rec = to_ad_record(_ig_result())
    if rec.platform != "instagram":
        print(f"  FAIL ig platform = {rec.platform}")
        return 1
    print("  PASS: instagram")
    return 0


def test_tiktok_cc() -> int:
    rec = to_ad_record(_tiktok_cc_result())
    fail = 0
    if rec.platform != "tiktok_cc":
        print(f"  FAIL platform = {rec.platform}, expected tiktok_cc"); fail += 1
    if rec.ad_id != "999" or rec.advertiser_name != "Starbucks":
        print(f"  FAIL ttcc id/adv: {rec.ad_id}/{rec.advertiser_name}"); fail += 1
    if rec.country != "US":
        print(f"  FAIL country = {rec.country}"); fail += 1
    if rec.score != 0.34:
        print(f"  FAIL score = {rec.score}"); fail += 1
    if "https://v/720p.mp4" not in rec.media_urls:
        print(f"  FAIL media: {rec.media_urls}"); fail += 1
    if "https://c/cover.jpg" not in rec.media_urls:
        print(f"  FAIL cover not in media: {rec.media_urls}"); fail += 1
    if fail == 0:
        print("  PASS: tiktok_cc")
    return fail


def test_tiktok_lib() -> int:
    rec = to_ad_record(_tiktok_lib_result())
    fail = 0
    if rec.platform != "tiktok_lib":
        print(f"  FAIL platform = {rec.platform}"); fail += 1
    if rec.ad_id != "C-123" or rec.advertiser_id != "ADV_1":
        print(f"  FAIL"); fail += 1
    if rec.first_seen_iso != "2023-11-14":
        print(f"  FAIL first_iso = {rec.first_seen_iso}"); fail += 1
    if "Buy now" not in rec.copy_text:
        print(f"  FAIL copy = {rec.copy_text}"); fail += 1
    if fail == 0:
        print("  PASS: tiktok_lib")
    return fail


def test_google() -> int:
    rec = to_ad_record(_google_result())
    fail = 0
    if rec.platform != "google_atc":
        print(f"  FAIL platform = {rec.platform}"); fail += 1
    if rec.ad_id != "CR1" or rec.advertiser_id != "AR1":
        print(f"  FAIL ids: {rec.ad_id} / {rec.advertiser_id}"); fail += 1
    if rec.first_seen_iso != "2023-11-14" or rec.last_seen_iso != "2023-12-14":
        print(f"  FAIL dates: {rec.first_seen_iso} / {rec.last_seen_iso}"); fail += 1
    if rec.landing_url != "https://example.com":
        print(f"  FAIL landing = {rec.landing_url}"); fail += 1
    if "Get 50% off" not in rec.copy_text or "Free shipping" not in rec.copy_text:
        print(f"  FAIL copy = {rec.copy_text}"); fail += 1
    if fail == 0:
        print("  PASS: google_atc")
    return fail


def test_to_dict() -> int:
    """AdRecord.to_dict round-trips."""
    rec = AdRecord(platform="meta", ad_id="1", advertiser_name="X")
    d = rec.to_dict()
    if d["platform"] != "meta" or d["ad_id"] != "1":
        print(f"  FAIL to_dict: {d}")
        return 1
    print("  PASS: to_dict")
    return 0


def main() -> int:
    print("=== test_ad_base ===")
    failures = 0
    for label, fn in [
        ("meta",         test_meta),
        ("instagram",    test_ig),
        ("tiktok_cc",    test_tiktok_cc),
        ("tiktok_lib",   test_tiktok_lib),
        ("google_atc",   test_google),
        ("google_search_advertisers", test_google_search_advertisers),
        ("google_domain", test_google_domain_result),
        ("to_dict",      test_to_dict),
    ]:
        print(f"\n[{label}]")
        try:
            failures += fn()
        except Exception as e:
            print(f"  FAIL: {type(e).__name__}: {e}")
            import traceback; traceback.print_exc()
            failures += 1
    print(f"\n{'PASS' if failures == 0 else f'{failures} FAIL'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
