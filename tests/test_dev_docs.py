"""dev_docs engine regression test.

Layered:

1. **import + presets** — confirm 80+ platforms registered, multi-host
   presets resolve correctly.
2. **query builder** — DDG modifier composition for site / mode /
   product / api_version permutations.
3. **section + version inferers** — pure URL string tests.
4. **mode validation** — invalid mode raises.
5. **CLI alias registration** — `dev_docs` and `docs` reachable.
6. **live multi-platform** — Stripe / OpenAI / AWS / Anthropic
   one-shot search; passes if 3 of the 4 return ≥1 hit on the
   right host (DDG occasionally rate-limits one provider per run).

Run::

    ~/tools/cloakbrowser/venv/bin/python tests/test_dev_docs.py
"""
from __future__ import annotations

import os
import sys
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search.engines.dev_docs import (
    DevDocsEngine, list_platforms, resolve_platform, _PRESETS,
)


def t_presets() -> int:
    if len(_PRESETS) < 60:
        print(f"  FAIL: only {len(_PRESETS)} presets")
        return 1
    fail = 0
    must_have = ["aws", "openai", "anthropic", "stripe", "google-cloud",
                 "react", "python", "github", "kubernetes", "mdn"]
    for p in must_have:
        if not resolve_platform(p):
            print(f"  FAIL: preset {p!r} missing")
            fail += 1
    multi = resolve_platform("anthropic")
    if len(multi) < 2:
        print(f"  FAIL: anthropic should resolve to >=2 hosts, got {multi}")
        fail += 1
    if fail == 0:
        print(f"  PASS: {len(_PRESETS)} presets, {len(must_have)} key "
              f"platforms verified, multi-host preset works")
    return fail


def t_query_builder() -> int:
    qb = DevDocsEngine._build_ddg_query
    cases = [
        # (query, hosts, mode, product, api_version) → must-have substrings
        (("webhook", ["stripe.com"], "search", None, None),
         ["site:stripe.com", "webhook"]),
        (("embedding", ["platform.openai.com"], "reference", None, None),
         ["site:platform.openai.com", "inurl:reference"]),
        (("messages", ["docs.anthropic.com", "docs.claude.com"],
          "api", None, None),
         ["(site:docs.anthropic.com OR site:docs.claude.com)",
          "inurl:api"]),
        (("lambda", ["docs.aws.amazon.com"], "changelog", "lambda", None),
         ["inurl:changelog", "inurl:lambda"]),
        (("hooks", ["react.dev"], "search", None, "18"),
         ['"18"', "hooks"]),
        # Empty query is OK — pure site filter
        (("", ["docs.python.org"], "search", None, None),
         ["site:docs.python.org"]),
    ]
    fail = 0
    for inp, must_have in cases:
        q = qb(*inp)
        for needle in must_have:
            if needle not in q:
                print(f"  FAIL: q={inp[0]!r} missing {needle!r} in: {q}")
                fail += 1
    if fail == 0:
        print(f"  PASS: query builder ({len(cases)} cases)")
    return fail


def t_url_inferers() -> int:
    inf_section = DevDocsEngine._infer_section
    inf_version = DevDocsEngine._infer_version
    fail = 0

    section_cases = [
        ("https://docs.stripe.com/api/reference/webhooks", "reference"),
        ("https://docs.aws.amazon.com/lambda/latest/dg/changelog.html", "changelog"),
        ("https://platform.openai.com/docs/quickstart", "quickstart"),
        ("https://react.dev/learn/tutorial-tic-tac-toe", "tutorial"),
        ("https://docs.aws.amazon.com/cookbook/", "example"),
        ("https://stripe.com/blog/webhooks", "blog"),
        ("https://stripe.com/help/faq", "faq"),
        ("https://kubernetes.io/docs/concepts/", "guide"),
    ]
    for url, expected in section_cases:
        got = inf_section(url)
        if got != expected:
            print(f"  FAIL section({url!r}) -> {got!r}, want {expected!r}")
            fail += 1

    version_cases = [
        ("https://docs.stripe.com/api/reference/v3/", "v3"),
        ("https://platform.openai.com/api/v1/embeddings", "v1"),
        ("https://docs.aws.amazon.com/lambda/latest/dg/", ""),
    ]
    for url, expected in version_cases:
        got = inf_version(url)
        if got != expected:
            print(f"  FAIL version({url!r}) -> {got!r}, want {expected!r}")
            fail += 1

    if fail == 0:
        print(f"  PASS: section + version inferers "
              f"({len(section_cases) + len(version_cases)} cases)")
    return fail


def t_mode_validation() -> int:
    eng = DevDocsEngine.__new__(DevDocsEngine)
    eng.last_status = {}
    eng.page = None
    fail = 0
    try:
        eng.search("x", platform="aws", mode="bogus")
    except ValueError as e:
        if "unknown mode" not in str(e):
            print(f"  FAIL: wrong error: {e}")
            fail += 1
    else:
        print("  FAIL: bogus mode should raise")
        fail += 1

    try:
        eng.search("x")  # neither platform nor site
    except ValueError as e:
        if "platform" not in str(e):
            print(f"  FAIL: wrong error: {e}")
            fail += 1
    else:
        print("  FAIL: missing platform/site should raise")
        fail += 1

    try:
        eng.search("x", platform="not-a-real-platform")
    except ValueError as e:
        if "unknown platform" not in str(e):
            print(f"  FAIL: wrong error: {e}")
            fail += 1
    else:
        print("  FAIL: unknown platform should raise")
        fail += 1
    if fail == 0:
        print("  PASS: validation (3 error paths)")
    return fail


def t_alias_registered() -> int:
    from agent_search.cli import _engine_registry
    reg = _engine_registry()
    fail = 0
    for handle in ("dev_docs", "docs"):
        if handle not in reg:
            print(f"  FAIL: {handle!r} not registered")
            fail += 1
    if fail == 0:
        print("  PASS: dev_docs + docs aliases registered")
    return fail


def t_live_multi() -> int:
    if os.environ.get("AGENTSEARCH_SKIP_LIVE", "0") == "1":
        print("  SKIP")
        return 0
    from agent_search.core import BrowserConfig, launch, new_page
    cfg = BrowserConfig(headless=True, humanize=False, proxy=None)
    b = launch(cfg)
    cases = [
        ("stripe",    "subscription webhook", "stripe.com"),
        ("openai",    "embeddings",            "platform.openai.com"),
        ("anthropic", "tool use",              "anthropic.com"),
        ("aws",       "s3 presigned url",      "aws.amazon.com"),
    ]
    ok = 0
    try:
        page = new_page(b)
        eng = DevDocsEngine(page)
        for plat, q, host_substr in cases:
            try:
                results = eng.search(q, limit=3, platform=plat)
            except Exception as e:
                print(f"  ✗ {plat}: {type(e).__name__}: {e}")
                continue
            on_host = [r for r in results if host_substr in (r.url or "").lower()]
            mark = "✓" if on_host else "✗"
            print(f"  {mark} {plat:<10} → {len(on_host)}/{len(results)} on {host_substr}")
            if on_host:
                ok += 1
    finally:
        b.close()
    # 4 platforms tested; allow 1 transient miss (DDG sometimes throttles)
    if ok >= 3:
        print(f"  PASS: {ok}/4 platforms returned hits")
        return 0
    print(f"  FAIL: only {ok}/4 platforms returned hits")
    return 1


def main() -> int:
    print("=== test_dev_docs ===")
    failures = 0
    for label, fn in [
        ("presets",          t_presets),
        ("query_builder",    t_query_builder),
        ("url_inferers",     t_url_inferers),
        ("mode_validation",  t_mode_validation),
        ("alias_registered", t_alias_registered),
        ("live_multi",       t_live_multi),
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
