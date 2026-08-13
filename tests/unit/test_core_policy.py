from __future__ import annotations

import os
import tempfile
import unittest
from unittest.mock import patch

from agent_search.core import BrowserConfig, launch, wait_for_any
from agent_search.identity import BrowserSessionBusyError, ProfileBusyError


class FakeBrowser:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class CorePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ,
            {
                "AGENTSEARCH_IDENTITY_DIR": f"{self.tmp.name}/identity",
                "AGENTSEARCH_PROFILES_DIR": f"{self.tmp.name}/profiles",
                "AGENTSEARCH_BROWSER_SLOT_DIR": f"{self.tmp.name}/browser-slots",
                "AGENTSEARCH_BROWSER_CONCURRENCY": "2",
                "AGENTSEARCH_CLOAK_CACHE_DIR": f"{self.tmp.name}/cloakbrowser",
            },
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_public_engine_gets_stable_seed_without_public_geo_defaults(self) -> None:
        calls = []

        def fake_launch(**kwargs):
            calls.append(kwargs)
            return FakeBrowser()

        with patch("agent_search.core.cloakbrowser.launch", side_effect=fake_launch):
            first = launch(BrowserConfig(engine_name="google"))
            second = launch(BrowserConfig(engine_name="google_images"))

        self.assertEqual(calls[0]["args"], calls[1]["args"])
        self.assertIsNone(calls[0]["timezone"])
        self.assertIsNone(calls[0]["locale"])
        self.assertFalse(calls[0]["geoip"])
        self.assertTrue(calls[0]["args"][0].startswith("--fingerprint="))
        first.close()
        second.close()

    def test_legacy_launch_hides_account_selectors_and_restores_them(self) -> None:
        observed = {}

        def fake_launch(**kwargs):
            observed.update(
                {
                    "kwargs": kwargs,
                    "cache": os.environ.get("CLOAKBROWSER_CACHE_DIR"),
                    "key": os.environ.get("CLOAKBROWSER_LICENSE_KEY"),
                    "binary": os.environ.get("CLOAKBROWSER_BINARY_PATH"),
                    "version": os.environ.get("CLOAKBROWSER_VERSION"),
                }
            )
            return FakeBrowser()

        with patch.dict(
            os.environ,
            {
                "AGENTSEARCH_CLOAK_MODE": "legacy",
                "AGENTSEARCH_CLOAK_BROWSER_VERSION": "145.0.7632.109.2",
                "CLOAKBROWSER_LICENSE_KEY": "cb_saved_account_key",
                "CLOAKBROWSER_BINARY_PATH": "/tmp/current-account-build",
                "CLOAKBROWSER_VERSION": "150.0.7871.114.3",
            },
        ), patch(
            "agent_search.core.cloakbrowser.launch",
            side_effect=fake_launch,
        ):
            browser = launch(BrowserConfig(engine_name="google"))
            browser.close()
            self.assertEqual(
                os.environ["CLOAKBROWSER_LICENSE_KEY"],
                "cb_saved_account_key",
            )
            self.assertEqual(
                os.environ["CLOAKBROWSER_BINARY_PATH"],
                "/tmp/current-account-build",
            )

        self.assertEqual(
            observed["cache"],
            f"{self.tmp.name}/cloakbrowser",
        )
        self.assertIsNone(observed["key"])
        self.assertIsNone(observed["binary"])
        self.assertIsNone(observed["version"])
        self.assertEqual(
            observed["kwargs"]["browser_version"],
            "145.0.7632.109.2",
        )

    def test_account_launch_preserves_saved_key_path(self) -> None:
        observed = {}

        def fake_launch(**kwargs):
            observed["key"] = os.environ.get("CLOAKBROWSER_LICENSE_KEY")
            observed["kwargs"] = kwargs
            return FakeBrowser()

        with patch.dict(
            os.environ,
            {
                "AGENTSEARCH_CLOAK_MODE": "account",
                "CLOAKBROWSER_LICENSE_KEY": "cb_explicit_account_key",
            },
        ), patch(
            "agent_search.core.cloakbrowser.launch",
            side_effect=fake_launch,
        ):
            browser = launch(BrowserConfig(engine_name="google"))
            browser.close()

        self.assertEqual(observed["key"], "cb_explicit_account_key")
        self.assertNotIn("browser_version", observed["kwargs"])

    def test_direct_geo_defaults_can_be_explicitly_pinned(self) -> None:
        with patch.dict(
            os.environ,
            {
                "AGENTSEARCH_DEFAULT_TIMEZONE": "Asia/Singapore",
                "AGENTSEARCH_DEFAULT_LOCALE": "en-SG",
            },
        ), patch(
            "agent_search.core.cloakbrowser.launch",
            return_value=FakeBrowser(),
        ) as mocked:
            browser = launch(BrowserConfig(engine_name="google"))
            browser.close()

        self.assertEqual(mocked.call_args.kwargs["timezone"], "Asia/Singapore")
        self.assertEqual(mocked.call_args.kwargs["locale"], "en-SG")

    def test_proxy_enables_geoip_instead_of_forcing_local_timezone(self) -> None:
        with patch(
            "agent_search.core.cloakbrowser.launch",
            return_value=FakeBrowser(),
        ) as mocked:
            browser = launch(BrowserConfig(
                engine_name="bing",
                proxy="http://proxy.example:8080",
            ))
            browser.close()

        kwargs = mocked.call_args.kwargs
        self.assertTrue(kwargs["geoip"])
        self.assertIsNone(kwargs["timezone"])
        self.assertIsNone(kwargs["locale"])

    def test_persistent_profile_lock_lives_until_context_close(self) -> None:
        contexts: list[FakeBrowser] = []

        def fake_persistent(**_kwargs):
            context = FakeBrowser()
            contexts.append(context)
            return context

        with patch(
            "agent_search.core.cloakbrowser.launch_persistent_context",
            side_effect=fake_persistent,
        ):
            first = launch(BrowserConfig(
                engine_name="reddit",
                profile_lock_timeout_s=0,
            ))
            with self.assertRaises(ProfileBusyError):
                launch(BrowserConfig(
                    engine_name="reddit",
                    profile_lock_timeout_s=0,
                ))
            first.close()
            second = launch(BrowserConfig(
                engine_name="reddit",
                profile_lock_timeout_s=0,
            ))
            second.close()

        self.assertEqual(len(contexts), 2)
        self.assertTrue(all(context.closed for context in contexts))

    def test_browser_slot_lives_until_browser_close(self) -> None:
        with patch.dict(
            os.environ,
            {"AGENTSEARCH_BROWSER_CONCURRENCY": "1"},
        ), patch(
            "agent_search.core.cloakbrowser.launch",
            side_effect=lambda **_kwargs: FakeBrowser(),
        ):
            first = launch(BrowserConfig(
                engine_name="google",
                browser_lock_timeout_s=0,
            ))
            with self.assertRaises(BrowserSessionBusyError):
                launch(BrowserConfig(
                    engine_name="bing",
                    browser_lock_timeout_s=0,
                ))
            first.close()
            second = launch(BrowserConfig(
                engine_name="bing",
                browser_lock_timeout_s=0,
            ))
            second.close()

    def test_wait_for_any_uses_one_total_selector_budget(self) -> None:
        class Page:
            def __init__(self) -> None:
                self.calls = []

            def wait_for_selector(self, selector, **kwargs):
                self.calls.append((selector, kwargs))
                return object()

        page = Page()
        self.assertTrue(wait_for_any(page, [".first", ".second"], timeout=700))
        self.assertEqual(len(page.calls), 1)
        self.assertEqual(page.calls[0][0], ".first, .second")
        self.assertEqual(page.calls[0][1]["timeout"], 700)


if __name__ == "__main__":
    unittest.main()
