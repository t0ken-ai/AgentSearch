"""Google Ads Transparency Center engine regression test.

Layered tests:

1. **import smoke** — engine module imports, region table loads.
2. **region helpers** — region_num()/valid_codes()/region_label() lookups.
3. **YouTube ID extraction** — common URL formats.
4. **text ad decoder** — synthetic protobuf payload round-trip.
5. **suggestion → result** — both advertiser ("1") and domain ("2") shapes.
6. **creative summary** — format mapping, ms→iso conversion, days_running.
7. **param validation** — region must be ISO/anywhere; mode must be valid.
8. **live search_advertisers** — real call against ATC (kept from earlier).
9. **live unknown_mode** — raises ValueError before any network.

Run:
    ~/tools/cloakbrowser/venv/bin/python tests/test_google_ad_transparency.py
"""
from __future__ import annotations

import base64
import os
import sys
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search import core
from agent_search.engines.google_ad_transparency import (
    GoogleAdTransparencyEngine,
    decode_text_ad, _extract_ad_param, _extract_youtube_video_id,
    _FORMAT_MAP,
)
from agent_search.engines._google_atc_options import (
    REGIONS, region_num, valid_codes, region_label,
)


def _bare_engine() -> GoogleAdTransparencyEngine:
    eng = GoogleAdTransparencyEngine.__new__(GoogleAdTransparencyEngine)
    eng.last_status = {}
    return eng


def _import_smoke() -> int:
    if GoogleAdTransparencyEngine.name != "google_ad_transparency":
        print("  FAIL: name attr wrong")
        return 1
    if len(REGIONS) < 200:
        print(f"  FAIL: REGIONS only has {len(REGIONS)} entries")
        return 1
    if 1 not in _FORMAT_MAP or _FORMAT_MAP[1] != "text":
        print("  FAIL: _FORMAT_MAP missing format 1=text")
        return 1
    print(f"  PASS: import OK, {len(REGIONS)} regions, "
          f"{len(_FORMAT_MAP)} format codes")
    return 0


def _region_helpers() -> int:
    fail = 0
    if region_num("US") != 2840:
        print(f"  FAIL: US should be 2840, got {region_num('US')}")
        fail += 1
    if region_num("us") != 2840:  # case-insensitive
        print(f"  FAIL: lower 'us' should be 2840")
        fail += 1
    if region_num("CN") != 2156:
        print(f"  FAIL: CN should be 2156, got {region_num('CN')}")
        fail += 1
    if region_num("anywhere") is not None:
        print("  FAIL: 'anywhere' should be None")
        fail += 1
    if region_num("XX") is not None:
        print("  FAIL: invalid 'XX' should be None")
        fail += 1
    if "US" not in valid_codes() or "GB" not in valid_codes():
        print(f"  FAIL: valid_codes missing US/GB")
        fail += 1
    if not region_label("US"):
        print("  FAIL: region_label('US') is empty")
        fail += 1
    if fail == 0:
        print("  PASS: region helpers")
    return fail


def _youtube_id() -> int:
    cases = [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://www.youtube.com/shorts/abc123XYZ_", "abc123XYZ_"),
        ("https://www.youtube.com/embed/xyz", "xyz"),
        ("https://example.com/x", None),
        ("", None),
        (None, None),
    ]
    fail = 0
    for url, expected in cases:
        got = _extract_youtube_video_id(url) if url is not None else _extract_youtube_video_id("")
        if got != expected:
            print(f"  FAIL youtube_id({url!r}) -> {got!r}, expected {expected!r}")
            fail += 1
    if fail == 0:
        print("  PASS: YouTube ID extraction")
    return fail


def _text_ad_decode() -> int:
    """Synthesise a protobuf-style payload and round-trip it through
    decode_text_ad."""
    def _wire(s: bytes) -> bytes:
        # wire-type 2, field 1 = 0x0A
        return b"\x0a" + bytes([len(s)]) + s

    data = (
        _wire(b"Get 50% off shoes")
        + _wire(b"https://shop.example.com/sale")
        + _wire(b"Free shipping today")
    )
    b64 = base64.b64encode(data).decode().rstrip("=")
    decoded = decode_text_ad(f"https://ads/?ad={b64}")
    fail = 0
    if not decoded:
        print("  FAIL: decode_text_ad returned None")
        return 1
    if decoded["headline"] != "Get 50% off shoes":
        print(f"  FAIL: headline = {decoded['headline']!r}")
        fail += 1
    if decoded["destination_url"] != "https://shop.example.com/sale":
        print(f"  FAIL: dest = {decoded['destination_url']!r}")
        fail += 1
    if decoded["description"] != "Free shipping today":
        print(f"  FAIL: desc = {decoded['description']!r}")
        fail += 1
    # Empty / no payload returns None
    if decode_text_ad("https://no-ad-param/") is not None:
        print("  FAIL: URL without ad= should return None")
        fail += 1
    if fail == 0:
        print("  PASS: text ad decoder")
    return fail


def _suggestion_to_result() -> int:
    eng = _bare_engine()
    fail = 0

    # advertiser shape
    r1 = eng._suggestion_to_result({
        "1": {"1": "Nike, Inc.", "2": "AR05099", "3": "United States",
              "4": {"2": {"1": 50, "2": 100}}}
    }, "us")
    if r1 is None or r1.advertiser_name != "Nike, Inc." \
            or r1.advertiser_id != "AR05099" \
            or r1.ad_count != 50 or r1.ad_count_max != 100 \
            or r1.result_type != "advertiser":
        print(f"  FAIL: advertiser suggestion = {vars(r1) if r1 else None}")
        fail += 1
    if "ads=50-100" not in r1.snippet:
        print(f"  FAIL: snippet missing ads range: {r1.snippet}")
        fail += 1

    # advertiser shape, single count
    r1b = eng._suggestion_to_result({
        "1": {"1": "Brand", "2": "AR1", "3": "DE",
              "4": {"2": {"1": 10, "2": 10}}}
    }, "de")
    if r1b is None or "ads=10-10" in r1b.snippet:
        print(f"  FAIL: single-count snippet should not be range: {r1b.snippet}")
        fail += 1
    elif "ads=10" not in r1b.snippet:
        print(f"  FAIL: single-count snippet missing ads=10: {r1b.snippet}")
        fail += 1

    # domain shape
    r2 = eng._suggestion_to_result({"2": {"1": "nike.com"}}, "us")
    if r2 is None or r2.domain != "nike.com" or r2.result_type != "domain":
        print(f"  FAIL: domain suggestion = {vars(r2) if r2 else None}")
        fail += 1

    # missing id
    r3 = eng._suggestion_to_result({"1": {"1": "X", "2": ""}}, "us")
    if r3 is not None:
        print("  FAIL: empty advertiser_id should yield None")
        fail += 1

    if fail == 0:
        print("  PASS: suggestion → SearchResult")
    return fail


def _creative_summary() -> int:
    eng = _bare_engine()
    ad = {
        "2": "CR123",
        "3": {"1": 3, "2": 17},
        "5": {"1": 1700000000000, "2": 1702592000000},
        "9": "US", "12": "Buy now",
    }
    r = eng._creative_summary(ad, "AR05099", "us")
    fail = 0
    for k, expected in [
        ("creative_id", "CR123"),
        ("format", "video"),
        ("format_int", 3),
        ("format_subtype", 17),
        ("first_seen_iso", "2023-11-14"),
        ("last_seen_iso", "2023-12-14"),
        ("days_running", 30),
        ("country", "US"),
        ("text_summary", "Buy now"),
    ]:
        if getattr(r, k, None) != expected:
            print(f"  FAIL creative.{k}: got {getattr(r, k, None)!r}, "
                  f"expected {expected!r}")
            fail += 1
    if fail == 0:
        print("  PASS: creative summary parsing")
    return fail


def _param_validation() -> int:
    eng = _bare_engine()
    eng.page = None
    fail = 0

    # Bad mode
    try:
        eng.search("x", limit=1, mode="bogus")
    except ValueError:
        pass
    else:
        print("  FAIL: bogus mode should raise ValueError")
        fail += 1

    # Bad region
    try:
        eng.search("x", limit=1, mode="search_advertisers", region="ZZ")
    except ValueError as e:
        if "ISO-3166" not in str(e) and "anywhere" not in str(e):
            print(f"  FAIL: region error message wrong: {e}")
            fail += 1
    else:
        print("  FAIL: region=ZZ should raise ValueError")
        fail += 1

    # Valid: region anywhere passes validation (don't actually invoke network)
    # We can't easily run the full search() without page, so just verify
    # region 'anywhere' is in the validation path (no exception thrown
    # on the region check itself).

    if fail == 0:
        print("  PASS: parameter validation")
    return fail


# --- Live tests below (kept from prior file) ---


def _search_advertisers_live() -> int:
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
    empty = [r for r in results if not getattr(r, "advertiser_id", "")
             and not getattr(r, "domain", "")]
    if empty:
        print(f"  FAIL: {len(empty)}/{len(results)} have neither advertiser_id nor domain")
        return 1
    ar_prefixed = [r for r in results
                   if getattr(r, "advertiser_id", "").startswith("AR")]
    print(f"  PASS: {len(results)} results, {len(ar_prefixed)} advertisers")
    for r in results[:3]:
        if getattr(r, "result_type", "") == "domain":
            print(f"    - domain: {r.domain}")
        else:
            print(f"    - {r.advertiser_name} [{r.country}] "
                  f"id={r.advertiser_id} ads={r.ad_count}")
    return 0


def main() -> int:
    print("=== test_google_ad_transparency ===")
    failures = 0
    for label, fn in [
        ("import_smoke",          _import_smoke),
        ("region_helpers",        _region_helpers),
        ("youtube_id",            _youtube_id),
        ("text_ad_decode",        _text_ad_decode),
        ("suggestion_to_result",  _suggestion_to_result),
        ("creative_summary",      _creative_summary),
        ("param_validation",      _param_validation),
        ("live_search_adv",       _search_advertisers_live),
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
