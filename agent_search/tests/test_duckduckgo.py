"""Test DuckDuckGo Search adapter."""
from ..engines.duckduckgo import DuckDuckGoEngine
from .runner import run_engine_test

if __name__ == "__main__":
    import sys
    ok = run_engine_test(DuckDuckGoEngine, query="python programming", runs=3)
    sys.exit(0 if ok else 1)
