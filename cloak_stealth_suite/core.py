"""Core browser launch and configuration."""

import logging
import random
import time
from dataclasses import dataclass, field

import cloakbrowser

log = logging.getLogger(__name__)

TIMEZONES = [
    ("America/New_York", "en-US"),
    ("America/Chicago", "en-US"),
    ("America/Los_Angeles", "en-US"),
    ("Europe/London", "en-GB"),
    ("Europe/Berlin", "de-DE"),
]


@dataclass
class BrowserConfig:
    headless: bool = True
    proxy: str | None = None
    timezone: str | None = None
    locale: str | None = None
    humanize: bool = True
    geoip: bool = False
    extra_args: list[str] = field(default_factory=list)


def launch(config: BrowserConfig | None = None) -> "Browser":
    """Launch a stealth browser with the given config."""
    cfg = config or BrowserConfig()

    tz = cfg.timezone
    loc = cfg.locale
    if not tz and not cfg.geoip:
        tz, loc = random.choice(TIMEZONES)

    kwargs = dict(
        headless=cfg.headless,
        proxy=cfg.proxy,
        timezone=tz,
        locale=loc or "en-US",
        geoip=cfg.geoip,
        humanize=cfg.humanize,
    )
    if cfg.extra_args:
        kwargs["args"] = cfg.extra_args

    log.info("Launching browser: headless=%s tz=%s locale=%s", cfg.headless, tz, loc)
    return cloakbrowser.launch(**kwargs)


def new_page(browser, user_agent: str | None = None):
    """Create a new page with optional UA override."""
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
