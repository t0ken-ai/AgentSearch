"""Test Google Search adapter."""
from ..engines.google import GoogleEngine
from .runner import run_engine_test

if __name__ == "__main__":
    import sys
    ok = run_engine_test(GoogleEngine, query="python programming", runs=3)
    sys.exit(0 if ok else 1)
