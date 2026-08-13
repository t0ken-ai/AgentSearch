from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_search.health import HealthLog


class HealthLogTests(unittest.TestCase):
    def test_metrics_include_percentiles_cache_and_block_reasons(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            health = HealthLog(Path(tmp) / "health.json")
            health.record(
                "engine",
                ok=True,
                count=2,
                ms=100,
                metrics={
                    "navigation_ms": 70,
                    "wait_ms": 10,
                    "condition_wait_ms": 20,
                    "cache_hit": False,
                    "deadline_reached": False,
                },
            )
            health.record(
                "engine",
                ok=False,
                ms=500,
                metrics={
                    "blocked_reason": "captcha",
                    "cache_hit": False,
                    "deadline_reached": True,
                },
            )
            health.record(
                "engine",
                ok=True,
                count=2,
                ms=200,
                metrics={"cache_hit": True},
            )

            stats = health.stats("engine")

        self.assertEqual(stats["p50_ms"], 200)
        self.assertEqual(stats["p95_ms"], 500)
        self.assertEqual(stats["avg_navigation_ms"], 23)
        self.assertEqual(stats["cache_hit_rate"], 0.333)
        self.assertEqual(stats["deadline_rate"], 0.333)
        self.assertEqual(stats["block_reasons"], {"captcha": 1})

    def test_refresh_observes_another_writer(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "health.json"
            reader = HealthLog(path)
            HealthLog(path).record("engine", ok=True, count=1, ms=10)

            self.assertEqual(reader.stats("engine")["attempts"], 0)
            reader.refresh()
            self.assertEqual(reader.stats("engine")["attempts"], 1)


if __name__ == "__main__":
    unittest.main()
