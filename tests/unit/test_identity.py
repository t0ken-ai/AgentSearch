from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_search.identity import (
    BrowserSessionBusyError,
    BrowserSessionLease,
    ProfileBusyError,
    ProfileLease,
    ProfileProxyMismatchError,
    bind_profile_to_proxy,
    derive_fingerprint_seed,
    http_cache_partition,
    resolve_identity,
)
from agent_search.policies import identity_policy


class IdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(
            os.environ,
            {
                "AGENTSEARCH_IDENTITY_DIR": f"{self.tmp.name}/identity",
                "AGENTSEARCH_PROFILES_DIR": f"{self.tmp.name}/profiles",
                "AGENTSEARCH_BROWSER_SLOT_DIR": f"{self.tmp.name}/browser-slots",
                "AGENTSEARCH_AUTO_PROFILES": "1",
            },
        )
        self.env.start()

    def tearDown(self) -> None:
        self.env.stop()
        self.tmp.cleanup()

    def test_site_family_is_stable_but_install_secret_is_private(self) -> None:
        first = derive_fingerprint_seed("google-search", secret=b"a" * 32)
        again = derive_fingerprint_seed("google-search", secret=b"a" * 32)
        other_install = derive_fingerprint_seed("google-search", secret=b"b" * 32)

        self.assertEqual(first, again)
        self.assertNotEqual(first, other_install)

    def test_aliases_and_target_hosts_share_the_site_family(self) -> None:
        self.assertEqual(identity_policy("google_images").key, "google-search")
        self.assertEqual(
            identity_policy(None, "news.google.com").key,
            "google-search",
        )
        linkedin = identity_policy(None, "www.linkedin.com")
        self.assertEqual(linkedin.key, "linkedin")
        self.assertTrue(linkedin.persistent)
        self.assertEqual(
            identity_policy("meta_ad_library").key,
            identity_policy(None, "www.facebook.com").key,
        )

    def test_automatic_profiles_are_split_by_proxy_affinity(self) -> None:
        direct = resolve_identity(engine_name="reddit", secret=b"c" * 32)
        proxied = resolve_identity(
            engine_name="reddit",
            proxy="http://user:secret@proxy.example:8080",
            secret=b"c" * 32,
        )

        self.assertEqual(direct.profile_dir, Path(self.tmp.name) / "profiles/reddit")
        self.assertIn("proxy-", str(proxied.profile_dir))
        self.assertNotEqual(direct.profile_dir, proxied.profile_dir)
        self.assertNotEqual(direct.fingerprint_seed, proxied.fingerprint_seed)
        self.assertNotIn("secret", proxied.launch_key)

    def test_http_cache_is_partitioned_without_storing_proxy_password(self) -> None:
        direct = http_cache_partition("arxiv")
        proxied = http_cache_partition(
            "arxiv",
            "http://user:secret@proxy.example:8080",
        )

        self.assertNotEqual(direct, proxied)
        self.assertNotIn("secret", proxied)

    def test_corrupt_install_key_fails_closed(self) -> None:
        identity_dir = Path(self.tmp.name) / "identity"
        identity_dir.mkdir(parents=True, exist_ok=True)
        (identity_dir / "identity.key").write_bytes(b"short")

        with self.assertRaisesRegex(RuntimeError, "identity key is corrupt"):
            resolve_identity(engine_name="google")

    def test_first_run_race_waits_for_the_winning_writer(self) -> None:
        identity_dir = Path(self.tmp.name) / "identity"
        identity_dir.mkdir(parents=True, exist_ok=True)
        key_path = identity_dir / "identity.key"

        with patch(
            "agent_search.identity.os.open",
            side_effect=FileExistsError,
        ), patch.object(
            Path,
            "read_bytes",
            side_effect=(FileNotFoundError, b"", b"e" * 32),
        ), patch("agent_search.identity.time.sleep"):
            identity = resolve_identity(engine_name="google")

        self.assertIsInstance(identity.fingerprint_seed, int)
        self.assertFalse(key_path.exists())

    def test_explicit_automatic_path_keeps_the_same_fingerprint(self) -> None:
        automatic = resolve_identity(engine_name="twitter", secret=b"d" * 32)
        explicit = resolve_identity(
            engine_name="twitter",
            explicit_profile=str(automatic.profile_dir),
            secret=b"d" * 32,
        )

        self.assertEqual(automatic.fingerprint_seed, explicit.fingerprint_seed)
        self.assertEqual(automatic.launch_key, explicit.launch_key)

        proxied = resolve_identity(
            engine_name="twitter",
            proxy="http://proxy.example:8080",
            secret=b"d" * 32,
        )
        proxied_explicit = resolve_identity(
            engine_name="twitter",
            proxy="http://proxy.example:8080",
            explicit_profile=str(proxied.profile_dir),
            secret=b"d" * 32,
        )
        self.assertEqual(proxied.launch_key, proxied_explicit.launch_key)

    def test_explicit_profile_refuses_another_proxy_identity(self) -> None:
        profile = Path(self.tmp.name) / "profiles/alice"
        profile.mkdir(parents=True)
        bind_profile_to_proxy(profile, "first")

        with self.assertRaises(ProfileProxyMismatchError):
            bind_profile_to_proxy(profile, "second")

    @unittest.skipUnless(os.name == "posix", "fcntl lock assertion")
    def test_browser_session_lease_enforces_the_host_wide_limit(self) -> None:
        first = BrowserSessionLease(timeout_s=0, slot_count=1).acquire()
        try:
            with self.assertRaises(BrowserSessionBusyError):
                BrowserSessionLease(timeout_s=0, slot_count=1).acquire()
        finally:
            first.release()

        BrowserSessionLease(timeout_s=0, slot_count=1).acquire().release()

    @unittest.skipUnless(os.name == "posix", "fcntl lock assertion")
    def test_profile_lease_blocks_a_second_owner(self) -> None:
        profile = Path(self.tmp.name) / "profiles/reddit"
        first = ProfileLease(profile, timeout_s=0).acquire()
        try:
            with self.assertRaises(ProfileBusyError):
                ProfileLease(profile, timeout_s=0).acquire()
        finally:
            first.release()

        ProfileLease(profile, timeout_s=0).acquire().release()


if __name__ == "__main__":
    unittest.main()
