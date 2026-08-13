"""Core browser launch and configuration."""

import logging
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

import cloakbrowser

from .identity import (
    BrowserSessionLease,
    ProfileLease,
    bind_profile_to_proxy,
    profiles_dir,
    resolve_identity,
)

log = logging.getLogger(__name__)


def environment_proxy_url() -> str | None:
    """Return the deployment-wide proxy without logging its credentials."""
    for name in (
        "AGENTSEARCH_PROXY",
        "FLUXISP_PROXY",
        "HTTPS_PROXY",
        "HTTP_PROXY",
    ):
        value = os.environ.get(name)
        if value:
            return value
    return None


def profile_path(name: str) -> Path:
    """Return the on-disk directory for the named persistent profile.

    The directory is *not* created here — the caller decides whether to
    `mkdir(parents=True, exist_ok=True)`. This lets callers detect a
    "profile doesn't exist yet" situation if they care.
    """
    if not name or "/" in name or ".." in name or "\\" in name:
        raise ValueError(f"invalid profile name: {name!r}")
    # Resolve the environment at call time so tests and long-running hosts can
    # redirect profile storage before their first browser launch.
    return profiles_dir() / name


@dataclass
class BrowserConfig:
    headless: bool = True
    proxy: str | None = None
    timezone: str | None = None
    locale: str | None = None
    humanize: bool | None = None
    geoip: bool | None = None
    extra_args: list[str] = field(default_factory=list)
    # Canonical adapter name or target URL. These select a stable site-family
    # identity; aliases such as ddg/duckduckgo resolve to the same policy.
    engine_name: str | None = None
    target_url: str | None = None
    # When set, launch a persistent context backed by this directory so
    # cookies / localStorage / IndexedDB survive across runs. Use this to
    # carry login state for sites like Twitter, LinkedIn, Glassdoor, etc.
    # Stealth (CloakBrowser's C++ patches) still applies — this is strictly
    # better than driving the user's real Chrome via CDP.
    user_data_dir: str | None = None
    # A persistent Chromium directory cannot be opened concurrently. The
    # lease is held until browser.close() and bounds how long callers wait.
    profile_lock_timeout_s: float = 10.0
    # CloakBrowser licenses cap sessions across processes, not just inside one
    # MCP server. This bounds how long a launch waits for a host-wide slot.
    browser_lock_timeout_s: float = 45.0
    # Optional proxy rotation pool. When ``proxy`` is None and this is set,
    # ``launch()`` calls ``proxy_pool.next()`` to pick a proxy URL per
    # browser launch (across-invocation rotation). The pool's strategy
    # (random / round-robin / sticky) decides the order.
    # Type-hinted as ``Any`` to avoid a hard import cycle with proxy.py.
    proxy_pool: object | None = None


def resolve_config_proxy(cfg: BrowserConfig) -> str | None:
    """Resolve an explicit or pooled proxy once and retain the picked slot."""
    effective_proxy = cfg.proxy
    cfg._picked_proxy = None  # type: ignore[attr-defined]
    if effective_proxy or cfg.proxy_pool is None:
        return effective_proxy
    try:
        picked = cfg.proxy_pool.next()
    except Exception as e:
        log.warning("[proxy] pool.next() failed: %s", e)
        picked = None
    if picked is None:
        log.warning("[proxy] pool empty - continuing without proxy")
        return None
    cfg._picked_proxy = picked  # type: ignore[attr-defined]
    log.info(
        "[proxy] using %s://%s:%d (source=%s, score=%.2f)",
        picked.scheme,
        picked.host,
        picked.port,
        picked.source or "user",
        picked.health_score(),
    )
    return picked.url


def _attach_lifetime_leases(resource, leases: list, identity):
    """Release host/profile leases exactly once when Playwright closes."""
    original_close = resource.close
    closed = False

    def close_with_leases(*args, **kwargs):
        nonlocal closed
        if closed:
            return None
        closed = True
        try:
            return original_close(*args, **kwargs)
        finally:
            # Profile ownership is narrower than the licensed browser slot and
            # is released first, mirroring reverse acquisition order.
            for lease in leases:
                lease.release()

    resource.close = close_with_leases
    resource._agentsearch_identity = identity
    return resource


def launch(config: BrowserConfig | None = None):
    """Launch a stealth browser (or a persistent context) with the given config.

    Returns a CloakBrowser ``Browser`` (no profile) or ``BrowserContext``
    (with profile). Both expose ``.new_page()`` and ``.close()`` so the
    rest of the codebase doesn't need to know which one it got.
    """
    cfg = config or BrowserConfig()

    # Resolve the effective proxy: explicit URL takes precedence, otherwise
    # consult the rotation pool (if any). The picked Proxy is stashed back
    # on cfg so callers can `mark_ok` / `mark_fail` after the run.
    effective_proxy = resolve_config_proxy(cfg)

    identity = resolve_identity(
        engine_name=cfg.engine_name,
        target_url=cfg.target_url,
        proxy=effective_proxy,
        explicit_profile=cfg.user_data_dir,
    )
    cfg._identity = identity  # type: ignore[attr-defined]

    use_geoip = cfg.geoip if cfg.geoip is not None else bool(effective_proxy)
    tz = cfg.timezone
    loc = cfg.locale
    if not tz and not use_geoip:
        tz = identity.policy.timezone
    if not loc and not use_geoip:
        loc = identity.policy.locale
    humanize = (
        cfg.humanize
        if cfg.humanize is not None
        else identity.policy.humanize
    )

    extra_args = list(cfg.extra_args)
    if not any(arg.startswith("--fingerprint=") for arg in extra_args):
        extra_args.append(f"--fingerprint={identity.fingerprint_seed}")

    common = dict(
        headless=cfg.headless,
        proxy=effective_proxy,
        timezone=tz,
        locale=loc,
        geoip=use_geoip,
        humanize=humanize,
    )
    common["args"] = extra_args

    user_data_dir = cfg.user_data_dir or (
        str(identity.profile_dir) if identity.profile_dir else None
    )
    if user_data_dir:
        # Prepare storage before occupying a scarce licensed session slot.
        Path(user_data_dir).mkdir(parents=True, exist_ok=True)
    session_lease = BrowserSessionLease(
        timeout_s=cfg.browser_lock_timeout_s
    ).acquire()
    if user_data_dir:
        log.info(
            "Launching persistent context: identity=%s dir=%s headless=%s "
            "tz=%s locale=%s geoip=%s",
            identity.policy.key,
            user_data_dir,
            cfg.headless,
            tz,
            loc,
            use_geoip,
        )
        profile_lease = None
        try:
            profile_lease = ProfileLease(
                Path(user_data_dir), timeout_s=cfg.profile_lock_timeout_s
            ).acquire()
            # Verify affinity while holding the same exclusive lease as
            # Chromium; otherwise two first launches could race the marker.
            bind_profile_to_proxy(Path(user_data_dir), identity.proxy_affinity)
            context = cloakbrowser.launch_persistent_context(
                user_data_dir=user_data_dir,
                **common,
            )
        except BaseException:
            if profile_lease is not None:
                profile_lease.release()
            session_lease.release()
            raise
        return _attach_lifetime_leases(
            context,
            [profile_lease, session_lease],
            identity,
        )

    log.info(
        "Launching browser: identity=%s headless=%s tz=%s locale=%s geoip=%s",
        identity.policy.key,
        cfg.headless,
        tz,
        loc,
        use_geoip,
    )
    try:
        browser = cloakbrowser.launch(**common)
    except BaseException:
        session_lease.release()
        raise
    return _attach_lifetime_leases(browser, [session_lease], identity)


def new_page(browser, user_agent: str | None = None):
    """Create a new page with optional UA override.

    Works for both ``Browser`` (anonymous) and ``BrowserContext``
    (persistent profile) — both expose ``new_page()``.
    """
    page = browser.new_page()
    if user_agent:
        page.set_extra_http_headers({"User-Agent": user_agent})
    return page


def safe_goto(page, url: str, timeout: int = 30000, retries: int = 2) -> bool:
    """Navigate within the active engine's deadline and retry budget."""
    from .execution import (
        SearchDeadlineExceeded,
        current_trace,
        remaining_seconds,
    )

    trace = current_trace()
    if trace is not None:
        retries = min(retries, trace.policy.navigation_retries)
    for attempt in range(retries + 1):
        remaining = remaining_seconds()
        if remaining is not None and remaining <= 0:
            if trace is not None:
                trace.deadline_reached = True
            raise SearchDeadlineExceeded("search deadline reached before navigation")
        effective_timeout = timeout
        if remaining is not None:
            effective_timeout = max(1, min(timeout, int(remaining * 1000)))
        started = time.monotonic()
        try:
            page.goto(
                url,
                timeout=effective_timeout,
                wait_until="domcontentloaded",
            )
            if trace is not None and trace.error.startswith("navigation_failed:"):
                trace.error = ""
            return True
        except Exception as e:
            log.warning("goto %s failed (attempt %d): %s", url, attempt + 1, e)
            if attempt < retries:
                human_delay(0.2, 0.6)
        finally:
            if trace is not None:
                trace.navigation_count += 1
                trace.navigation_ms += int((time.monotonic() - started) * 1000)
    if trace is not None and not trace.error:
        trace.error = f"navigation_failed: {url}"
    remaining = remaining_seconds()
    if remaining is not None and remaining <= 0 and trace is not None:
        # A final Playwright timeout can consume the remainder without
        # entering another loop iteration where the deadline is checked.
        trace.deadline_reached = True
    return False


def human_delay(min_s: float = 0.5, max_s: float = 2.0):
    """Sleep without crossing the active engine's cooperative deadline."""
    from .execution import SearchDeadlineExceeded, current_trace, remaining_seconds

    trace = current_trace()
    delay = random.uniform(min_s, max_s)
    remaining = remaining_seconds()
    if remaining is not None:
        if remaining <= 0:
            if trace is not None:
                trace.deadline_reached = True
            raise SearchDeadlineExceeded("search deadline reached before delay")
        delay = min(delay, max(0.0, remaining - 0.01))
    if trace is not None:
        trace.wait_ms += int(delay * 1000)
    if delay > 0:
        time.sleep(delay)


def wait_for_any(page, selectors: list[str] | tuple[str, ...], timeout: int = 5000) -> bool:
    """Wait once for any CSS selector under one total, deadline-aware budget.

    Waiting for fallback selectors sequentially multiplies tail latency (four
    selectors at 5s became 20s before parsing). Chromium accepts a comma-joined
    CSS selector, so one wait preserves fallback coverage without that tax.
    """
    from .execution import (
        SearchDeadlineExceeded,
        current_trace,
        remaining_seconds,
    )

    if not selectors:
        return False
    effective_timeout = max(1, int(timeout))
    remaining = remaining_seconds()
    if remaining is not None:
        if remaining <= 0:
            trace = current_trace()
            if trace is not None:
                trace.deadline_reached = True
            raise SearchDeadlineExceeded("search deadline reached before wait")
        effective_timeout = max(1, min(effective_timeout, int(remaining * 1000)))
    started = time.monotonic()
    try:
        page.wait_for_selector(
            ", ".join(selectors),
            timeout=effective_timeout,
            state="attached",
        )
        return True
    except Exception:
        remaining = remaining_seconds()
        if remaining is not None and remaining <= 0:
            trace = current_trace()
            if trace is not None:
                trace.deadline_reached = True
            raise SearchDeadlineExceeded("search deadline reached during wait")
        return False
    finally:
        trace = current_trace()
        if trace is not None:
            trace.condition_wait_ms += int((time.monotonic() - started) * 1000)
