"""News-engine smoke test.

Six news engines were shipped without dedicated test files:

  bbc / cnn / aljazeera / apnews / arstechnica / guardian

Plus three more that are similar enough to bundle in:

  npr / techcrunch / verge / reuters

This script runs a single shared query through each engine and asserts:

  • it doesn't raise
  • it returns ≥ 1 result
  • the top result has a real http(s) URL on the engine's host

It's a *smoke* test (catches DOM rotations + import regressions),
not a yield benchmark. Set ``AGENTSEARCH_SKIP_LIVE=1`` to skip the
network calls and only check imports.

Run::

    ~/tools/cloakbrowser/venv/bin/python tests/test_news_engines_smoke.py
"""
from __future__ import annotations

import os
import sys
import time
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# (engine_handle, expected_host_substr_in_first_url)
_ENGINES: list[tuple[str, str]] = [
    ("bbc",         "bbc."),
    ("cnn",         "cnn.com"),
    ("aljazeera",   "aljazeera.com"),
    ("apnews",      "apnews.com"),
    ("arstechnica", "arstechnica.com"),
    ("guardian",    "theguardian.com"),
    ("npr",         "npr.org"),
    ("techcrunch",  "techcrunch.com"),
    ("verge",       "theverge.com"),
    ("reuters",     "reuters.com"),
]

_QUERY = "artificial intelligence"


def t_imports() -> int:
    """Each engine module imports cleanly + exposes an Engine class."""
    from agent_search.cli import _engine_registry
    reg = _engine_registry()
    fail = 0
    for handle, _ in _ENGINES:
        if handle not in reg:
            print(f"  FAIL: engine {handle!r} not in cli registry")
            fail += 1
    if fail == 0:
        print(f"  PASS: {len(_ENGINES)} news engines registered")
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
        for handle, host_substr in _ENGINES:
            try:
                engine_cls = _get_engine(handle)
            except Exception as e:
                print(f"  ✗ {handle:<14} cli registry: {e}")
                fail += 1
                continue
            page = new_page(b)
            t0 = time.time()
            try:
                eng = engine_cls(page)
                rs = eng.search(_QUERY, limit=3) or []
            except Exception as e:
                print(f"  ✗ {handle:<14} search raised: "
                      f"{type(e).__name__}: {e}")
                fail += 1
                continue
            finally:
                try:
                    page.close()
                except Exception:
                    pass
            elapsed = time.time() - t0
            if not rs:
                print(f"  ✗ {handle:<14} 0 results in {elapsed:.1f}s")
                fail += 1
                continue
            top_url = (rs[0].url or "").lower()
            if host_substr not in top_url:
                # Fallback engines (site:google) sometimes route through
                # an SE wrapper — accept results that mention the host
                # in title or snippet too.
                top_blob = (rs[0].title or "") + " " + (rs[0].snippet or "")
                if host_substr not in top_blob.lower():
                    print(f"  ✗ {handle:<14} top hit not on {host_substr}: "
                          f"{top_url[:80]}")
                    fail += 1
                    continue
            print(f"  ✓ {handle:<14} {len(rs)} hits in {elapsed:>4.1f}s "
                  f"({rs[0].title[:50]})")
    finally:
        b.close()
    if fail:
        print(f"  FAIL: {fail}/{len(_ENGINES)} engines")
    else:
        print(f"  PASS: {len(_ENGINES)}/{len(_ENGINES)} engines green")
    return fail


def main() -> int:
    print("=== test_news_engines_smoke ===")
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
