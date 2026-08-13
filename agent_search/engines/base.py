"""Base adapter for all engines."""

import logging
import random

import requests

from ..core import safe_goto, human_delay
from ..execution import (
    SearchDeadlineExceeded,
    current_trace,
    ensure_time_remaining,
    remaining_seconds,
)
from ..policies import search_policy
from ..results import SearchResult
from ..stealth.enhance import apply_stealth, check_blocked

log = logging.getLogger(__name__)


class BaseEngine:
    """Base class for site adapters."""

    name: str = "base"
    max_retries: int = 3
    transport: str = "browser"

    def __init__(self, page):
        self.page = page
        if page is not None:
            apply_stealth(page)

    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Execute under the central attempt budget and cooperative deadline."""
        policy = current_trace().policy if current_trace() else search_policy(self.name)
        attempts = max(1, min(self.max_retries, policy.max_attempts))
        for attempt in range(attempts):
            ensure_time_remaining()
            trace = current_trace()
            if trace is not None:
                trace.attempts = attempt + 1
            try:
                results = self._do_search(query, limit)
                # A timed-out navigation leaves Playwright's page in an
                # in-flight state where even page.title() may consume its 30s
                # default. The navigation failure already explains the empty
                # result, so do not turn it into another hidden timeout.
                navigation_failed = bool(
                    trace and trace.error.startswith("navigation_failed:")
                )
                blocked = (
                    check_blocked(self.page)
                    if self.page is not None and not navigation_failed
                    else None
                )
                if blocked:
                    log.warning("[%s] Blocked (attempt %d): %s", self.name, attempt + 1, blocked)
                    if trace is not None:
                        trace.blocked_reason = str(blocked)
                    continue
                if results:
                    return results
                log.warning("[%s] No results (attempt %d)", self.name, attempt + 1)
            except SearchDeadlineExceeded:
                if trace is not None:
                    trace.deadline_reached = True
                raise
            except Exception as e:
                log.error("[%s] Error (attempt %d): %s", self.name, attempt + 1, e)
                if trace is not None:
                    trace.error = f"{type(e).__name__}: {e}"
            if attempt + 1 < attempts:
                human_delay(*policy.retry_delay_s)
        return []

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        raise NotImplementedError


class HttpEngine(BaseEngine):
    """Base for documented public APIs that do not need Chromium.

    Keeping these adapters on a requests session avoids browser startup and
    preserves proxy support. Browser-only fallbacks should live in a separate
    adapter so one API call cannot unexpectedly consume a licensed session.
    """

    transport = "http"
    request_timeout_s = 15.0

    def __init__(self, page=None, *, proxy: str | None = None):
        super().__init__(page)
        self.session = requests.Session()
        self.set_proxy(proxy)

    def set_proxy(self, proxy: str | None) -> None:
        self.proxy = proxy
        self.session.proxies.clear()
        if proxy:
            self.session.proxies.update({"http": proxy, "https": proxy})

    def http_get(self, url: str, **kwargs) -> requests.Response:
        """GET with timeout clamped to the remaining search budget."""
        ensure_time_remaining()
        remaining = remaining_seconds()
        timeout = kwargs.pop("timeout", self.request_timeout_s)
        if remaining is not None:
            timeout = max(0.1, min(float(timeout), remaining))
        response = self.session.get(url, timeout=timeout, **kwargs)
        response.raise_for_status()
        return response
