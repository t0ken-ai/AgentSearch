"""Shared test utilities."""

import logging
import sys
import time

from ..core import launch, new_page, BrowserConfig
from ..stealth.enhance import check_blocked

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
log = logging.getLogger(__name__)


def run_engine_test(engine_cls, query="python programming", limit=5, runs=3):
    """Run a standard engine test: search N times, report pass/fail."""
    name = engine_cls.name if hasattr(engine_cls, 'name') else engine_cls.__name__
    print(f"\n{'='*50}")
    print(f"Testing: {name}")
    print(f"Query: {query} | Limit: {limit} | Runs: {runs}")
    print(f"{'='*50}\n")

    cfg = BrowserConfig(headless=True)
    browser = launch(cfg)
    page = new_page(browser)

    passed = 0
    for i in range(runs):
        engine = engine_cls(page)
        results = engine.search(query, limit=limit)
        blocked = check_blocked(page)

        ok = len(results) > 0 and not blocked
        status = "✅ PASS" if ok else "❌ FAIL"
        print(f"  Run {i+1}/{runs}: {status} ({len(results)} results, blocked={blocked})")

        if results:
            print(f"    First: {results[0].title[:60]}")
        if ok:
            passed += 1
        time.sleep(2)

    browser.close()

    rate = passed / runs * 100
    final = "✅ PASSED" if passed == runs else "⚠️ PARTIAL" if passed > 0 else "❌ FAILED"
    print(f"\nResult: {final} ({passed}/{runs} = {rate:.0f}%)")
    return passed == runs
