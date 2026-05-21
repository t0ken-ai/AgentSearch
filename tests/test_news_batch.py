"""Batch test for all 10 R6 news adapters.

Each adapter is run against a topical query and the result count, mode
and a sample row are reported. This is the smoke test that's run before
committing the R6 batch.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_news_batch.py [adapter_name]   # optional filter
"""

from __future__ import annotations

import logging
import os
import sys
import time
import traceback
from dataclasses import dataclass

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search import core
from agent_search.engines.bbc import BBCEngine
from agent_search.engines.guardian import GuardianEngine
from agent_search.engines.reuters import ReutersEngine
from agent_search.engines.apnews import APNewsEngine
from agent_search.engines.cnn import CNNEngine
from agent_search.engines.npr import NPREngine
from agent_search.engines.aljazeera import AlJazeeraEngine
from agent_search.engines.techcrunch import TechCrunchEngine
from agent_search.engines.verge import VergeEngine
from agent_search.engines.arstechnica import ArsTechnicaEngine


@dataclass
class CaseResult:
    name: str
    count: int
    mode: str
    note: str = ""
    sample_title: str = ""
    sample_url: str = ""

    @property
    def status(self) -> str:
        return "PASS" if self.count > 0 else "FAIL"


CASES = [
    ("bbc",         BBCEngine,         "artificial intelligence"),
    ("guardian",    GuardianEngine,    "climate change"),
    ("reuters",     ReutersEngine,     "stock market"),
    ("apnews",      APNewsEngine,      "election"),
    ("cnn",         CNNEngine,         "technology"),
    ("npr",         NPREngine,         "podcast"),
    ("aljazeera",   AlJazeeraEngine,   "middle east"),
    ("techcrunch",  TechCrunchEngine,  "openai"),
    ("verge",       VergeEngine,       "iphone"),
    ("arstechnica", ArsTechnicaEngine, "linux kernel"),
]


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    only = sys.argv[1] if len(sys.argv) > 1 else None

    print("=== R6 news-batch test ===")
    cases = [c for c in CASES if (only is None or c[0] == only)]
    print(f"Cases: {len(cases)}\n")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    results: list[CaseResult] = []
    for i, (name, cls, query) in enumerate(cases, start=1):
        print(f"--- [{i:2d}/{len(cases)}] {name}: {query!r} ---")
        # Fresh browser per case — running 10 heavy SPAs in one process
        # eats memory and tends to crash on the later ones.
        browser = core.launch(cfg)
        try:
            page = core.new_page(browser)
            t0 = time.time()
            try:
                engine = cls(page)
                rows = engine.search(query, limit=5)
            except Exception as e:
                print(f"    EXCEPTION: {e}")
                traceback.print_exc()
                results.append(CaseResult(name=name, count=0, mode="error",
                                          note=f"EXC:{e}"))
                continue
            elapsed = time.time() - t0
            mode = (engine.last_status or {}).get("mode", "unknown")
            sample_title = ""
            sample_url = ""
            if rows:
                sample_title = (rows[0].title or "")[:80]
                sample_url = rows[0].url
            cr = CaseResult(
                name=name, count=len(rows), mode=mode,
                sample_title=sample_title, sample_url=sample_url,
            )
            results.append(cr)
            print(f"    {cr.status}: got {len(rows)} via {mode} in {elapsed:.1f}s")
            if rows:
                top = rows[0]
                print(f"    [1] {sample_title}")
                print(f"        {sample_url[:90]}")
                if (top.snippet or "").strip():
                    print(f"        {(top.snippet or '')[:90]}…")
        finally:
            try:
                browser.close()
            except Exception:
                pass
        time.sleep(2.0)

    # Summary
    print("\n" + "=" * 64)
    print("SUMMARY")
    print("=" * 64)
    pass_n = sum(1 for r in results if r.status == "PASS")
    fail_n = sum(1 for r in results if r.status == "FAIL")
    print(f"Total: {len(results)}   PASS: {pass_n}   FAIL: {fail_n}")
    print()
    print(f"{'NAME':<14} {'STATUS':<6} {'MODE':<14} {'COUNT':>5}  SAMPLE")
    for r in results:
        print(f"{r.name:<14} {r.status:<6} {r.mode:<14} {r.count:>5}  "
              f"{r.sample_title[:50]}")

    return 0 if fail_n == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
