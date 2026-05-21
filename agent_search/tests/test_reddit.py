"""Test Reddit adapter."""
from ..engines.reddit import RedditEngine
from .runner import run_engine_test

if __name__ == "__main__":
    import sys
    ok = run_engine_test(RedditEngine, query="python programming", runs=3)
    sys.exit(0 if ok else 1)
