from __future__ import annotations

import unittest
from unittest.mock import patch

try:
    from agent_search import mcp_server
except ImportError:  # MCP is an optional project dependency.
    mcp_server = None


@unittest.skipIf(mcp_server is None, "mcp extra is not installed")
class McpBrowserGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_regular_operation_releases_idle_session_by_default(self) -> None:
        events: list[str] = []

        async def run_on_browser_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        def operation() -> str:
            events.append("operation")
            return "done"

        def shutdown() -> None:
            events.append("shutdown")

        with patch.object(
            mcp_server._pool,
            "shutdown",
            side_effect=shutdown,
        ), patch.object(
            mcp_server,
            "_run_on_browser_thread",
            side_effect=run_on_browser_thread,
        ), patch.object(mcp_server, "RETAIN_BROWSER", False):
            result = await mcp_server._to_browser_thread(operation)

        self.assertEqual(result, "done")
        self.assertEqual(events, ["operation", "shutdown"])

    async def test_external_fanout_releases_shared_pool_first(self) -> None:
        events: list[str] = []

        async def run_on_browser_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        def shutdown() -> None:
            events.append("shutdown")

        def fanout() -> str:
            events.append("fanout")
            return "done"

        with patch.object(
            mcp_server._pool,
            "shutdown",
            side_effect=shutdown,
        ), patch.object(
            mcp_server,
            "_run_on_browser_thread",
            side_effect=run_on_browser_thread,
        ):
            result = await mcp_server._run_external_browser_work(fanout)

        self.assertEqual(result, "done")
        self.assertEqual(events, ["shutdown", "fanout"])


if __name__ == "__main__":
    unittest.main()
