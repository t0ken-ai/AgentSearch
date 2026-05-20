"""Stress test for YouTube adapter with diverse queries.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_youtube_stress.py
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
from cloak_stealth_suite.engines.youtube import YouTubeEngine


@dataclass
class CaseResult:
    label: str
    query: str
    limit: int
    count: int
    expected_min: int
    note: str = ""
    sample: dict = field(default_factory=dict)

    @property
    def status(self) -> str:
        if self.count >= self.expected_min:
            return "PASS"
        return "FAIL"


CASES: list[tuple[str, str, int, int]] = [
    ("english_tutorial",   "Python tutorial",         10, 5),
    ("music",              "lo-fi hip hop",            5, 3),
    ("brand_specific",     "Apple WWDC 2024 keynote",  5, 2),
    ("chinese",            "人工智能教程",                  5, 2),
    ("question_format",    "how does a transformer work", 5, 3),
    ("short_query",        "react",                     5, 3),
    ("limit_1",            "rick astley",               1, 1),
    ("limit_15",           "best documentaries 2024",   15, 5),
    ("emoji_in_query",     "🐍 python tips",             5, 1),
    ("technical",          "kubernetes ingress controller", 5, 3),
]


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== YouTube adapter stress test ===")
    print(f"Cases: {len(CASES)}\n")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    case_results: list[CaseResult] = []
    try:
        page = core.new_page(browser)
        engine = YouTubeEngine(page)

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

            sample = {}
            if results:
                top = results[0]
                sample = {
                    "title": (top.title or "")[:60],
                    "video_id": getattr(top, "video_id", "") or "",
                    "channel": getattr(top, "channel", "") or "",
                    "views": getattr(top, "views", "") or "",
                    "duration": getattr(top, "duration", "") or "",
                }

            cr = CaseResult(
                label=label, query=query, limit=limit,
                count=len(results), expected_min=expected_min,
                sample=sample,
            )
            case_results.append(cr)

            print(f"    {cr.status}: got {len(results)}/{limit} in {elapsed:.1f}s")
            if sample:
                print(f"    [1] {sample['title']}")
                meta = []
                if sample.get("video_id"): meta.append(f"id={sample['video_id']}")
                if sample.get("channel"): meta.append(f"by {sample['channel']}")
                if sample.get("views"): meta.append(f"{sample['views']} views")
                if sample.get("duration"): meta.append(sample["duration"])
                if meta:
                    print(f"        {' · '.join(str(m) for m in meta)}")

            time.sleep(2.0)
    finally:
        try:
            browser.close()
        except Exception as e:
            print(f"warning: browser.close() raised: {e}", file=sys.stderr)

    # Summary
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

    # Fields-coverage analysis
    fields_observed = {"video_id": 0, "channel": 0, "views": 0, "duration": 0}
    total_with_results = 0
    for c in case_results:
        if c.sample:
            total_with_results += 1
            for k in fields_observed:
                if c.sample.get(k):
                    fields_observed[k] += 1
    if total_with_results:
        print(f"\nField coverage on top result (n={total_with_results}):")
        for k, v in fields_observed.items():
            print(f"  {k:<12}: {v}/{total_with_results}  ({100*v/total_with_results:.0f}%)")

    print()
    print(f"{'CASE':<22} {'Q':<28} {'GOT':>5} {'WANT':>5} {'STATUS':>7}  NOTE")
    for c in case_results:
        q = c.query if len(c.query) <= 26 else c.query[:25] + "…"
        print(f"{c.label:<22} {q:<28} {c.count:>5} {c.limit:>5} "
              f"{c.status:>7}  {c.note}")

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
