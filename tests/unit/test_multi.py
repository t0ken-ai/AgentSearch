from __future__ import annotations

import time
import unittest
from unittest.mock import patch

from agent_search.multi import (
    _run_process_fanout,
    _start_method_for_main,
    merge_results,
    search_images_many,
    search_many,
)
from tests.unit.workers import fake_engine_runner


class MergeResultsTests(unittest.TestCase):
    def test_deduplicates_normalized_urls_and_preserves_consensus(self) -> None:
        per_engine = {
            "first": {
                "results": [{
                    "title": "First title",
                    "url": "HTTPS://Example.test/story/#section",
                    "snippet": "short",
                    "score": 2,
                }],
            },
            "second": {
                "results": [{
                    "title": "Second title",
                    "url": "https://example.test/story",
                    "snippet": "a substantially longer snippet",
                    "score": 9,
                }],
            },
        }

        merged = merge_results(per_engine)

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["engines"], ["first", "second"])
        self.assertEqual(merged[0]["snippet"], "a substantially longer snippet")
        self.assertEqual(merged[0]["score"], 9)


class ProcessFanoutTests(unittest.TestCase):
    def test_interactive_posix_call_uses_available_start_method(self) -> None:
        with patch("agent_search.multi.os.name", "posix"):
            self.assertEqual(_start_method_for_main(None), "fork")
            self.assertEqual(_start_method_for_main("<stdin>"), "fork")
            self.assertEqual(
                _start_method_for_main("/real/kernel.py", interactive=True),
                "fork",
            )
        with patch("agent_search.multi.os.name", "nt"):
            self.assertEqual(_start_method_for_main(None), "spawn")

    def test_returns_results_in_request_order(self) -> None:
        per_engine, deadline_reached = _run_process_fanout(
            ["fast-b", "fast-a"],
            "query",
            3,
            True,
            10.0,
            2,
            runner=fake_engine_runner,
        )

        self.assertFalse(deadline_reached)
        self.assertEqual(list(per_engine), ["fast-b", "fast-a"])
        self.assertTrue(all(row["ok"] for row in per_engine.values()))

    def test_deadline_terminates_a_running_worker(self) -> None:
        started = time.monotonic()
        per_engine, deadline_reached = _run_process_fanout(
            ["fast", "sleep:10", "sleep:11", "sleep:12"],
            "query",
            1,
            True,
            2.0,
            4,
            runner=fake_engine_runner,
        )
        elapsed = time.monotonic() - started

        self.assertTrue(deadline_reached)
        self.assertTrue(per_engine["fast"]["ok"])
        for name in ("sleep:10", "sleep:11", "sleep:12"):
            self.assertTrue(per_engine[name]["timed_out"])
        # A thread-pool implementation blocks for the full 10 seconds while
        # exiting its context. Leave startup/CI headroom while proving that
        # the advertised deadline is genuinely enforceable.
        self.assertLess(elapsed, 6.0)

    def test_worker_exception_is_returned_without_crashing_supervisor(self) -> None:
        per_engine, deadline_reached = _run_process_fanout(
            ["raise"],
            "query",
            1,
            True,
            10.0,
            1,
            runner=fake_engine_runner,
        )

        self.assertFalse(deadline_reached)
        self.assertFalse(per_engine["raise"]["ok"])
        self.assertIn("intentional worker failure", per_engine["raise"]["error"])


class PublicFanoutShapeTests(unittest.TestCase):
    def test_empty_search_shapes_include_deadline_metadata(self) -> None:
        for payload in (
            search_many("query", []),
            search_images_many("query", []),
        ):
            self.assertEqual(payload["timed_out"], 0)
            self.assertFalse(payload["deadline_reached"])
            self.assertEqual(payload["per_engine"], {})


if __name__ == "__main__":
    unittest.main()
