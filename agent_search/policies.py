"""Central browser-identity and search-execution policies.

The project has many aliases and adapters, but a browser identity belongs to
the site family being visited rather than to a CLI spelling.  Keeping that
mapping here prevents launch parameters, retry budgets, and cache behaviour
from drifting independently across CLI, MCP, HTTP, and multi-engine workers.

Only a deterministic fingerprint seed is fixed for ordinary public engines.
Disk-backed profiles are reserved for sites where login state or a regular
(non-incognito) profile materially improves availability.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class IdentityPolicy:
    """Stable browser identity settings for one site family."""

    key: str
    timezone: str | None
    locale: str | None
    persistent: bool = False
    humanize: bool = False
    risk: str = "normal"


@dataclass(frozen=True)
class SearchPolicy:
    """Execution budget for an adapter.

    ``deadline_s`` is a cooperative deadline inside a single browser session;
    multi-engine workers additionally enforce it by terminating the worker
    process.  Cache TTL is zero for account/ad surfaces where stale or
    identity-specific results must never cross requests.
    """

    deadline_s: float = 35.0
    max_attempts: int = 2
    navigation_retries: int = 1
    retry_delay_s: tuple[float, float] = (0.25, 0.75)
    cache_ttl_s: int = 180
    risk: str = "normal"


_IDENTITY_GROUPS = {
    "google": "google-search",
    "google_images": "google-search",
    "google_maps": "google-search",
    "google_patents": "google-search",
    "google_ad_transparency": "google-ads",
    "youtube": "youtube",
    "bing": "microsoft-search",
    "bing_images": "microsoft-search",
    "duckduckgo": "duckduckgo",
    "duckduckgo_images": "duckduckgo",
    "brave": "brave-search",
    "brave_images": "brave-search",
    "reddit": "reddit",
    "reddit_subreddit": "reddit",
    "x": "twitter",
    "linkedin": "linkedin",
    "linkedin_jobs": "linkedin",
    "instagram": "instagram",
    "instagram_ad_library": "instagram",
    "meta_ad_library": "facebook",
    "tiktok": "tiktok",
    "tiktok_ad_library": "tiktok",
    "tiktok_creative_center": "tiktok",
    "so360": "so360",
    "so360_images": "so360",
    "sogou": "sogou",
    "sogou_images": "sogou",
    "naver": "naver",
    "naver_images": "naver",
    "daum": "daum",
    "daum_images": "daum",
    "yandex": "yandex",
    "yandex_images": "yandex",
    "yahoo_japan": "yahoo-japan",
    "yahoo_japan_images": "yahoo-japan",
}

_HOST_ENGINES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("google.com", "googleusercontent.com"), "google"),
    (("youtube.com", "youtu.be"), "youtube"),
    (("bing.com",), "bing"),
    (("duckduckgo.com",), "duckduckgo"),
    (("reddit.com", "redd.it"), "reddit"),
    (("x.com", "twitter.com"), "twitter"),
    (("linkedin.com",), "linkedin"),
    (("instagram.com",), "instagram"),
    (("facebook.com", "fb.com"), "facebook"),
    (("tiktok.com",), "tiktok"),
    (("bilibili.com",), "bilibili"),
    (("douyin.com",), "douyin"),
    (("weibo.com",), "weibo"),
    (("xiaohongshu.com",), "xiaohongshu"),
    (("zhihu.com",), "zhihu"),
    (("medium.com",), "medium"),
    (("quora.com",), "quora"),
    (("glassdoor.com",), "glassdoor"),
)

_PERSISTENT_ENGINES = {
    "bilibili",
    "douyin",
    "facebook",
    "glassdoor",
    "instagram",
    "instagram_ad_library",
    "linkedin",
    "linkedin_jobs",
    "medium",
    "meta_ad_library",
    "quora",
    "reddit",
    "reddit_subreddit",
    "tiktok",
    "tiktok_ad_library",
    "tiktok_creative_center",
    "twitter",
    "x",
    "weibo",
    "xiaohongshu",
    "zhihu",
}

_HIGH_RISK_ENGINES = _PERSISTENT_ENGINES | {
    "amazon",
    "booking",
    "indeed",
    "netflix",
    "pinterest",
    "spotify",
    "twitch",
}

_AD_ENGINES = {
    "google_ad_transparency",
    "instagram_ad_library",
    "meta_ad_library",
    "tiktok_ad_library",
    "tiktok_creative_center",
}

_API_ENGINES = {"arxiv", "hackernews", "npm", "pubmed"}

def _engine_from_host(host: str | None) -> str:
    clean_host = (host or "").lower().split(":", 1)[0]
    if clean_host.startswith("www."):
        clean_host = clean_host[4:]
    for domains, engine in _HOST_ENGINES:
        if any(
            clean_host == domain or clean_host.endswith(f".{domain}")
            for domain in domains
        ):
            return engine
    return ""


def canonical_identity_key(engine_name: str | None, host: str | None = None) -> str:
    """Return the identity family shared by aliases and related adapters."""
    engine = (engine_name or "").strip().lower().replace("-", "_")
    engine = engine or _engine_from_host(host)
    if engine:
        return _IDENTITY_GROUPS.get(engine, engine)
    clean_host = (host or "").lower().split(":", 1)[0]
    if clean_host.startswith("www."):
        clean_host = clean_host[4:]
    return f"host:{clean_host}" if clean_host else "default"


def identity_policy(engine_name: str | None, host: str | None = None) -> IdentityPolicy:
    """Resolve a stable identity without prescribing brittle GPU/UA values."""
    engine = (engine_name or "").strip().lower().replace("-", "_")
    engine = engine or _engine_from_host(host)
    # Direct connections inherit CloakBrowser/system reality. A public fixed
    # timezone/locale would cluster installations and can contradict the exit
    # IP. Operators may still pin both for a controlled deployment.
    timezone = os.environ.get("AGENTSEARCH_DEFAULT_TIMEZONE")
    locale = os.environ.get("AGENTSEARCH_DEFAULT_LOCALE")

    persistent_enabled = os.environ.get("AGENTSEARCH_AUTO_PROFILES", "1") != "0"
    persistent = persistent_enabled and engine in _PERSISTENT_ENGINES
    high_risk = engine in _HIGH_RISK_ENGINES
    return IdentityPolicy(
        key=canonical_identity_key(engine, host),
        timezone=timezone,
        locale=locale,
        persistent=persistent,
        humanize=high_risk,
        risk="high" if high_risk else "normal",
    )


def search_policy(engine_name: str | None) -> SearchPolicy:
    """Return conservative per-engine budgets tuned by transport/risk."""
    engine = (engine_name or "").strip().lower().replace("-", "_")
    if engine in _API_ENGINES:
        return SearchPolicy(
            deadline_s=15.0,
            max_attempts=1,
            navigation_retries=0,
            retry_delay_s=(0.0, 0.0),
            cache_ttl_s=300,
            risk="api",
        )
    if engine in _AD_ENGINES:
        return SearchPolicy(
            deadline_s=60.0,
            max_attempts=1,
            navigation_retries=1,
            retry_delay_s=(0.5, 1.0),
            cache_ttl_s=0,
            risk="high",
        )
    if engine in _HIGH_RISK_ENGINES:
        return SearchPolicy(
            deadline_s=45.0,
            max_attempts=2,
            navigation_retries=1,
            retry_delay_s=(0.5, 1.25),
            cache_ttl_s=0,
            risk="high",
        )
    if engine in {"google", "bing"}:
        # Historical failures on challenge pages were spending 40-150 seconds
        # retrying the same blocked identity. Fail quickly so fallback can act.
        return SearchPolicy(
            deadline_s=20.0,
            max_attempts=1,
            navigation_retries=0,
            retry_delay_s=(0.0, 0.0),
            cache_ttl_s=120,
            risk="guarded",
        )
    if engine == "qwant":
        # Qwant occasionally never fires DOMContentLoaded even after useful
        # HTML arrives. Bound navigation aggressively so fallback can act.
        return SearchPolicy(
            deadline_s=10.0,
            max_attempts=1,
            navigation_retries=0,
            retry_delay_s=(0.0, 0.0),
            cache_ttl_s=120,
            risk="guarded",
        )
    if engine in {"duckduckgo", "startpage", "brave", "ecosia"}:
        return SearchPolicy(
            deadline_s=25.0,
            max_attempts=2,
            navigation_retries=1,
            retry_delay_s=(0.25, 0.6),
            cache_ttl_s=180,
            risk="guarded",
        )
    return SearchPolicy()


def engine_uses_browser(engine_cls: type) -> bool:
    """Return False for adapters that declare the direct HTTP transport."""
    return getattr(engine_cls, "transport", "browser") != "http"


def browser_concurrency_limit() -> int:
    """Return the licensed browser-session budget used by fan-out workers.

    CloakBrowser's current free key permits one latest-binary session.  Paid
    installations can raise this explicitly without changing code.
    """
    raw = os.environ.get("AGENTSEARCH_BROWSER_CONCURRENCY", "1")
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def retain_browser_sessions() -> bool:
    """Return whether long-running servers may keep an idle browser open.

    Codex and other MCP hosts commonly start one server process per project or
    conversation.  Retaining the free license's only session in any one of
    those idle processes would starve every other client, so cross-project
    installations release by default.  A dedicated single-client deployment
    can opt back into warm-session reuse explicitly.
    """
    return os.environ.get("AGENTSEARCH_RETAIN_BROWSER", "0") == "1"


def max_parallelism(requested: int) -> int:
    """Cap process/thread fan-out independently from browser-session count."""
    raw = os.environ.get("AGENTSEARCH_MAX_PARALLEL", "8")
    try:
        ceiling = max(1, int(raw))
    except ValueError:
        ceiling = 8
    return max(1, min(int(requested), ceiling))
