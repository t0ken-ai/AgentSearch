from __future__ import annotations

import unittest
from unittest.mock import patch

from agent_search.engines.base import BaseEngine
from agent_search.execution import SearchTrace, execution_scope
from agent_search.policies import SearchPolicy


class FailedNavigationEngine(BaseEngine):
    name = "failed_navigation"

    def _do_search(self, query: str, limit: int):
        from agent_search.execution import current_trace

        current_trace().error = "navigation_failed: https://example.test"
        return []


class BaseEngineTests(unittest.TestCase):
    def test_navigation_failure_skips_block_probe_on_inflight_page(self) -> None:
        engine = FailedNavigationEngine.__new__(FailedNavigationEngine)
        engine.page = object()
        trace = SearchTrace(
            engine=engine.name,
            policy=SearchPolicy(max_attempts=1),
            transport="browser",
        )
        with execution_scope(trace), patch(
            "agent_search.engines.base.check_blocked"
        ) as check:
            self.assertEqual(engine.search("query", limit=1), [])

        check.assert_not_called()


if __name__ == "__main__":
    unittest.main()
