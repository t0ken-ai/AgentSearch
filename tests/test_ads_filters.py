"""Tests for the ``--filter key=val`` post-collection filter on
``agentsearch ads``.

Each test exercises one or more predicates produced by
:func:`agent_search.cli._parse_ad_filters` against synthetic
:class:`AdRecord` dicts so the test stays hermetic + fast.

Run::

    ~/tools/cloakbrowser/venv/bin/python tests/test_ads_filters.py
"""
from __future__ import annotations

import os
import sys
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search.cli import _parse_ad_filters


def t_min_impressions() -> int:
    preds = _parse_ad_filters(["min_impressions=10000"])
    fail = 0
    if not preds[0]({"impressions_upper": 50000}):
        print("  FAIL: 50000 upper >= 10000 should pass"); fail += 1
    if preds[0]({"impressions_upper": 5000}):
        print("  FAIL: 5000 upper < 10000 should fail"); fail += 1
    # Conservative: missing data → pass (don't drop ads silently)
    if not preds[0]({}):
        print("  FAIL: missing impressions data should pass conservatively"); fail += 1
    if fail == 0:
        print("  PASS: min_impressions")
    return fail


def t_max_spend() -> int:
    preds = _parse_ad_filters(["max_spend=1000"])
    fail = 0
    if preds[0]({"spend_lower": 5000}):
        print("  FAIL: lower 5000 > max 1000 should fail"); fail += 1
    if not preds[0]({"spend_lower": 500}):
        print("  FAIL: lower 500 <= max 1000 should pass"); fail += 1
    if fail == 0:
        print("  PASS: max_spend")
    return fail


def t_has_video_image() -> int:
    fail = 0
    pv = _parse_ad_filters(["has_video=true"])[0]
    pi = _parse_ad_filters(["has_image=true"])[0]
    rv = {"media_urls": ["https://x/v.mp4"]}
    ri = {"media_urls": ["https://x/i.png"]}
    rb = {"media_urls": ["https://x/v.mp4", "https://x/i.png"]}
    if not pv(rv) or pv(ri):
        print(f"  FAIL has_video: rv={pv(rv)} ri={pv(ri)}"); fail += 1
    if not pi(ri) or pi(rv):
        print(f"  FAIL has_image: ri={pi(ri)} rv={pi(rv)}"); fail += 1
    # Both present → both true
    if not (pv(rb) and pi(rb)):
        print("  FAIL: both kinds case"); fail += 1
    # has_video=false should INVERT
    pf = _parse_ad_filters(["has_video=false"])[0]
    if pf(rv) or not pf(ri):
        print("  FAIL: has_video=false inversion"); fail += 1
    if fail == 0:
        print("  PASS: has_video / has_image")
    return fail


def t_dates() -> int:
    fail = 0
    after = _parse_ad_filters(["last_seen_after=2026-04-01"])[0]
    if not after({"last_seen_iso": "2026-05-15"}):
        print("  FAIL: 2026-05-15 should be after 2026-04-01"); fail += 1
    if after({"last_seen_iso": "2026-03-15"}):
        print("  FAIL: 2026-03-15 should NOT be after 2026-04-01"); fail += 1
    # Missing date — '' < any date so it doesn't pass after-filter
    if after({}):
        print("  FAIL: missing date should not pass after-filter"); fail += 1

    before = _parse_ad_filters(["last_seen_before=2026-04-01"])[0]
    if not before({"last_seen_iso": "2026-03-15"}):
        print("  FAIL: 2026-03-15 should be before 2026-04-01"); fail += 1
    # Missing → defaults to '9999-12-31' so it FAILS before-filter
    # (we don't want to silently drop date-less records when user
    #  asked for a date-bounded view)
    if before({}):
        print("  FAIL: missing date should not pass before-filter"); fail += 1

    if fail == 0:
        print("  PASS: date filters")
    return fail


def t_country() -> int:
    fail = 0
    p = _parse_ad_filters(["country=US"])[0]
    if not p({"country": "US"}):
        print("  FAIL: US"); fail += 1
    if not p({"country": "us"}):
        print("  FAIL: lowercase us should match"); fail += 1
    if p({"country": "GB"}):
        print("  FAIL: GB should not match"); fail += 1
    if p({}):
        print("  FAIL: missing country should not match US"); fail += 1
    if fail == 0:
        print("  PASS: country")
    return fail


def t_advertiser_contains() -> int:
    p = _parse_ad_filters(["advertiser_contains=nike"])[0]
    fail = 0
    if not p({"advertiser_name": "NIKE Korea"}):
        print("  FAIL: NIKE Korea should contain 'nike'"); fail += 1
    if p({"advertiser_name": "Adidas"}):
        print("  FAIL: Adidas does not contain 'nike'"); fail += 1
    if p({}):
        print("  FAIL: empty name should not match"); fail += 1
    if fail == 0:
        print("  PASS: advertiser_contains")
    return fail


def t_and_chain() -> int:
    """Multiple --filter flags AND together."""
    preds = _parse_ad_filters([
        "country=US",
        "has_video=true",
        "min_days_running=10",
    ])
    if len(preds) != 3:
        print(f"  FAIL: expected 3 preds, got {len(preds)}")
        return 1
    d_ok = {"country": "US", "media_urls": ["x.mp4"], "days_running": 30}
    d_bad = {"country": "US", "media_urls": ["x.mp4"], "days_running": 5}
    if not all(p(d_ok) for p in preds):
        print(f"  FAIL: ok record should pass all 3"); return 1
    if all(p(d_bad) for p in preds):
        print(f"  FAIL: bad record should fail at days check"); return 1
    print("  PASS: AND chain (3 preds)")
    return 0


def t_errors() -> int:
    fail = 0
    try:
        _parse_ad_filters(["unknown=1"])
    except ValueError as e:
        if "unknown filter key" not in str(e):
            print(f"  FAIL: wrong message: {e}"); fail += 1
    else:
        print("  FAIL: unknown key should raise"); fail += 1

    try:
        _parse_ad_filters(["malformed"])
    except ValueError as e:
        if "key=val" not in str(e):
            print(f"  FAIL: wrong message: {e}"); fail += 1
    else:
        print("  FAIL: malformed should raise"); fail += 1

    if fail == 0:
        print("  PASS: error cases")
    return fail


def t_score_and_active() -> int:
    fail = 0
    p_score = _parse_ad_filters(["min_score=2.0"])[0]
    if not p_score({"score": 5.0}):
        print("  FAIL: score 5 >= 2"); fail += 1
    if p_score({"score": 1.0}):
        print("  FAIL: score 1 < 2 should fail"); fail += 1
    # Missing → conservative pass
    if not p_score({}):
        print("  FAIL: missing score should pass conservatively"); fail += 1

    p_active = _parse_ad_filters(["is_active=true"])[0]
    if not p_active({"is_active": True}):
        print("  FAIL: True"); fail += 1
    if p_active({"is_active": False}):
        print("  FAIL: False"); fail += 1
    if p_active({}):
        print("  FAIL: missing should not match true"); fail += 1
    if fail == 0:
        print("  PASS: score + is_active")
    return fail


def main() -> int:
    print("=== test_ads_filters ===")
    failures = 0
    for label, fn in [
        ("min_impressions",   t_min_impressions),
        ("max_spend",         t_max_spend),
        ("has_video_image",   t_has_video_image),
        ("dates",             t_dates),
        ("country",           t_country),
        ("advertiser_contains", t_advertiser_contains),
        ("and_chain",         t_and_chain),
        ("errors",            t_errors),
        ("score_and_active",  t_score_and_active),
    ]:
        print(f"\n[{label}]")
        try:
            failures += fn()
        except Exception:
            traceback.print_exc()
            failures += 1
    print(f"\n{'PASS' if failures == 0 else f'{failures} FAIL'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
