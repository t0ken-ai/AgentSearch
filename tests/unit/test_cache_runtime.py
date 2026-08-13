from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_search.cache import SearchCache, make_cache_key
from agent_search.results import SearchResult
from agent_search.runtime import execute_search, search_metrics


class CountingEngine:
    name = "hackernews"
    transport = "http"

    def __init__(self) -> None:
        self.calls = 0

    def search(self, query: str, limit: int = 10):
        self.calls += 1
        result = SearchResult("title", "https://example.test", query)
        result.adapter_field = {"limit": limit}
        return [result]


class CacheRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ,
            {
                "AGENTSEARCH_CACHE_PATH": f"{self.tmp.name}/cache.sqlite3",
                "AGENTSEARCH_HEALTH_PATH": f"{self.tmp.name}/health.json",
            },
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_cache_key_partitions_identity_and_options(self) -> None:
        left = make_cache_key("x", "q", 5, {"sort": "new"}, "identity-a")
        right = make_cache_key("x", "q", 5, {"sort": "new"}, "identity-b")
        changed = make_cache_key("x", "q", 5, {"sort": "top"}, "identity-a")
        self.assertNotEqual(left, right)
        self.assertNotEqual(left, changed)

    def test_runtime_cache_preserves_extension_fields(self) -> None:
        cache = SearchCache(Path(self.tmp.name) / "cache.sqlite3")
        engine = CountingEngine()

        first = execute_search(
            engine,
            "query",
            limit=3,
            cache_partition="public",
            cache=cache,
        )
        second = execute_search(
            engine,
            "query",
            limit=3,
            cache_partition="public",
            cache=cache,
        )

        self.assertEqual(engine.calls, 1)
        self.assertEqual(first[0].adapter_field, {"limit": 3})
        self.assertEqual(second[0].adapter_field, {"limit": 3})
        self.assertTrue(search_metrics(engine)["cache_hit"])

    def test_health_probe_can_bypass_a_warm_cache(self) -> None:
        cache = SearchCache(Path(self.tmp.name) / "cache.sqlite3")
        engine = CountingEngine()

        for _ in range(2):
            execute_search(
                engine,
                "canary",
                cache_partition="public",
                cache=cache,
                use_cache=False,
            )

        self.assertEqual(engine.calls, 2)
        self.assertFalse(search_metrics(engine)["cache_hit"])


if __name__ == "__main__":
    unittest.main()
