from __future__ import annotations

import unittest
from unittest.mock import patch

from agent_search.engines.hackernews import HackerNewsEngine
from agent_search.engines.pubmed import PubMedEngine
from agent_search.policies import (
    engine_uses_browser,
    identity_policy,
    max_parallelism,
    retain_browser_sessions,
    search_policy,
)


class PolicyTests(unittest.TestCase):
    def test_public_api_engines_do_not_require_chromium(self) -> None:
        self.assertFalse(engine_uses_browser(HackerNewsEngine))
        self.assertFalse(engine_uses_browser(PubMedEngine))
        self.assertEqual(search_policy("hackernews").cache_ttl_s, 300)

    def test_high_risk_sites_get_profiles_without_public_fixed_values(self) -> None:
        reddit = identity_policy("reddit")
        google = identity_policy("google")
        self.assertTrue(reddit.persistent)
        self.assertTrue(reddit.humanize)
        self.assertFalse(google.persistent)
        self.assertEqual(google.key, "google-search")

    def test_parallelism_has_a_global_ceiling(self) -> None:
        with patch.dict("os.environ", {"AGENTSEARCH_MAX_PARALLEL": "3"}):
            self.assertEqual(max_parallelism(99), 3)

    def test_idle_browser_retention_requires_explicit_opt_in(self) -> None:
        with patch.dict("os.environ", {"AGENTSEARCH_RETAIN_BROWSER": "0"}):
            self.assertFalse(retain_browser_sessions())
        with patch.dict("os.environ", {"AGENTSEARCH_RETAIN_BROWSER": "1"}):
            self.assertTrue(retain_browser_sessions())


if __name__ == "__main__":
    unittest.main()
