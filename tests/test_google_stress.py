"""Comprehensive stress test for Google adapter.

Runs multiple queries sequentially in a single browser session to surface:
- DOM parsing edge cases for different query types
- Result count consistency (how often do we get fewer than requested)
- CAPTCHA / sorry-page handling
- Consent dialog flow
- Rate-limiting behaviour after several back-to-back queries

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_google_stress.py
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

from agent_search import core
from agent_search.engines.google import GoogleEngine


@dataclass
class CaseResult:
    label: str
    query: str
    limit: int
    count: int
    expected_min: int
    selector_counts: dict = field(default_factory=dict)
    note: str = ""

    @property
    def status(self) -> str:
        if self.count >= self.expected_min:
            return "PASS"
        return "FAIL"


CASES: list[tuple[str, str, int, int]] = [
    # (label, query, limit, expected_min)
    ("english_phrase",   "open source software", 5, 3),
    ("english_short",    "AI",                    5, 3),
    ("technical",        "rust async tokio",      5, 3),
    ("question",         "how to learn python",   5, 3),
    ("phrase_quoted",    '"large language model"', 5, 3),
    ("chinese",          "人工智能",                5, 3),
    ("special_chars",    "C++ vs Rust",           5, 3),
    ("limit_1",          "github",                1, 1),
    ("limit_10",         "best programming languages 2024", 10, 5),
    ("very_specific",    "site:arxiv.org transformer attention", 5, 1),
]


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== Google adapter stress test ===")
    print(f"Cases: {len(CASES)}\n")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    case_results: list[CaseResult] = []
    try:
        page = core.new_page(browser)
        engine = GoogleEngine(page)

        for i, (label, query, limit, expected_min) in enumerate(CASES, start=1):
            print(f"--- [{i:2d}/{len(CASES)}] {label}: {query!r} (limit={limit}) ---")
            t0 = time.time()
            try:
                results = engine.search(query, limit=limit)
            except Exception as e:
                print(f"    EXCEPTION: {e}")
                traceback.print_exc()
                case_results.append(CaseResult(
                    label=label, query=query, limit=limit,
                    count=0, expected_min=expected_min, note=f"EXC:{e}",
                ))
                continue
            elapsed = time.time() - t0
            sel_counts = engine.selector_counts()
            note = ""
            if engine.last_status.get("block_reason"):
                note = f"BLOCKED:{engine.last_status['block_reason']}"

            cr = CaseResult(
                label=label, query=query, limit=limit,
                count=len(results), expected_min=expected_min,
                selector_counts=sel_counts, note=note,
            )
            case_results.append(cr)

            print(f"    {cr.status}: got {len(results)}/{limit} in {elapsed:.1f}s")
            print(f"    selectors: {sel_counts}")
            if results:
                top = results[0]
                title = (top.title or "")[:60]
                print(f"    [1] {title}")
                print(f"        {top.url[:80]}")
            if note:
                print(f"    note: {note}")

            # Pause between cases to avoid back-to-back rate-limiting.
            time.sleep(2.0)
    finally:
        try:
            browser.close()
        except Exception as e:
            print(f"warning: browser.close() raised: {e}", file=sys.stderr)

    # ---- Summary ----
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    pass_count = sum(1 for c in case_results if c.status == "PASS")
    fail_count = sum(1 for c in case_results if c.status == "FAIL")
    total_results = sum(c.count for c in case_results)
    total_requested = sum(c.limit for c in case_results)

    print(f"Total cases   : {len(case_results)}")
    print(f"  PASS        : {pass_count}")
    print(f"  FAIL        : {fail_count}")
    print(f"Result yield  : {total_results}/{total_requested} "
          f"({100 * total_results / total_requested:.0f}%)")
    print()

    print(f"{'CASE':<22} {'Q':<28} {'GOT':>5} {'WANT':>5} {'STATUS':>7}  NOTE")
    for c in case_results:
        q = c.query if len(c.query) <= 26 else c.query[:25] + "…"
        print(f"{c.label:<22} {q:<28} {c.count:>5} {c.limit:>5} "
              f"{c.status:>7}  {c.note}")

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
