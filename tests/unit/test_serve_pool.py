from __future__ import annotations

import unittest
from unittest.mock import patch

from agent_search import __version__
from agent_search.serve import BrowserPool, Handler


class FakeBrowser:
    def __init__(self) -> None:
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1


class BrowserPoolTests(unittest.TestCase):
    def test_http_server_header_uses_package_version(self) -> None:
        self.assertEqual(Handler.server_version, f"AgentSearch/{__version__}")

    def test_profile_browser_is_request_scoped_and_never_becomes_shared(self) -> None:
        launched: list[FakeBrowser] = []
        configs = []

        def fake_launch(config):
            browser = FakeBrowser()
            configs.append(config)
            launched.append(browser)
            return browser

        pool = BrowserPool()
        with patch("agent_search.serve.launch", side_effect=fake_launch):
            with pool.session() as anonymous_first:
                pass
            with pool.session("/profiles/alice") as profile_browser:
                self.assertIsNot(profile_browser, anonymous_first)
                self.assertEqual(profile_browser.close_calls, 0)

            self.assertEqual(profile_browser.close_calls, 1)
            self.assertEqual(anonymous_first.close_calls, 0)

            with pool.session() as anonymous_second:
                self.assertIs(anonymous_second, anonymous_first)

            pool.shutdown()

        self.assertEqual(len(launched), 2)
        self.assertIsNone(configs[0].user_data_dir)
        self.assertEqual(configs[1].user_data_dir, "/profiles/alice")
        self.assertEqual(anonymous_first.close_calls, 1)

    def test_shared_browser_recycles_at_configured_boundary(self) -> None:
        launched: list[FakeBrowser] = []

        def fake_launch(_config):
            browser = FakeBrowser()
            launched.append(browser)
            return browser

        pool = BrowserPool()
        with (
            patch("agent_search.serve.launch", side_effect=fake_launch),
            patch("agent_search.serve.RECYCLE_AFTER", 1),
        ):
            with pool.session() as first:
                pass
            with pool.session() as second:
                pass
            pool.shutdown()

        self.assertIsNot(first, second)
        self.assertEqual(first.close_calls, 1)
        self.assertEqual(second.close_calls, 1)


if __name__ == "__main__":
    unittest.main()
