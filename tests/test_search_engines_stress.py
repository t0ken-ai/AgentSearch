"""Stress test for the major search engine adapters.

Drives the same set of queries through Bing, DuckDuckGo, and Brave so we
can see how each behaves under typical load and compare yield numbers.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_search_engines_stress.py [engine_name]   # optional
"""

from __future__ import annotations

import logging
import os
import sys
import time
import traceback
from dataclasses import dataclass, field

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from cloak_stealth_suite import core
from cloak_stealth_suite.engines.bing import BingEngine
from cloak_stealth_suite.engines.duckduckgo import DuckDuckGoEngine
from cloak_stealth_suite.engines.brave import BraveEngine


@dataclass
class CaseResult:
    engine: str
    label: str
    query: str
    limit: int
    count: int
    expected_min: int
    note: str = ""

    @property
    def status(self) -> str:
        if self.count >= self.expected_min:
            return "PASS"
        return "FAIL"


CASES: list[tuple[str, str, int, int]] = [
    ("english_phrase",   "open source software",            5, 3),
    ("technical",        "rust async tokio",                5, 3),
    ("chinese",          "人工智能",                          5, 2),
    ("special_chars",    "C++ vs Rust",                     5, 2),
    ("limit_10",         "best programming languages 2024", 10, 5),
]

ENGINES = [
    ("bing",        BingEngine),
    ("duckduckgo",  DuckDuckGoEngine),
    ("brave",       BraveEngine),
]


def run_engine(engine_name: str, engine_cls) -> list[CaseResult]:
    print(f"\n{'='*60}")
    print(f"  Engine: {engine_name}")
    print(f"{'='*60}")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    case_results: list[CaseResult] = []
    try:
        page = core.new_page(browser)
        engine = engine_cls(page)

        for i, (label, query, limit, expected_min) in enumerate(CASES, start=1):
            print(f"--- [{i}/{len(CASES)}] {label}: {query!r} (limit={limit}) ---")
            t0 = time.time()
            try:
                results = engine.search(query, limit=limit)
            except Exception as e:
                print(f"    EXCEPTION: {e}")
                case_results.append(CaseResult(
                    engine=engine_name, label=label, query=query, limit=limit,
                    count=0, expected_min=expected_min, note=f"EXC:{e}",
                ))
                continue
            elapsed = time.time() - t0
            cr = CaseResult(
                engine=engine_name, label=label, query=query, limit=limit,
                count=len(results), expected_min=expected_min,
            )
            case_results.append(cr)
            print(f"    {cr.status}: got {len(results)}/{limit} in {elapsed:.1f}s")
            if results:
                top = results[0]
                print(f"    [1] {(top.title or '')[:60]}")
                print(f"        {top.url[:80]}")
            time.sleep(2.0)
    finally:
        try:
            browser.close()
        except Exception:
            pass
    return case_results


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    only = sys.argv[1] if len(sys.argv) > 1 else None
    engines_to_run = [(n, c) for (n, c) in ENGINES if (only is None or n == only)]
    if not engines_to_run:
        print(f"Unknown engine: {only}. Choose from: {[n for n,_ in ENGINES]}")
        return 2

    all_results: list[CaseResult] = []
    for name, cls in engines_to_run:
        try:
            all_results.extend(run_engine(name, cls))
        except Exception:
            traceback.print_exc()

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    by_engine: dict[str, list[CaseResult]] = {}
    for r in all_results:
        by_engine.setdefault(r.engine, []).append(r)

    print(f"{'ENGINE':<12} {'PASS':>5} {'FAIL':>5} {'YIELD':>10}")
    overall_pass = overall_fail = 0
    for eng, results in by_engine.items():
        p = sum(1 for r in results if r.status == "PASS")
        f = sum(1 for r in results if r.status == "FAIL")
        got = sum(r.count for r in results)
        want = sum(r.limit for r in results)
        overall_pass += p
        overall_fail += f
        pct = 100 * got / want if want else 0
        print(f"{eng:<12} {p:>5} {f:>5} {got:>4}/{want:<4} ({pct:.0f}%)")

    print()
    print(f"{'ENGINE':<12} {'CASE':<22} {'GOT':>5} {'WANT':>5} {'STATUS':>7}  NOTE")
    for r in all_results:
        print(f"{r.engine:<12} {r.label:<22} {r.count:>5} {r.limit:>5} "
              f"{r.status:>7}  {r.note}")

    return 0 if overall_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
