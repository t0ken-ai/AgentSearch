"""Core browser launch and configuration."""

import logging
import os
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

import cloakbrowser

log = logging.getLogger(__name__)

TIMEZONES = [
    ("America/New_York", "en-US"),
    ("America/Chicago", "en-US"),
    ("America/Los_Angeles", "en-US"),
    ("Europe/London", "en-GB"),
    ("Europe/Berlin", "de-DE"),
]

# Where persistent browser profiles live by default. Each profile is its own
# directory under here (e.g. ./twitter, ./linkedin, ...). Override with the
# AGENTSEARCH_PROFILES_DIR environment variable.
DEFAULT_PROFILES_DIR = Path(
    os.environ.get(
        "AGENTSEARCH_PROFILES_DIR",
        str(Path.home() / ".cache" / "agentsearch" / "profiles"),
    )
)


def profile_path(name: str) -> Path:
    """Return the on-disk directory for the named persistent profile.

    The directory is *not* created here — the caller decides whether to
    `mkdir(parents=True, exist_ok=True)`. This lets callers detect a
    "profile doesn't exist yet" situation if they care.
    """
    if not name or "/" in name or ".." in name or "\\" in name:
        raise ValueError(f"invalid profile name: {name!r}")
    return DEFAULT_PROFILES_DIR / name


@dataclass
class BrowserConfig:
    headless: bool = True
    proxy: str | None = None
    timezone: str | None = None
    locale: str | None = None
    humanize: bool = True
    geoip: bool = False
    extra_args: list[str] = field(default_factory=list)
    # When set, launch a persistent context backed by this directory so
    # cookies / localStorage / IndexedDB survive across runs. Use this to
    # carry login state for sites like Twitter, LinkedIn, Glassdoor, etc.
    # Stealth (CloakBrowser's C++ patches) still applies — this is strictly
    # better than driving the user's real Chrome via CDP.
    user_data_dir: str | None = None
    # Optional proxy rotation pool. When ``proxy`` is None and this is set,
    # ``launch()`` calls ``proxy_pool.next()`` to pick a proxy URL per
    # browser launch (across-invocation rotation). The pool's strategy
    # (random / round-robin / sticky) decides the order.
    # Type-hinted as ``Any`` to avoid a hard import cycle with proxy.py.
    proxy_pool: object | None = None


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
    effective_proxy = cfg.proxy
    cfg._picked_proxy = None  # type: ignore[attr-defined]
    if not effective_proxy and cfg.proxy_pool is not None:
        try:
            picked = cfg.proxy_pool.next()
        except Exception as e:
            log.warning("[proxy] pool.next() failed: %s", e)
            picked = None
        if picked is not None:
            effective_proxy = picked.url
            cfg._picked_proxy = picked  # type: ignore[attr-defined]
            log.info(
                "[proxy] using %s://%s:%d (source=%s, score=%.2f)",
                picked.scheme, picked.host, picked.port,
                picked.source or "user", picked.health_score(),
            )
        else:
            log.warning("[proxy] pool empty — launching without proxy")

    tz = cfg.timezone
    loc = cfg.locale
    if not tz and not cfg.geoip:
        tz, loc = random.choice(TIMEZONES)

    common = dict(
        headless=cfg.headless,
        proxy=effective_proxy,
        timezone=tz,
        locale=loc or "en-US",
        geoip=cfg.geoip,
        humanize=cfg.humanize,
    )
    if cfg.extra_args:
        common["args"] = cfg.extra_args

    if cfg.user_data_dir:
        # Make sure the profile dir exists so CloakBrowser can write into it.
        Path(cfg.user_data_dir).mkdir(parents=True, exist_ok=True)
        log.info(
            "Launching persistent context: dir=%s headless=%s tz=%s locale=%s",
            cfg.user_data_dir,
            cfg.headless,
            tz,
            loc,
        )
        return cloakbrowser.launch_persistent_context(
            user_data_dir=cfg.user_data_dir,
            **common,
        )

    log.info("Launching browser: headless=%s tz=%s locale=%s", cfg.headless, tz, loc)
    return cloakbrowser.launch(**common)


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
    """Navigate to URL with retry logic."""
    for attempt in range(retries + 1):
        try:
            page.goto(url, timeout=timeout, wait_until="domcontentloaded")
            return True
        except Exception as e:
            log.warning("goto %s failed (attempt %d): %s", url, attempt + 1, e)
            if attempt < retries:
                time.sleep(2 + random.random() * 2)
    return False


def human_delay(min_s: float = 0.5, max_s: float = 2.0):
    """Random human-like delay."""
    time.sleep(random.uniform(min_s, max_s))
