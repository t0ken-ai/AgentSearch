"""Per-install, per-site browser identity resolution and profile locking."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import urlparse

from .policies import (
    IdentityPolicy,
    browser_concurrency_limit,
    identity_policy,
)


def _identity_home() -> Path:
    return Path(
        os.environ.get(
            "AGENTSEARCH_IDENTITY_DIR",
            str(Path.home() / ".config" / "agentsearch"),
        )
    )


def profiles_dir() -> Path:
    """Return the root for automatic and user-created persistent profiles."""
    return Path(
        os.environ.get(
            "AGENTSEARCH_PROFILES_DIR",
            str(Path.home() / ".cache" / "agentsearch" / "profiles"),
        )
    )


def browser_slots_dir() -> Path:
    """Return the host-wide lock root for licensed Chromium sessions."""
    return Path(
        os.environ.get(
            "AGENTSEARCH_BROWSER_SLOT_DIR",
            str(profiles_dir().parent / "browser-slots"),
        )
    )


def _load_or_create_secret() -> bytes:
    """Load the private install seed, creating it atomically on first use.

    The seed is intentionally local and never logged or committed.  Deriving
    identities from it makes a site see a returning device while ensuring two
    AgentSearch installations do not collapse into one public fingerprint.
    """
    path = _identity_home() / "identity.key"
    path.parent.mkdir(parents=True, exist_ok=True)

    def validated(value: bytes) -> bytes:
        if len(value) != 32:
            raise RuntimeError(
                f"identity key is corrupt (expected 32 bytes): {path}"
            )
        return value

    try:
        return validated(path.read_bytes())
    except FileNotFoundError:
        value = secrets.token_bytes(32)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            # Another process won first-run initialization. O_EXCL becomes
            # visible just before its 32-byte write, so tolerate that tiny
            # window without treating a genuinely corrupt old key as valid.
            for _ in range(20):
                candidate = path.read_bytes()
                if len(candidate) == 32:
                    return candidate
                time.sleep(0.05)
            return validated(path.read_bytes())
        with os.fdopen(fd, "wb") as handle:
            handle.write(value)
        return value


def _proxy_descriptor(proxy: str | None) -> str:
    """Return a credential-free proxy identity suitable for HMAC input."""
    if not proxy:
        return "direct"
    try:
        parsed = urlparse(proxy)
        host = parsed.hostname or "unknown"
        port = parsed.port or 0
        user = parsed.username or ""
        return f"{parsed.scheme.lower()}://{user}@{host.lower()}:{port}"
    except (TypeError, ValueError):
        return "proxy:custom"


def derive_fingerprint_seed(
    identity_key: str,
    proxy: str | None = None,
    *,
    secret: bytes | None = None,
) -> int:
    """Derive a stable positive 31-bit seed for one site/proxy identity."""
    material = f"{identity_key}\0{_proxy_descriptor(proxy)}".encode("utf-8")
    digest = hmac.new(
        secret or _load_or_create_secret(), material, hashlib.sha256
    ).digest()
    # CloakBrowser's default generator uses five digits, but the Chromium
    # patch accepts a normal positive integer. A wider space prevents site
    # families from colliding as the registry grows.
    return 10_000 + int.from_bytes(digest[:4], "big") % 2_000_000_000


@dataclass(frozen=True)
class ResolvedIdentity:
    """Fully resolved identity used by a single browser process."""

    policy: IdentityPolicy
    fingerprint_seed: int
    profile_dir: Path | None
    proxy_affinity: str
    launch_key: str

    @property
    def cache_partition(self) -> str:
        return self.launch_key


def http_cache_partition(
    engine_name: str | None,
    proxy: str | None = None,
) -> str:
    """Partition public HTTP results by adapter and credential-free egress.

    API responses can be localized or rate-limited by exit IP just like SERPs.
    Reusing a direct-US response after switching to another proxy would make
    the short cache surprising, so HTTP and browser paths share the same
    private affinity derivation without exposing proxy credentials on disk.
    """
    identity = resolve_identity(engine_name=engine_name, proxy=proxy)
    return f"http:{identity.cache_partition}"


def resolve_identity(
    *,
    engine_name: str | None = None,
    target_url: str | None = None,
    proxy: str | None = None,
    explicit_profile: str | None = None,
    secret: bytes | None = None,
) -> ResolvedIdentity:
    """Resolve identity, locale policy, and optional persistent directory."""
    host = ""
    if target_url:
        try:
            host = urlparse(target_url).hostname or ""
        except ValueError:
            host = ""
    policy = identity_policy(engine_name, host)
    secret_value = secret or _load_or_create_secret()
    proxy_digest = hmac.new(
        secret_value,
        _proxy_descriptor(proxy).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:12]

    automatic_profile: Path | None = None
    if policy.persistent:
        automatic_profile = profiles_dir() / policy.key
        if proxy:
            automatic_profile = automatic_profile / f"proxy-{proxy_digest}"

    profile: Path | None = None
    identity_key = policy.key
    if explicit_profile:
        profile = Path(explicit_profile).expanduser()
        if (
            automatic_profile is None
            or profile.resolve(strict=False)
            != automatic_profile.resolve(strict=False)
        ):
            identity_key = f"profile:{profile.resolve(strict=False)}"
        policy = replace(policy, key=identity_key, persistent=True)
    if policy.persistent and not explicit_profile:
        # Proxy-bound slots prevent one login profile from appearing in two
        # countries. Direct traffic keeps the original human-friendly path.
        profile = automatic_profile
    seed = derive_fingerprint_seed(identity_key, proxy, secret=secret_value)
    launch_key = f"{identity_key}:{proxy_digest}:{'disk' if profile else 'memory'}"
    return ResolvedIdentity(
        policy=policy,
        fingerprint_seed=seed,
        profile_dir=profile,
        proxy_affinity=proxy_digest,
        launch_key=launch_key,
    )


class ProfileBusyError(RuntimeError):
    """Raised when another process already owns a persistent profile."""


class ProfileProxyMismatchError(RuntimeError):
    """Raised when a disk profile is reopened through another proxy slot."""


class BrowserSessionBusyError(RuntimeError):
    """Raised when every licensed browser slot stays occupied."""


def bind_profile_to_proxy(profile: Path, proxy_affinity: str) -> None:
    """Persist and verify the egress identity paired with a disk profile.

    Cookies and device state form one identity with the proxy. Silently
    changing egress on an existing profile is more damaging than failing the
    launch, so callers must create/login a separate named profile instead.
    """
    marker = profile / ".agentsearch-proxy-affinity"
    try:
        existing = marker.read_text(encoding="ascii").strip()
    except FileNotFoundError:
        existing = ""
    if existing and not hmac.compare_digest(existing, proxy_affinity):
        raise ProfileProxyMismatchError(
            "persistent profile is bound to a different proxy identity; "
            "use a separate profile for each proxy/country"
        )
    if not existing:
        marker.write_text(proxy_affinity + "\n", encoding="ascii")
        try:
            marker.chmod(0o600)
        except OSError:
            pass


class BrowserSessionLease:
    """Hold one host-wide CloakBrowser license slot for a browser lifetime.

    MCP clients usually create separate server processes per project.  A
    process-local semaphore therefore cannot enforce CloakBrowser's global
    session allowance.  Advisory file locks survive crashes safely and let
    paid installations expose more slots through the existing concurrency
    setting without sharing browser objects across Playwright processes.
    """

    def __init__(
        self,
        timeout_s: float = 45.0,
        *,
        slot_count: int | None = None,
    ) -> None:
        self.timeout_s = max(0.0, float(timeout_s))
        configured_slots = (
            slot_count
            if slot_count is not None
            else browser_concurrency_limit()
        )
        self.slot_count = max(
            1,
            int(configured_slots),
        )
        self.path: Path | None = None
        self._handle = None

    @staticmethod
    def _lock(handle) -> None:
        if os.name == "posix":
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def acquire(self) -> "BrowserSessionLease":
        """Wait for any configured slot, then retain its file descriptor."""
        root = browser_slots_dir()
        root.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_s
        while True:
            for index in range(self.slot_count):
                path = root / f"session-{index}.lock"
                handle = open(path, "a+b")
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"0\n")
                    handle.flush()
                try:
                    self._lock(handle)
                except (BlockingIOError, OSError):
                    handle.close()
                    continue
                handle.seek(0)
                handle.truncate()
                handle.write(f"pid={os.getpid()}\n".encode("ascii"))
                handle.flush()
                self.path = path
                self._handle = handle
                return self
            if time.monotonic() >= deadline:
                raise BrowserSessionBusyError(
                    "all CloakBrowser session slots are in use; wait for the "
                    "active MCP/search operation to finish or raise "
                    "AGENTSEARCH_BROWSER_CONCURRENCY to the licensed limit"
                )
            time.sleep(0.1)

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            if os.name == "posix":
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            else:
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            handle.close()

    def __enter__(self) -> "BrowserSessionLease":
        return self.acquire()

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.release()


class ProfileLease:
    """Cross-process exclusive lease held for a persistent browser lifetime."""

    def __init__(self, profile: Path, timeout_s: float = 10.0) -> None:
        digest = hashlib.sha256(
            str(profile.resolve(strict=False)).encode()
        ).hexdigest()[:20]
        lock_dir = profiles_dir() / ".locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        self.path = lock_dir / f"{digest}.lock"
        self.timeout_s = max(0.0, timeout_s)
        self._handle = None

    def acquire(self) -> "ProfileLease":
        """Wait for exclusive ownership without deleting another process's lock."""
        handle = open(self.path, "a+b")
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + self.timeout_s
        while True:
            try:
                self._lock(handle)
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    handle.close()
                    raise ProfileBusyError(
                        f"profile is already in use: {self.path.name}"
                    )
                time.sleep(0.1)
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()}\n".encode())
        handle.flush()
        self._handle = handle
        return self

    @staticmethod
    def _lock(handle) -> None:
        if os.name == "posix":
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            if os.name == "posix":
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            else:
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            handle.close()

    def __enter__(self) -> "ProfileLease":
        return self.acquire()

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.release()
