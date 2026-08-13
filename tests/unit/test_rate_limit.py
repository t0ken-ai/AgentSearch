from __future__ import annotations

import os
import tempfile
import time
import unittest
from unittest.mock import patch

from agent_search.execution import (
    SearchDeadlineExceeded,
    SearchTrace,
    execution_scope,
)
from agent_search.policies import SearchPolicy
from agent_search.rate_limit import wait_for_request_slot


class RequestRateLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ,
            {"AGENTSEARCH_RATE_LIMIT_DIR": self.tmp.name},
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_first_request_is_immediate_and_next_one_is_spaced(self) -> None:
        started = time.monotonic()
        wait_for_request_slot("example.test", 0.04)
        wait_for_request_slot("example.test", 0.04)

        self.assertGreaterEqual(time.monotonic() - started, 0.03)

    def test_queued_slot_does_not_cross_search_deadline(self) -> None:
        wait_for_request_slot("deadline.test", 0.2)
        trace = SearchTrace(
            engine="test",
            policy=SearchPolicy(deadline_s=0.01),
            transport="http",
        )

        with execution_scope(trace), self.assertRaises(SearchDeadlineExceeded):
            wait_for_request_slot("deadline.test", 0.2)

        self.assertTrue(trace.deadline_reached)


if __name__ == "__main__":
    unittest.main()
