"""App Store URL parser tests.

Layered:

1. **classifier** — URL → (store, app_id) for Apple / Google / unknown.
2. **domain helper** — _domain_of strips to eTLD+1.
3. **dataclass** — AppMetadata.to_dict round-trip.
4. **live Apple** — iTunes Search API smoke (Instagram / TikTok), proves
   the JSON shape we depend on.
5. **live Google Play** — HTML scrape smoke, proves the regex set still
   matches Google Play's current markup.

Run::

    ~/tools/cloakbrowser/venv/bin/python tests/test_app_store.py
"""
from __future__ import annotations

import os
import sys
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search.engines._app_store import (
    AppMetadata, classify_app_url, lookup_app, lookup_apple, lookup_google,
    _domain_of,
)


def t_classify() -> int:
    fail = 0
    cases = [
        ("https://apps.apple.com/us/app/instagram/id389801252", ("apple", "389801252")),
        ("https://apps.apple.com/jp/app/tiktok/id835599320?l=en", ("apple", "835599320")),
        ("https://itunes.apple.com/lookup?id=123456789", ("apple", None)),
        ("https://play.google.com/store/apps/details?id=com.shopify.mobile",
         ("google", "com.shopify.mobile")),
        ("https://example.com/foo", ("unknown", None)),
        ("", ("unknown", None)),
        (None, ("unknown", None)),
    ]
    for url, expected in cases:
        got = classify_app_url(url)
        if got != expected:
            print(f"  FAIL classify({url!r}) -> {got}, expected {expected}")
            fail += 1
    if fail == 0:
        print("  PASS: classify (7 cases)")
    return fail


def t_domain() -> int:
    fail = 0
    cases = [
        ("http://www.shopify.com/mobile", "shopify.com"),
        ("https://Instagram.com/", "instagram.com"),
        ("https://example.co.uk/path", "example.co.uk"),
        ("", ""),
        ("not-a-url", ""),
    ]
    for url, expected in cases:
        got = _domain_of(url)
        if got != expected:
            print(f"  FAIL _domain_of({url!r}) -> {got!r}, expected {expected!r}")
            fail += 1
    if fail == 0:
        print("  PASS: _domain_of (5 cases)")
    return fail


def t_dataclass() -> int:
    m = AppMetadata(
        store="apple", app_id="123", bundle_id="com.x.y",
        title="Foo", developer_name="Foo Inc.",
        website="https://foo.com/", domain="foo.com",
    )
    d = m.to_dict()
    if d["app_id"] != "123" or d["domain"] != "foo.com":
        print(f"  FAIL: {d}")
        return 1
    print("  PASS: AppMetadata.to_dict round-trip")
    return 0


# Live tests below — guarded so a no-network env still passes the unit
# tier. Skip by setting AGENTSEARCH_SKIP_LIVE=1.

def _live_skip() -> bool:
    return os.environ.get("AGENTSEARCH_SKIP_LIVE", "0") == "1"


def _proxies():
    p = os.environ.get("FLUXISP_PROXY")
    return ({"https": p, "http": p}) if p else None


def t_live_apple_instagram() -> int:
    if _live_skip():
        print("  SKIP: AGENTSEARCH_SKIP_LIVE=1")
        return 0
    m = lookup_apple("389801252", proxies=_proxies())
    if not m or m.developer_name not in ("Instagram, Inc.", "Instagram Inc.",
                                          "Instagram"):
        print(f"  FAIL: {m}")
        return 1
    if m.domain != "instagram.com":
        print(f"  FAIL domain: {m.domain}")
        return 1
    # Extended-fields sanity: Apple lookups should populate plenty of
    # detail. Only assert structural — the live values change with
    # every Instagram release.
    fail = 0
    for field_name, predicate in [
        ("description",      lambda v: isinstance(v, str) and len(v) > 100),
        ("icon_url",         lambda v: isinstance(v, str) and v.startswith("http")),
        ("screenshot_urls",  lambda v: isinstance(v, list) and len(v) >= 1),
        ("rating_count",     lambda v: isinstance(v, int) and v > 0),
        ("version",          lambda v: isinstance(v, str) and v),
        ("release_date_iso", lambda v: isinstance(v, str) and v.startswith("20")),
        ("last_updated_iso", lambda v: isinstance(v, str) and v.startswith("20")),
        ("size_bytes",       lambda v: isinstance(v, int) and v > 0),
        ("min_os",           lambda v: isinstance(v, str) and v),
        ("languages",        lambda v: isinstance(v, list) and len(v) >= 1),
        ("genres",           lambda v: isinstance(v, list) and "Photo & Video" in v),
        ("price_str",        lambda v: isinstance(v, str)),
        ("currency",         lambda v: v == "USD"),
    ]:
        if not predicate(getattr(m, field_name)):
            print(f"  FAIL extended.{field_name}: {getattr(m, field_name)!r}")
            fail += 1
    if fail == 0:
        print(f"  PASS: Apple Instagram lookup ({m.developer_name}, "
              f"v{m.version}, {m.size_bytes // (1024*1024)}MB, "
              f"{m.rating_count} ratings)")
    return fail


def t_live_google_shopify() -> int:
    if _live_skip():
        print("  SKIP: AGENTSEARCH_SKIP_LIVE=1")
        return 0
    m = lookup_google("com.shopify.mobile", proxies=_proxies())
    if not m:
        print("  FAIL: no metadata returned")
        return 1
    if "Shopify" not in (m.developer_name or m.title):
        print(f"  FAIL: developer/title doesn't mention Shopify: "
              f"dev={m.developer_name!r} title={m.title!r}")
        return 1
    if m.domain != "shopify.com":
        print(f"  FAIL domain: {m.domain}")
        return 1
    print(f"  PASS: Google Play Shopify lookup "
          f"(dev={m.developer_name!r}, domain={m.domain})")
    return 0


def t_live_one_shot() -> int:
    if _live_skip():
        print("  SKIP: AGENTSEARCH_SKIP_LIVE=1")
        return 0
    # Bare numeric → Apple
    m1 = lookup_app("835599320", proxies=_proxies())
    if not m1 or m1.store != "apple" or "tiktok" not in (m1.domain or "").lower():
        print(f"  FAIL bare-id: {m1}")
        return 1
    # Bare package → Google
    m2 = lookup_app("com.spotify.music", proxies=_proxies())
    if not m2 or m2.store != "google":
        print(f"  FAIL bare-pkg: {m2}")
        return 1
    print(f"  PASS: lookup_app routing "
          f"(835599320 → {m1.title}, com.spotify.music → {m2.title})")
    return 0


def main() -> int:
    print("=== test_app_store ===")
    failures = 0
    for label, fn in [
        ("classify",            t_classify),
        ("_domain_of",          t_domain),
        ("dataclass",           t_dataclass),
        ("live_apple",          t_live_apple_instagram),
        ("live_google",         t_live_google_shopify),
        ("live_one_shot",       t_live_one_shot),
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
