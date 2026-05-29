"""facebook_docs engine regression test.

Layered:

1. **import smoke + alias registration**
2. **query builder** — verify ddg_query construction for each
   product / mode / api_version permutation.
3. **section / product / version inferers** — pure URL-string tests.
4. **live search** — actual `site:developers.facebook.com` query
   through DDG. Counts as PASS when ≥3 results land on the right
   domain.

Run::

    ~/tools/cloakbrowser/venv/bin/python tests/test_facebook_docs.py
"""
from __future__ import annotations

import os
import sys
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search.engines.facebook_docs import FacebookDocsEngine, _PRODUCTS


def _bare() -> FacebookDocsEngine:
    eng = FacebookDocsEngine.__new__(FacebookDocsEngine)
    eng.last_status = {}
    return eng


def t_import_smoke() -> int:
    if FacebookDocsEngine.name != "facebook_docs":
        print(f"  FAIL: name = {FacebookDocsEngine.name}")
        return 1
    if FacebookDocsEngine.SITE != "developers.facebook.com":
        print(f"  FAIL: SITE = {FacebookDocsEngine.SITE}")
        return 1
    print("  PASS: name + SITE constants")
    return 0


def t_alias_registered() -> int:
    from agent_search.cli import _engine_registry
    reg = _engine_registry()
    fail = 0
    for handle in ("facebook_docs", "fb_docs", "meta_docs", "fb_dev"):
        if handle not in reg:
            print(f"  FAIL: {handle!r} not in CLI registry")
            fail += 1
    if fail == 0:
        print("  PASS: 4 aliases registered (facebook_docs / fb_docs / "
              "meta_docs / fb_dev)")
    return fail


def t_query_builder() -> int:
    eng = _bare()
    cases = [
        # (query, mode, product, api_version) → must contain these substrings
        (("ad creation", "search", "marketing-api", None),
         ["site:developers.facebook.com",
          "(inurl:marketing-api)", "ad creation"]),
        (("adcreative", "reference", "marketing-api", "v25.0"),
         ["inurl:reference", '"v25.0"', "adcreative"]),
        (("breaking changes", "changelog", "graph-api", None),
         ["inurl:changelog OR inurl:release-notes",
          "(inurl:graph-api)", "breaking changes"]),
        (("webhook", "search", "whatsapp-business", None),
         ["inurl:whatsapp", "inurl:whatsapp-business-platform"]),
        # Unknown product — falls through as plain inurl
        (("foo", "search", "custom-platform", None),
         ["inurl:custom-platform"]),
    ]
    fail = 0
    for (query, mode, product, ver), needles in cases:
        q = eng._build_ddg_query(query, mode, product, ver)
        for needle in needles:
            if needle not in q:
                print(f"  FAIL: query={query!r} mode={mode!r} "
                      f"missing {needle!r} in: {q}")
                fail += 1
    if fail == 0:
        print(f"  PASS: query builder ({len(cases)} cases)")
    return fail


def t_url_inferers() -> int:
    inf_section = FacebookDocsEngine._infer_section
    inf_product = FacebookDocsEngine._infer_product
    inf_version = FacebookDocsEngine._infer_version

    fail = 0
    section_cases = [
        ("https://developers.facebook.com/docs/marketing-api/reference/adcreative/", "reference"),
        ("https://developers.facebook.com/docs/graph-api/changelog/", "changelog"),
        ("https://developers.facebook.com/docs/marketing-api/get-started/", "quickstart"),
        ("https://developers.facebook.com/docs/whatsapp/webhooks/", "webhook"),
        ("https://developers.facebook.com/docs/marketing-api/", "guide"),
    ]
    for url, expected in section_cases:
        got = inf_section(url)
        if got != expected:
            print(f"  FAIL section({url!r}) -> {got!r}, expected {expected!r}")
            fail += 1

    product_cases = [
        ("https://developers.facebook.com/docs/marketing-api/", "marketing-api"),
        ("https://developers.facebook.com/docs/graph-api/changelog", "graph-api"),
        ("https://developers.facebook.com/docs/whatsapp-business-platform/", "whatsapp-business"),
        ("https://developers.facebook.com/docs/messenger-platform/", "messenger"),
    ]
    for url, expected in product_cases:
        got = inf_product(url)
        if got != expected:
            print(f"  FAIL product({url!r}) -> {got!r}, expected {expected!r}")
            fail += 1

    version_cases = [
        ("https://developers.facebook.com/docs/marketing-api/reference/v25.0/", "v25.0"),
        ("https://developers.facebook.com/docs/graph-api/reference/v21.0/", "v21.0"),
        ("https://developers.facebook.com/docs/marketing-api/", ""),
    ]
    for url, expected in version_cases:
        got = inf_version(url)
        if got != expected:
            print(f"  FAIL version({url!r}) -> {got!r}, expected {expected!r}")
            fail += 1

    if fail == 0:
        print(f"  PASS: section / product / version inferers "
              f"({len(section_cases) + len(product_cases) + len(version_cases)} cases)")
    return fail


def t_invalid_mode() -> int:
    eng = _bare()
    eng.page = None  # not touched before validation
    try:
        eng.search("x", mode="bogus")
    except ValueError as e:
        if "search" in str(e) or "reference" in str(e):
            print("  PASS: unknown mode raises ValueError")
            return 0
    print("  FAIL: unknown mode should raise ValueError")
    return 1


def t_live_search() -> int:
    """Hit the real DDG site-search and verify ≥3 results land on the
    Meta developer portal."""
    if os.environ.get("AGENTSEARCH_SKIP_LIVE", "0") == "1":
        print("  SKIP: AGENTSEARCH_SKIP_LIVE=1")
        return 0
    from agent_search.core import BrowserConfig, launch, new_page
    cfg = BrowserConfig(headless=True, humanize=False, proxy=None)
    b = launch(cfg)
    try:
        page = new_page(b)
        eng = FacebookDocsEngine(page)
        results = eng.search("ad creation campaigns", limit=5,
                             product="marketing-api")
    finally:
        b.close()

    fb_results = [r for r in results
                  if "developers.facebook.com" in (r.url or "").lower()]
    if len(fb_results) < 3:
        print(f"  FAIL: only {len(fb_results)} of {len(results)} "
              f"results on developers.facebook.com")
        for r in results:
            print(f"    {r.url}")
        return 1
    # spot-check section tagging
    if not any(r.__dict__.get("doc_section") for r in fb_results):
        print(f"  FAIL: no section tag on any result")
        return 1
    print(f"  PASS: live search returned {len(fb_results)} fb-docs hits "
          f"(sample: {fb_results[0].title[:50]})")
    return 0


def main() -> int:
    print("=== test_facebook_docs ===")
    failures = 0
    for label, fn in [
        ("import_smoke",      t_import_smoke),
        ("alias_registered",  t_alias_registered),
        ("query_builder",     t_query_builder),
        ("url_inferers",      t_url_inferers),
        ("invalid_mode",      t_invalid_mode),
        ("live_search",       t_live_search),
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
