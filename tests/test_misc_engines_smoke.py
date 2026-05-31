"""Smoke test for the remaining engines without dedicated test files.

After test_news_engines_smoke covered 10 news engines, 12 engines
were still uncovered (per the post-review audit). They span four
domains so a single shared query doesn't work — each gets its own
appropriate query.

  Travel:    booking / expedia
  Jobs:      glassdoor / ziprecruiter / linkedin
  Maps:      google_maps
  Reference: mdn / wikipedia / semanticscholar
  Torrents:  torrent_1337x
  Finance:   yahoo_finance
  Ad lib:    instagram_ad_library

Each engine gets:
  • query that should plausibly return ≥ 1 result
  • optional engine-specific kwargs (instagram_ad_library needs country)
  • host_substring that the top hit's URL is expected to contain

Set ``AGENTSEARCH_SKIP_LIVE=1`` to skip the network calls and only
check imports.

Run::

    ~/tools/cloakbrowser/venv/bin/python tests/test_misc_engines_smoke.py
"""
from __future__ import annotations

import os
import sys
import time
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# (engine_handle, query, kwargs, expected_host_substr)
_CASES: list[tuple[str, str, dict, str]] = [
    # Travel
    ("booking",            "Tokyo",                {}, "booking.com"),
    ("expedia",            "Paris hotels",         {}, "expedia."),
    # Jobs
    ("glassdoor",          "software engineer",    {}, "glassdoor."),
    ("ziprecruiter",       "software engineer",    {}, "ziprecruiter."),
    ("linkedin",           "openai",               {}, "linkedin.com"),
    # Maps
    ("google_maps",        "ramen new york",       {}, "google."),
    # Reference / docs
    ("mdn",                "fetch api",            {}, "mozilla.org"),
    ("wikipedia",          "transformer model",    {}, "wikipedia.org"),
    ("semanticscholar",    "transformer attention",{}, "semanticscholar.org"),
    # Torrents
    ("torrent_1337x",      "ubuntu",               {}, "1337x"),
    # Finance
    ("yahoo_finance",      "AAPL",                 {}, "finance.yahoo.com"),
    # Ad lib (needs country)
    ("instagram_ad_library", "Shopify",            {"country": "US"},
                                                          "facebook.com/ads/library"),
]


def t_imports() -> int:
    """Each engine module imports cleanly + handle is in cli registry."""
    from agent_search.cli import _engine_registry
    reg = _engine_registry()
    fail = 0
    for handle, _, _, _ in _CASES:
        if handle not in reg:
            print(f"  FAIL: engine {handle!r} not in cli registry")
            fail += 1
    if fail == 0:
        print(f"  PASS: {len(_CASES)} engines registered")
    return fail


def t_live_smoke() -> int:
    if os.environ.get("AGENTSEARCH_SKIP_LIVE", "0") == "1":
        print("  SKIP")
        return 0
    from agent_search.core import BrowserConfig, launch, new_page
    from agent_search.cli import _get_engine

    cfg = BrowserConfig(headless=True, humanize=False, proxy=None)
    b = launch(cfg)
    fail = 0
    try:
        for handle, query, kwargs, host_substr in _CASES:
            try:
                engine_cls = _get_engine(handle)
            except Exception as e:
                print(f"  ✗ {handle:<22} cli registry: {e}")
                fail += 1
                continue
            page = new_page(b)
            t0 = time.time()
            try:
                eng = engine_cls(page)
                rs = eng.search(query, limit=3, **kwargs) or []
            except Exception as e:
                print(f"  ✗ {handle:<22} search raised: "
                      f"{type(e).__name__}: {str(e)[:60]}")
                fail += 1
                continue
            finally:
                try:
                    page.close()
                except Exception:
                    pass
            elapsed = time.time() - t0
            if not rs:
                print(f"  ✗ {handle:<22} 0 results in {elapsed:.1f}s")
                fail += 1
                continue
            top_url = (rs[0].url or "").lower()
            top_blob = ((rs[0].title or "") + " " +
                        (rs[0].snippet or "")).lower()
            if host_substr not in top_url and host_substr not in top_blob:
                print(f"  ✗ {handle:<22} top hit not on {host_substr}: "
                      f"{top_url[:80]}")
                fail += 1
                continue
            title_brief = (rs[0].title or "")[:50]
            print(f"  ✓ {handle:<22} {len(rs)} hits in "
                  f"{elapsed:>4.1f}s  ({title_brief})")
    finally:
        b.close()
    if fail:
        print(f"  FAIL: {fail}/{len(_CASES)} engines")
    else:
        print(f"  PASS: {len(_CASES)}/{len(_CASES)} engines green")
    return fail


def main() -> int:
    print("=== test_misc_engines_smoke ===")
    failures = 0
    for label, fn in [("imports", t_imports), ("live", t_live_smoke)]:
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
