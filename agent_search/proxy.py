"""Proxy support for AgentSearch.

A small, dependency-free proxy module that lets the CLI / agent rotate
through many proxies when the user's home IP gets rate-limited by IG /
YT / Reddit / etc.

Supports:
  * HTTP / HTTPS / SOCKS4 / SOCKS5 — both bare URLs and ``user:pass@host:port``
  * ``ProxyPool`` with random / round-robin / sticky strategies
  * Health checks against a real-world target (default ``https://www.google.com``)
  * Marking proxies as ok/fail so a long run drops dead ones
  * On-disk cache at ``~/.cache/agentsearch/proxies.json`` so the user
    doesn't re-fetch on every CLI call
  * Free-list fetchers from popular GitHub repos (proxifly / roosterkid /
    Zaeem20 / TheSpeedX) — just text files, no API key

CloakBrowser / Playwright accept proxies as either a URL string OR a dict
``{server, username?, password?, bypass?}``. We expose both via
:meth:`Proxy.url` and :meth:`Proxy.to_playwright_dict`.

Example::

    from agent_search.proxy import ProxyPool

    pool = ProxyPool.load_from_cache()                       # cached, fast
    if not pool.healthy():
        pool.fetch_from_github("proxifly_socks5", limit=200)  # live pull
        pool.test_all(max_workers=40)                         # filter dead
        pool.save()
    p = pool.next()
    cfg = BrowserConfig(headless=True, proxy=p.url)
    browser = launch(cfg)

The free-proxy lists hosted on GitHub are inherently low-quality (most
listed proxies are dead within minutes); for serious automation buy a
residential pool from Webshare / Bright Data / Oxylabs / IPRoyal and
load it via ``ProxyPool.load_from_file()`` — same API, much higher hit
rate.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import ssl
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from pathlib import Path

log = logging.getLogger(__name__)


def _ssl_context() -> ssl.SSLContext:
    """Build an SSL context that works on Python 3.14 / macOS where the
    system trust store isn't always wired into ``ssl.create_default_context()``.

    Prefers ``certifi`` (installed transitively via ``cloakbrowser``) when
    available; falls back to the platform default otherwise.
    """
    try:
        import certifi  # noqa: WPS433
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


_SSL_CTX = _ssl_context()


# ---------------------------------------------------------------- locations

DEFAULT_CACHE_DIR = Path(
    os.environ.get(
        "AGENTSEARCH_PROXY_CACHE_DIR",
        str(Path.home() / ".cache" / "agentsearch"),
    )
)

# When the user just wants "give me proxies", this is where they end up.
DEFAULT_CACHE_FILE = DEFAULT_CACHE_DIR / "proxies.json"


# ---------------------------------------------------------------- sources

# Curated free-list sources hosted on GitHub. Each entry is a tuple of
# (raw URL, default scheme inferred when entries don't carry a scheme).
# The user can mix-and-match via ``ProxyPool.fetch_from_github("proxifly_socks5")``
# or ``ProxyPool.fetch_from_github("all")`` to grab everything.
GITHUB_SOURCES: dict[str, tuple[str, str]] = {
    # proxifly/free-proxy-list — refreshed every 5 min, well-formed scheme://host:port
    "proxifly_http":   ("https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt", "http"),
    "proxifly_socks4": ("https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/socks4/data.txt", "socks4"),
    "proxifly_socks5": ("https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/socks5/data.txt", "socks5"),
    "proxifly_all":    ("https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/all/data.txt", ""),
    # roosterkid/openproxylist — hourly, host:port (no scheme; we use the inferred default)
    "roosterkid_https":  ("https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt", "https"),
    "roosterkid_socks4": ("https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS4_RAW.txt", "socks4"),
    "roosterkid_socks5": ("https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS5_RAW.txt", "socks5"),
    # TheSpeedX/PROXY-List — many entries, host:port
    "speedx_http":   ("https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt", "http"),
    "speedx_socks4": ("https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks4.txt", "socks4"),
    "speedx_socks5": ("https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt", "socks5"),
    # Zaeem20/FREE_PROXIES_LIST — every 10 min
    "zaeem_http":  ("https://raw.githubusercontent.com/Zaeem20/FREE_PROXIES_LIST/master/http.txt", "http"),
    "zaeem_socks4": ("https://raw.githubusercontent.com/Zaeem20/FREE_PROXIES_LIST/master/socks4.txt", "socks4"),
    "zaeem_socks5": ("https://raw.githubusercontent.com/Zaeem20/FREE_PROXIES_LIST/master/socks5.txt", "socks5"),
}


# Predefined "bundles" callers can ask for instead of naming individual sources.
SOURCE_BUNDLES: dict[str, list[str]] = {
    "all":    list(GITHUB_SOURCES.keys()),
    "http":   ["proxifly_http", "roosterkid_https", "speedx_http", "zaeem_http"],
    "socks":  ["proxifly_socks4", "proxifly_socks5", "roosterkid_socks4",
               "roosterkid_socks5", "speedx_socks4", "speedx_socks5",
               "zaeem_socks4", "zaeem_socks5"],
    "socks5": ["proxifly_socks5", "roosterkid_socks5", "speedx_socks5", "zaeem_socks5"],
    "socks4": ["proxifly_socks4", "roosterkid_socks4", "speedx_socks4", "zaeem_socks4"],
}


_PROXY_LINE_RE = re.compile(
    r"^\s*(?:(?P<scheme>https?|socks[45]h?)://)?"
    r"(?:(?P<user>[^:@/\s]+):(?P<pass>[^@/\s]+)@)?"
    r"(?P<host>[A-Za-z0-9_.-]+):(?P<port>\d{1,5})\s*$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------- model

@dataclass
class Proxy:
    """A single proxy endpoint with health-tracking metadata."""

    scheme: str  # "http" / "https" / "socks4" / "socks5"
    host: str
    port: int
    username: str = ""
    password: str = ""
    source: str = ""             # "user" / "proxifly_http" / etc.
    last_ok_at: float | None = None
    last_err: str = ""
    latency_ms: float | None = None
    success_count: int = 0
    fail_count: int = 0

    @property
    def server(self) -> str:
        """Just ``<scheme>://<host>:<port>`` — what Playwright wants in the
        ``server`` field of a ``ProxySettings`` dict (no embedded auth)."""
        return f"{self.scheme}://{self.host}:{self.port}"

    @property
    def url(self) -> str:
        """Full URL with embedded credentials (when present)."""
        if self.username:
            user = urllib.parse.quote(self.username, safe="")
            pw = urllib.parse.quote(self.password, safe="")
            return f"{self.scheme}://{user}:{pw}@{self.host}:{self.port}"
        return self.server

    def to_playwright_dict(self) -> dict:
        """The dict shape CloakBrowser / Playwright accept directly."""
        out = {"server": self.server}
        if self.username:
            out["username"] = self.username
        if self.password:
            out["password"] = self.password
        return out

    def health_score(self) -> float:
        """Higher is better. Combines hit rate with recency."""
        total = self.success_count + self.fail_count
        if total == 0:
            return 0.5  # untested → neutral
        rate = self.success_count / total
        # Decay penalty if we haven't seen a success recently.
        if self.last_ok_at:
            age_min = max(0.0, (time.time() - self.last_ok_at) / 60.0)
            recency = max(0.0, 1.0 - age_min / 60.0)  # 1h → 0
        else:
            recency = 0.0
        return rate * 0.7 + recency * 0.3

    @classmethod
    def from_url(cls, raw: str, *, default_scheme: str = "http",
                 source: str = "") -> "Proxy | None":
        """Parse a proxy URL or ``host:port`` line into a Proxy.

        Accepts:
          * ``http://user:pass@host:8080``
          * ``socks5://1.2.3.4:1080``
          * ``1.2.3.4:8080`` (uses ``default_scheme``)

        Returns ``None`` for malformed input. Tolerates trailing whitespace,
        ``socks5h`` (DNS-through-proxy variant — normalised to ``socks5``).
        """
        if not raw:
            return None
        s = raw.strip()
        # Strip inline comments / extra whitespace tokens common in free lists.
        if " " in s:
            s = s.split()[0]
        m = _PROXY_LINE_RE.match(s)
        if not m:
            return None
        scheme = (m.group("scheme") or default_scheme or "http").lower()
        # Normalise socks5h → socks5 (CloakBrowser/Playwright don't expose
        # the "h" variant; the resolver behaviour is fine anyway).
        if scheme == "socks5h":
            scheme = "socks5"
        try:
            port = int(m.group("port"))
        except (TypeError, ValueError):
            return None
        if not (0 < port < 65536):
            return None
        # Inputs may already be percent-encoded (free-list rows often are).
        # Store the *decoded* username/password so ``to_playwright_dict``
        # passes the raw value to the browser, and ``url`` re-encodes
        # exactly once on emit.
        return cls(
            scheme=scheme,
            host=m.group("host"),
            port=port,
            username=urllib.parse.unquote(m.group("user") or ""),
            password=urllib.parse.unquote(m.group("pass") or ""),
            source=source,
        )

    def to_json(self) -> dict:
        d = asdict(self)
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Proxy":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------- pool

class ProxyPool:
    """A list of proxies + rotation + health bookkeeping.

    Strategies:
      * ``"random"`` (default) — pick uniformly among the healthy set.
      * ``"round-robin"`` — cycle in order, skipping ones marked failed.
      * ``"sticky"`` — return the same proxy until it fails once, then move on.
        Useful when your target maintains session cookies tied to source IP.

    Thread-safe: ``next()`` / ``mark_ok()`` / ``mark_fail()`` use a
    :class:`threading.Lock` so multiple worker threads can share one pool.
    """

    def __init__(
        self,
        proxies: list[Proxy] | None = None,
        *,
        strategy: str = "random",
        only_healthy: bool = False,
    ):
        self._lock = threading.Lock()
        self.all: list[Proxy] = list(proxies or [])
        self.strategy = strategy
        self.only_healthy = only_healthy
        self._idx = 0
        self._sticky: Proxy | None = None

    def __len__(self) -> int:
        return len(self.all)

    def add(self, p: Proxy) -> None:
        with self._lock:
            # de-dupe on (scheme, host, port)
            key = (p.scheme, p.host, p.port)
            for existing in self.all:
                if (existing.scheme, existing.host, existing.port) == key:
                    return
            self.all.append(p)

    def add_many(self, proxies: list[Proxy]) -> int:
        before = len(self.all)
        for p in proxies:
            self.add(p)
        return len(self.all) - before

    # ------------------------------ load / save / fetch

    def load_from_lines(
        self, lines, *, default_scheme: str = "http", source: str = "",
    ) -> int:
        """Parse text lines (one proxy per line) and add to pool. Returns
        the number of new proxies added."""
        added = 0
        for line in lines:
            p = Proxy.from_url(line, default_scheme=default_scheme, source=source)
            if p is None:
                continue
            before = len(self.all)
            self.add(p)
            if len(self.all) > before:
                added += 1
        return added

    def load_from_file(self, path: str, *, default_scheme: str = "http") -> int:
        """Load proxies from a plain-text file (one URL or host:port per line)."""
        path = str(path)
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        return self.load_from_lines(
            lines, default_scheme=default_scheme,
            source=f"file:{os.path.basename(path)}",
        )

    def fetch_from_github(
        self, source_or_bundle: str, *,
        timeout: float = 20.0,
        limit: int | None = None,
    ) -> int:
        """Fetch a free-proxy list from GitHub and add to pool. Returns
        the number of new proxies added.

        ``source_or_bundle`` may be a single key from ``GITHUB_SOURCES``
        (e.g. ``"proxifly_socks5"``) or a bundle name from
        ``SOURCE_BUNDLES`` (e.g. ``"all"`` / ``"socks"``).
        """
        if source_or_bundle in SOURCE_BUNDLES:
            sources = SOURCE_BUNDLES[source_or_bundle]
        else:
            sources = [source_or_bundle]

        added = 0
        for s in sources:
            entry = GITHUB_SOURCES.get(s)
            if not entry:
                log.warning("[proxy] unknown source %r — skipping", s)
                continue
            url, default_scheme = entry
            try:
                req = urllib.request.Request(
                    url,
                    headers={"User-Agent": "AgentSearch/1.0 proxy-fetcher"},
                )
                with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as resp:
                    body = resp.read().decode("utf-8", errors="replace")
            except Exception as e:
                log.warning("[proxy] fetch %s failed: %s", s, e)
                continue
            lines = body.splitlines()
            if limit is not None:
                lines = lines[:limit]
            n = self.load_from_lines(
                lines,
                default_scheme=default_scheme or "http",
                source=s,
            )
            log.info("[proxy] %s -> +%d (of %d lines)", s, n, len(lines))
            added += n
        return added

    def save(self, path: str | None = None) -> str:
        """Persist all proxies (with their health metadata) to JSON.

        Returns the path written. Defaults to
        ``~/.cache/agentsearch/proxies.json``.
        """
        target = Path(path) if path else DEFAULT_CACHE_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "saved_at": time.time(),
            "strategy": self.strategy,
            "proxies": [p.to_json() for p in self.all],
        }
        with open(target, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return str(target)

    @classmethod
    def load_from_cache(
        cls, path: str | None = None, *,
        only_healthy: bool = False,
    ) -> "ProxyPool":
        """Load a cached pool. Returns an empty pool when the file
        doesn't exist (so first-time CLI calls don't crash)."""
        target = Path(path) if path else DEFAULT_CACHE_FILE
        if not target.exists():
            return cls(only_healthy=only_healthy)
        try:
            with open(target, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            log.warning("[proxy] cache load failed: %s", e)
            return cls(only_healthy=only_healthy)
        proxies = [Proxy.from_json(p) for p in data.get("proxies") or []]
        pool = cls(
            proxies,
            strategy=data.get("strategy") or "random",
            only_healthy=only_healthy,
        )
        return pool

    # ------------------------------ health

    def test(
        self,
        p: Proxy,
        *,
        target_url: str = "https://api.ipify.org?format=text",
        timeout: float = 10.0,
    ) -> bool:
        """Make a single request through this proxy and update health.

        Uses urllib + a ProxyHandler. Note: urllib only knows HTTP / HTTPS
        proxies natively; SOCKS proxies need ``PySocks`` if we wanted to
        verify them out-of-band. For SOCKS we still mark the proxy as
        "untested" (success_count and fail_count both 0) and let the
        actual browser/page driver do the verification when used.
        """
        if p.scheme.startswith("socks"):
            # Browser-level test only — handled by caller. Stay neutral.
            return False

        handler_url = p.url
        proxy_handler = urllib.request.ProxyHandler({
            "http": handler_url,
            "https": handler_url,
        })
        opener = urllib.request.build_opener(proxy_handler)
        opener.addheaders = [
            ("User-Agent", "AgentSearch/1.0 proxy-test"),
        ]
        started = time.time()
        try:
            resp = opener.open(target_url, timeout=timeout)
            data = resp.read(4096)  # cap read
            ok = bool(data) and resp.status == 200
        except Exception as e:
            with self._lock:
                p.fail_count += 1
                p.last_err = f"{type(e).__name__}: {str(e)[:80]}"
            return False
        latency = (time.time() - started) * 1000
        with self._lock:
            if ok:
                p.success_count += 1
                p.last_ok_at = time.time()
                p.latency_ms = latency
                p.last_err = ""
            else:
                p.fail_count += 1
                p.last_err = f"HTTP {resp.status}"
        return ok

    def test_all(
        self,
        *,
        max_workers: int = 30,
        target_url: str = "https://api.ipify.org?format=text",
        timeout: float = 10.0,
        scheme_filter: str | None = None,
        max_test: int | None = None,
    ) -> dict[str, int]:
        """Test all proxies in parallel. Returns a summary
        ``{ok: N, fail: N, skipped: N}`` count."""
        targets = self.all
        if scheme_filter:
            targets = [p for p in targets if p.scheme == scheme_filter]
        if max_test is not None:
            targets = targets[:max_test]
        ok = 0
        fail = 0
        skipped = 0
        if not targets:
            return {"ok": 0, "fail": 0, "skipped": 0}
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {
                ex.submit(self.test, p, target_url=target_url, timeout=timeout): p
                for p in targets
            }
            for f in as_completed(futs):
                p = futs[f]
                try:
                    res = f.result()
                except Exception:
                    fail += 1
                    continue
                if p.scheme.startswith("socks"):
                    skipped += 1
                elif res:
                    ok += 1
                else:
                    fail += 1
        return {"ok": ok, "fail": fail, "skipped": skipped}

    # ------------------------------ pick

    def healthy(self) -> list[Proxy]:
        """Return proxies with a positive success rate.

        SOCKS proxies (which we don't auto-test via urllib) count as
        "neutral" — they're returned only when nothing else is healthy
        and ``only_healthy`` is False.
        """
        return [
            p for p in self.all
            if p.success_count > 0 and p.fail_count <= p.success_count * 2
        ]

    def candidates(self) -> list[Proxy]:
        if self.only_healthy:
            return self.healthy()
        h = self.healthy()
        if h:
            return h
        return list(self.all)

    def next(self) -> Proxy | None:
        """Pick the next proxy according to the configured strategy."""
        with self._lock:
            cand = self.candidates()
            if not cand:
                return None
            if self.strategy == "random":
                return random.choice(cand)
            if self.strategy == "round-robin":
                p = cand[self._idx % len(cand)]
                self._idx = (self._idx + 1) % len(cand)
                return p
            if self.strategy == "sticky":
                if self._sticky and self._sticky in cand:
                    return self._sticky
                self._sticky = random.choice(cand)
                return self._sticky
            # Unknown strategy → fall back to random.
            return random.choice(cand)

    def mark_ok(self, p: Proxy, latency_ms: float | None = None) -> None:
        with self._lock:
            p.success_count += 1
            p.last_ok_at = time.time()
            if latency_ms is not None:
                p.latency_ms = latency_ms
            p.last_err = ""

    def mark_fail(self, p: Proxy, err: str = "") -> None:
        with self._lock:
            p.fail_count += 1
            if err:
                p.last_err = err[:120]
            # Sticky strategy: a failure makes us drop this proxy and
            # pick a fresh one on the next next().
            if self.strategy == "sticky" and self._sticky is p:
                self._sticky = None

    def stats(self) -> dict:
        """Aggregate counts useful for the ``proxy stats`` CLI."""
        by_scheme: dict[str, int] = {}
        healthy = 0
        for p in self.all:
            by_scheme[p.scheme] = by_scheme.get(p.scheme, 0) + 1
            if p.success_count > 0:
                healthy += 1
        return {
            "total": len(self.all),
            "healthy": healthy,
            "by_scheme": by_scheme,
        }


# ---------------------------------------------------------------- CLI helpers


def resolve_proxy_spec(spec: str | None):
    """Convert a CLI ``--proxy`` value to ``(url, pool)``.

    Returns one of:
      * ``(None, None)`` — no proxy requested.
      * ``(url, None)`` — a single static proxy URL.
      * ``(None, pool)`` — a :class:`ProxyPool` to rotate from.

    Spec forms accepted::

        ""                           # no proxy
        "http://1.2.3.4:8080"        # static URL
        "socks5://u:p@1.2.3.4:1080"  # static URL with auth
        "1.2.3.4:8080"               # bare host:port (assumes http)
        "pool"                       # use ~/.cache/agentsearch/proxies.json
        "pool:socks5"                # filter the cache to socks5 schemes
        "pool:http"                  # filter to http+https
        "pool:/abs/path/to/list.json"   # custom cache file
        "file:/path/to/proxies.txt"  # plain-text list, one per line

    Hand the resulting ``url`` to :class:`BrowserConfig.proxy` for static
    use, or attach the ``pool`` to :class:`BrowserConfig.proxy_pool` for
    rotation.
    """
    if not spec:
        return (None, None)
    s = spec.strip()
    if not s:
        return (None, None)

    # ---------- pool:* (rotate from cached / filtered list)
    if s == "pool" or s.startswith("pool:"):
        rest = s[5:] if s.startswith("pool:") else ""
        # ``pool:<absolute path>`` → load that JSON cache
        if rest.startswith("/") or rest.startswith("~"):
            cache_path = os.path.expanduser(rest)
            pool = ProxyPool.load_from_cache(cache_path)
        else:
            pool = ProxyPool.load_from_cache()
            if rest:
                # Treat ``rest`` as a scheme filter ("socks5", "socks", "http").
                want: set[str]
                if rest == "socks":
                    want = {"socks4", "socks5"}
                elif rest == "http":
                    want = {"http", "https"}
                else:
                    want = {rest}
                pool.all = [p for p in pool.all if p.scheme in want]
        if len(pool) == 0:
            log.warning(
                "[proxy] pool spec %r resolved to 0 proxies — "
                "did you run `agentsearch proxies fetch` first?",
                spec,
            )
        return (None, pool)

    # ---------- file:/path/to/list.txt (plain text, one per line)
    if s.startswith("file:"):
        pool = ProxyPool()
        pool.load_from_file(s[5:])
        return (None, pool)

    # ---------- bare URL or host:port
    p = Proxy.from_url(s)
    if p is None:
        raise ValueError(f"invalid --proxy value: {spec!r}")
    return (p.url, None)


def apply_proxy_spec_to_config(cfg, spec: str | None) -> None:
    """Resolve ``spec`` and mutate ``cfg.proxy`` / ``cfg.proxy_pool``.

    Convenience for CLI / agent code paths that build a
    :class:`agent_search.core.BrowserConfig`.
    """
    url, pool = resolve_proxy_spec(spec)
    if url is not None:
        cfg.proxy = url
    if pool is not None:
        cfg.proxy_pool = pool
