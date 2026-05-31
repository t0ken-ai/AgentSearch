"""Live coverage audit for every engine in the cli registry.

For each unique engine class:
  • build a generic query (handle-aware overrides for engines
    that need a non-default kwarg, e.g. dev_docs needs platform=)
  • call ``engine.search(query, limit=2)``
  • record (handle, class_name, query, hits, latency, sample_url, err)

Output is JSON Lines so the result can be re-processed by trend
diffs (e.g. weekly: which engines newly broke).

Run::

    ~/tools/cloakbrowser/venv/bin/python tools/audit_engines.py

Options::

    --skip <handle> [<handle> …]   skip individual engines
    --only <handle> [<handle> …]   only run these
    --timeout <seconds>            per-engine timeout (default 60)
    --limit <int>                  results limit per call (default 2)
    --output <path>                JSONL output (default /tmp/engines_audit.jsonl)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agent_search.cli import _engine_registry, _get_engine
from agent_search.core import BrowserConfig, launch, new_page

# Engines that need extra kwargs to return anything meaningful.
# Format:  handle → (query, kwargs_dict)
# Anything not listed gets the default ('artificial intelligence', {}).
_OVERRIDES: dict[str, tuple[str, dict]] = {
    # dev_docs needs a platform — pick a stable, fast preset
    "dev_docs": ("webhook", {"platform": "stripe"}),
    "docs":     ("webhook", {"platform": "stripe"}),
    "fb_docs":  ("ad creation", {}),
    "facebook_docs": ("ad creation", {}),
    "meta_docs":     ("ad creation", {}),
    "fb_dev":        ("ad creation", {}),

    # Ad libraries — pass benign queries that should hit something
    "meta_ad_library":         ("Shopify", {"country": "US"}),
    "fb_ads":                  ("Shopify", {"country": "US"}),
    "meta_ads":                ("Shopify", {"country": "US"}),
    "instagram_ad_library":    ("Shopify", {"country": "US"}),
    "ig_ads":                  ("Shopify", {"country": "US"}),
    "instagram_ads":           ("Shopify", {"country": "US"}),
    "google_ad_transparency":  ("shopify.com",
                                {"mode": "domain", "domain": "shopify.com",
                                 "region": "US"}),
    "g_ads":                   ("shopify.com",
                                {"mode": "domain", "domain": "shopify.com",
                                 "region": "US"}),
    "tiktok_creative_center":  ("", {"mode": "top_ads", "country": "US",
                                     "period": 7}),
    "tt_ads":                  ("", {"mode": "top_ads", "country": "US",
                                     "period": 7}),
    "ttcc":                    ("", {"mode": "top_ads", "country": "US",
                                     "period": 7}),
    "tiktok_ad_library":       ("", {"country": "GB"}),
    "tiktok_ads":              ("", {"country": "GB"}),

    # Engines that work better with a domain-flavoured query
    "virustotal":   ("https://example.com", {}),
    "google_patents": ("solar panel", {}),
    "1337x":        ("ubuntu", {}),
    "torrent_1337x":("ubuntu", {}),

    # Image engines do better with visual nouns
    "unsplash":     ("mountain", {}),
    "pixabay":      ("mountain", {}),
    "pexels":       ("mountain", {}),
    "pinterest":    ("interior design", {}),

    # Shopping
    "amazon":       ("usb cable", {}),
    "ebay":         ("vintage camera", {}),
    "icecat":       ("dell xps", {}),
    "steam":        ("portal", {}),

    # Local / jobs
    "yelp":         ("ramen new york", {}),
    "indeed":       ("software engineer", {}),
    "linkedin_jobs": ("software engineer", {}),

    # Misc
    "wolfram":      ("integral of x^2", {}),
    "weibo":        ("AI", {}),
    "douyin":       ("美食", {}),
    "xiaohongshu":  ("旅行", {}),
    "toutiao":      ("AI", {}),
    "bilibili":     ("Python", {}),
    "zhihu":        ("机器学习", {}),
    "baidu":        ("人工智能", {}),
    "sogou":        ("人工智能", {}),
    "so360":        ("人工智能", {}),
    "xiaoyuzhou":   ("Lex Fridman", {}),
}

_DEFAULT_QUERY = "artificial intelligence"


def _unique_engines(reg: dict) -> list[tuple[str, str, str]]:
    """De-duplicate the registry — return one entry per (module, class).

    Returns sorted list of (canonical_handle, module_name, class_name).
    """
    seen: dict[tuple[str, str], str] = {}
    for handle, (module_name, class_name) in reg.items():
        key = (module_name, class_name)
        # Prefer the handle that equals the module name (canonical),
        # else first-seen.
        if key not in seen or handle == module_name:
            seen[key] = handle
    triples = [(h, m, c) for (m, c), h in seen.items()]
    triples.sort()
    return triples


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip", nargs="*", default=[], help="handles to skip")
    ap.add_argument("--only", nargs="*", default=[], help="only run these")
    ap.add_argument("--timeout", type=int, default=60,
                    help="per-engine timeout (seconds, default 60)")
    ap.add_argument("--limit", type=int, default=2,
                    help="results limit per call (default 2)")
    ap.add_argument("--output", default="/tmp/engines_audit.jsonl")
    ap.add_argument("--verbose", "-v", action="store_true")
    args = ap.parse_args()

    if args.verbose:
        logging.basicConfig(level=logging.INFO)

    reg = _engine_registry()
    triples = _unique_engines(reg)
    if args.only:
        wanted = {h.lower() for h in args.only}
        triples = [t for t in triples if t[0].lower() in wanted]
    if args.skip:
        skip = {h.lower() for h in args.skip}
        triples = [t for t in triples if t[0].lower() not in skip]

    print(f"[audit] {len(triples)} unique engines")
    print(f"[audit] writing → {args.output}")

    cfg = BrowserConfig(headless=True, humanize=False, proxy=None)
    b = launch(cfg)
    failed: list[str] = []
    timed_out: list[str] = []

    def _timeout_handler(signum, frame):
        raise TimeoutError("audit timeout")

    with open(args.output, "w") as f:
        try:
            for i, (handle, modname, clsname) in enumerate(triples, 1):
                query, kwargs = _OVERRIDES.get(handle,
                                               (_DEFAULT_QUERY, {}))
                t0 = time.time()
                err = ""
                hits = 0
                first_url = ""
                try:
                    engine_cls = _get_engine(handle)
                    page = new_page(b)
                    try:
                        signal.signal(signal.SIGALRM, _timeout_handler)
                        signal.alarm(args.timeout)
                        try:
                            inst = engine_cls(page)
                            rs = inst.search(query, limit=args.limit,
                                             **kwargs) or []
                            hits = len(rs)
                            if rs:
                                first_url = (rs[0].url or "")[:120]
                        finally:
                            signal.alarm(0)
                    finally:
                        try:
                            page.close()
                        except Exception:
                            pass
                except TimeoutError:
                    err = "timeout"
                    timed_out.append(handle)
                except Exception as e:
                    err = f"{type(e).__name__}: {e}"[:200]
                elapsed = time.time() - t0

                rec = {
                    "handle":  handle,
                    "module":  modname,
                    "class":   clsname,
                    "query":   query,
                    "kwargs":  kwargs,
                    "hits":    hits,
                    "elapsed": round(elapsed, 1),
                    "first":   first_url,
                    "err":     err,
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()

                mark = "✓" if hits > 0 else ("💥" if err else "✗")
                line = (f"[{i:>3}/{len(triples)}] {mark} "
                        f"{handle:<26} hits={hits:<2} "
                        f"t={elapsed:>5.1f}s")
                if first_url:
                    line += f"  {first_url[:50]}"
                if err:
                    line += f"  ERR={err[:80]}"
                print(line)
                if hits == 0 and not err:
                    failed.append(handle)
        finally:
            b.close()

    failed_path = "/tmp/engines_audit_failed.txt"
    with open(failed_path, "w") as f:
        for h in failed:
            f.write(h + "\n")

    print(f"\n[audit] {len(failed)} zero-result, "
          f"{len(timed_out)} timed out → {failed_path}")
    if failed:
        print("[audit] zero-result engines:")
        for h in failed:
            print(f"  - {h}")
    if timed_out:
        print("[audit] timed-out engines:")
        for h in timed_out:
            print(f"  - {h}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
