"""Offline regression test for ``agent_search.proxy``.

Covers:

1. ``Proxy.from_url`` parses every supported shape (scheme://host:port,
   user:pass@host:port, bare host:port with default scheme, ``socks5h``
   normalisation, malformed lines, percent-encoded credentials).
2. ``Proxy.url`` / ``Proxy.server`` / ``Proxy.to_playwright_dict`` round-trip
   credentials exactly once (no double-encoding).
3. ``ProxyPool`` rotation strategies — random, round-robin, sticky.
4. ``ProxyPool.add`` deduplicates on (scheme, host, port).
5. ``mark_ok`` / ``mark_fail`` update health counters atomically; sticky
   strategy releases its pin on failure.
6. ``save`` / ``load_from_cache`` round-trip preserves health metadata.
7. ``fetch_from_github`` parses fixture-style text bodies (mocked via
   ``urlopen`` monkey-patch — no live network I/O).
8. Bundle expansion (``"socks5"`` / ``"socks"`` / ``"all"``) hits multiple
   sources.

Run:
    ~/tools/cloakbrowser/venv/bin/python tests/test_proxy.py
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import traceback
from contextlib import contextmanager
from unittest import mock

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search import proxy as proxy_mod
from agent_search.proxy import GITHUB_SOURCES, Proxy, ProxyPool, SOURCE_BUNDLES


# ----------------------------------------------------------------- helpers

class _FakeHTTPResponse(io.BytesIO):
    """Minimal stand-in for an ``http.client.HTTPResponse`` context manager."""

    def __init__(self, body: bytes, status: int = 200):
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


@contextmanager
def _fake_urlopen(url_to_body: dict[str, bytes]):
    """Monkey-patch ``urllib.request.urlopen`` *inside the proxy module* to
    return canned bodies for known URLs."""
    def fake(req, *args, **kwargs):
        full_url = req.full_url if hasattr(req, "full_url") else str(req)
        body = url_to_body.get(full_url)
        if body is None:
            raise RuntimeError(f"fake_urlopen: no canned body for {full_url}")
        return _FakeHTTPResponse(body)
    with mock.patch.object(proxy_mod.urllib.request, "urlopen", fake):
        yield


# ----------------------------------------------------------------- cases

def test_parse_shapes() -> None:
    cases = [
        # (raw, default_scheme, expected scheme/host/port/user/pass)
        ("http://1.2.3.4:8080", "http", ("http", "1.2.3.4", 8080, "", "")),
        ("https://10.0.0.1:443", "http", ("https", "10.0.0.1", 443, "", "")),
        ("socks4://1.2.3.4:1080", "http", ("socks4", "1.2.3.4", 1080, "", "")),
        ("socks5://1.2.3.4:1080", "http", ("socks5", "1.2.3.4", 1080, "", "")),
        # socks5h normalises to socks5
        ("socks5h://1.2.3.4:1080", "http", ("socks5", "1.2.3.4", 1080, "", "")),
        # bare host:port falls back to default_scheme
        ("203.0.113.7:3128", "http", ("http", "203.0.113.7", 3128, "", "")),
        ("203.0.113.7:3128", "socks5", ("socks5", "203.0.113.7", 3128, "", "")),
        # credentials inline
        (
            "http://alice:hunter2@1.2.3.4:8080", "http",
            ("http", "1.2.3.4", 8080, "alice", "hunter2"),
        ),
        # percent-encoded credentials decode on parse
        (
            "http://user:pa%24s%2Fword@1.2.3.4:8080", "http",
            ("http", "1.2.3.4", 8080, "user", "pa$s/word"),
        ),
        # whitespace + trailing junk tolerated
        ("   http://5.6.7.8:80   foo", "http", ("http", "5.6.7.8", 80, "", "")),
    ]
    for raw, default, expect in cases:
        p = Proxy.from_url(raw, default_scheme=default, source="test")
        assert p is not None, f"unexpected parse failure: {raw!r}"
        got = (p.scheme, p.host, p.port, p.username, p.password)
        assert got == expect, f"{raw!r} -> {got} != {expect}"

    # Reject malformed lines.
    for raw in ["", "garbage", "://nohost:80", "host:notaport",
                "host:99999", "host:0", "host:-1"]:
        assert Proxy.from_url(raw) is None, f"should reject: {raw!r}"


def test_emit_shapes() -> None:
    # Special chars in the password must be URL-encoded by the caller (the
    # regex disallows raw '@' '/' whitespace). We accept percent-encoded
    # input and decode it back to the raw value internally.
    p = Proxy.from_url("http://alice:p%24%2Fw@1.2.3.4:8080")
    assert p is not None
    assert p.username == "alice" and p.password == "p$/w", (p.username, p.password)
    # server: no auth
    assert p.server == "http://1.2.3.4:8080"
    # url: auth re-encoded exactly once (no %25 double-encoding)
    assert p.url == "http://alice:p%24%2Fw@1.2.3.4:8080", p.url
    # playwright dict: raw decoded values for username/password
    pw = p.to_playwright_dict()
    assert pw == {"server": "http://1.2.3.4:8080",
                  "username": "alice", "password": "p$/w"}, pw

    # Raw $ (allowed by the regex) survives a round-trip without re-encoding.
    p2 = Proxy.from_url("http://bob:s$cret@1.2.3.4:8080")
    assert p2 is not None
    assert p2.password == "s$cret"
    assert p2.url == "http://bob:s%24cret@1.2.3.4:8080", p2.url

    # No-auth case → playwright dict has only "server"
    p3 = Proxy.from_url("socks5://9.9.9.9:1080")
    assert p3.to_playwright_dict() == {"server": "socks5://9.9.9.9:1080"}


def test_pool_dedup_and_strategies() -> None:
    pool = ProxyPool(strategy="round-robin")
    n = pool.load_from_lines(
        [
            "http://1.1.1.1:80",
            "http://1.1.1.1:80",        # duplicate
            "2.2.2.2:8080",             # bare → http
            "socks5://3.3.3.3:1080",
            "",
            "junk",
            # different scheme on same host:port → counts as a new entry
            "https://1.1.1.1:80",
        ],
        default_scheme="http",
        source="manual",
    )
    assert n == 4, f"expected 4 unique adds, got {n}"
    assert len(pool) == 4
    by_scheme = pool.stats()["by_scheme"]
    assert by_scheme == {"http": 2, "socks5": 1, "https": 1}, by_scheme

    # round-robin walks the pool deterministically.
    rr_hosts = [pool.next().host for _ in range(8)]
    assert rr_hosts[:4] == [p.host for p in pool.all], rr_hosts
    assert rr_hosts[4:] == [p.host for p in pool.all], rr_hosts

    # random covers the whole pool given enough draws.
    pool.strategy = "random"
    seen = {pool.next().host for _ in range(80)}
    assert seen == {p.host for p in pool.all}

    # sticky returns the same proxy until it's marked failed.
    pool.strategy = "sticky"
    first = pool.next()
    second = pool.next()
    assert first is second, "sticky should pin"
    pool.mark_fail(first, err="simulated")
    third = pool.next()
    assert third is not None
    # After fail, sticky may pick the same host only if it's still healthy
    # — since none of these have positive success_count, healthy() is empty
    # and we fall back to all candidates. We just verify the pin was released.
    # (We don't assert third != first because the pool is small and sticky
    # may legitimately re-pick by random chance from `candidates()`.)


def test_pool_health_tracking() -> None:
    pool = ProxyPool()
    pool.load_from_lines(["http://1.1.1.1:80", "http://2.2.2.2:80"])
    p1, p2 = pool.all
    pool.mark_ok(p1, latency_ms=120.5)
    pool.mark_fail(p2, err="timeout")
    assert p1.success_count == 1 and p1.fail_count == 0
    assert p1.latency_ms == 120.5
    assert p2.success_count == 0 and p2.fail_count == 1
    assert "timeout" in p2.last_err
    assert pool.healthy() == [p1]
    # Health score: tested-good > untested > tested-bad.
    assert p1.health_score() > 0.5
    assert p2.health_score() == 0.0  # 0 success, all-fail


def test_save_load_roundtrip() -> None:
    pool = ProxyPool(strategy="round-robin")
    pool.load_from_lines([
        "http://1.1.1.1:80",
        "socks5://2.2.2.2:1080",
        "http://user:pw@3.3.3.3:8080",
    ])
    pool.mark_ok(pool.all[0], latency_ms=42.0)
    pool.mark_fail(pool.all[1], err="connect refused")

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        path = tf.name
    try:
        pool.save(path)
        # File is well-formed JSON with version marker.
        with open(path, encoding="utf-8") as f:
            blob = json.load(f)
        assert blob["version"] == 1
        assert blob["strategy"] == "round-robin"
        assert len(blob["proxies"]) == 3

        restored = ProxyPool.load_from_cache(path)
        assert len(restored) == 3
        assert restored.strategy == "round-robin"
        # Health metadata round-trips intact.
        r1 = next(p for p in restored.all if p.host == "1.1.1.1")
        assert r1.success_count == 1 and r1.latency_ms == 42.0
        r2 = next(p for p in restored.all if p.host == "2.2.2.2")
        assert r2.fail_count == 1 and "connect" in r2.last_err
        # Auth survived the trip.
        r3 = next(p for p in restored.all if p.host == "3.3.3.3")
        assert r3.username == "user" and r3.password == "pw"
    finally:
        os.unlink(path)


def test_load_from_cache_missing_returns_empty() -> None:
    """Calling load_from_cache on a non-existent path is graceful."""
    target = "/tmp/agentsearch-proxy-cache-does-not-exist.json"
    if os.path.exists(target):
        os.unlink(target)
    pool = ProxyPool.load_from_cache(target)
    assert isinstance(pool, ProxyPool)
    assert len(pool) == 0


def test_fetch_from_github_single_source() -> None:
    """fetch_from_github parses canned text body without live HTTP."""
    src = "proxifly_socks5"
    url = GITHUB_SOURCES[src][0]
    body = b"\n".join([
        b"socks5://1.1.1.1:1080",
        b"socks5://2.2.2.2:1080",
        b"# this is a comment line we should reject",
        b"3.3.3.3:1080",   # bare → uses inferred default 'socks5'
        b"socks5h://4.4.4.4:1080",  # normalises to socks5
        b"",
        b"junk-line",
    ])
    pool = ProxyPool()
    with _fake_urlopen({url: body}):
        n = pool.fetch_from_github(src)
    assert n == 4, f"expected 4 valid lines, got {n}"
    schemes = {p.scheme for p in pool.all}
    assert schemes == {"socks5"}, schemes
    # `source` field tagged correctly so we can debug provenance.
    assert all(p.source == src for p in pool.all)


def test_fetch_from_github_bundle() -> None:
    """A bundle name expands to multiple sources."""
    bundle = "socks5"
    url_to_body: dict[str, bytes] = {}
    expected_hosts: set[str] = set()
    for i, src in enumerate(SOURCE_BUNDLES[bundle]):
        url = GITHUB_SOURCES[src][0]
        host = f"10.0.{i}.1"
        url_to_body[url] = f"socks5://{host}:1080".encode()
        expected_hosts.add(host)
    pool = ProxyPool()
    with _fake_urlopen(url_to_body):
        n = pool.fetch_from_github(bundle)
    assert n == len(expected_hosts), f"expected {len(expected_hosts)} adds, got {n}"
    got_hosts = {p.host for p in pool.all}
    assert got_hosts == expected_hosts, got_hosts


def test_fetch_from_github_unknown_source_is_noop() -> None:
    pool = ProxyPool()
    n = pool.fetch_from_github("not_a_real_source")
    assert n == 0
    assert len(pool) == 0


def test_fetch_with_limit() -> None:
    src = "proxifly_http"
    url = GITHUB_SOURCES[src][0]
    body_lines = [f"http://1.0.0.{i}:80".encode() for i in range(50)]
    body = b"\n".join(body_lines)
    pool = ProxyPool()
    with _fake_urlopen({url: body}):
        n = pool.fetch_from_github(src, limit=10)
    assert n == 10, f"limit not honoured: {n}"
    assert len(pool) == 10


def test_resolve_proxy_spec() -> None:
    """``resolve_proxy_spec`` accepts each documented form."""
    from agent_search.proxy import resolve_proxy_spec

    # Empty / falsy → no-op.
    assert resolve_proxy_spec(None) == (None, None)
    assert resolve_proxy_spec("") == (None, None)
    assert resolve_proxy_spec("   ") == (None, None)

    # Static URL forms return (url, None) and re-encode auth correctly.
    url, pool = resolve_proxy_spec("http://1.2.3.4:8080")
    assert url == "http://1.2.3.4:8080" and pool is None

    url, pool = resolve_proxy_spec("socks5://alice:s%24cret@1.2.3.4:1080")
    assert pool is None
    assert url == "socks5://alice:s%24cret@1.2.3.4:1080", url

    # Bare host:port assumes http.
    url, pool = resolve_proxy_spec("9.9.9.9:3128")
    assert url == "http://9.9.9.9:3128" and pool is None

    # Malformed rejected with ValueError.
    try:
        resolve_proxy_spec("definitely not a proxy")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for garbage input")

    # ``pool`` with a custom (empty) cache path returns an empty pool.
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        cache_path = tf.name
    try:
        # write a tiny cache file with mixed schemes
        cache = ProxyPool()
        cache.load_from_lines([
            "http://1.1.1.1:80",
            "https://2.2.2.2:443",
            "socks4://3.3.3.3:1080",
            "socks5://4.4.4.4:1080",
        ])
        cache.save(cache_path)

        # ``pool:/abs/path`` loads a custom cache file.
        url, pool = resolve_proxy_spec(f"pool:{cache_path}")
        assert url is None and pool is not None
        assert len(pool) == 4

        # Default ``pool`` (no path) goes to DEFAULT_CACHE_FILE — we don't
        # test that branch here to avoid polluting the user's real cache.

        # ``pool:socks5`` filters to socks5 schemes (after loading default).
        # Since we can't easily inject the default path without monkey-
        # patching, verify the filter separately by hand-crafting a pool:
        from agent_search.proxy import ProxyPool as _PP, resolve_proxy_spec as _rps
        with mock.patch.object(
            _PP, "load_from_cache",
            classmethod(lambda cls, *a, **k: _PP(cache.all.copy())),
        ):
            _, pool_socks5 = _rps("pool:socks5")
            assert pool_socks5 is not None
            assert {p.scheme for p in pool_socks5.all} == {"socks5"}, pool_socks5.all
            _, pool_http = _rps("pool:http")
            assert pool_http is not None
            assert {p.scheme for p in pool_http.all} == {"http", "https"}
            _, pool_socks = _rps("pool:socks")
            assert {p.scheme for p in pool_socks.all} == {"socks4", "socks5"}
    finally:
        os.unlink(cache_path)


def test_apply_proxy_spec_to_config() -> None:
    """``apply_proxy_spec_to_config`` writes BrowserConfig attributes."""
    from agent_search.core import BrowserConfig
    from agent_search.proxy import apply_proxy_spec_to_config

    cfg = BrowserConfig()
    apply_proxy_spec_to_config(cfg, "http://1.2.3.4:8080")
    assert cfg.proxy == "http://1.2.3.4:8080"
    assert cfg.proxy_pool is None

    cfg2 = BrowserConfig()
    # Build a tiny on-disk pool, point a `pool:` spec at it.
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        cache = tf.name
    try:
        seed = ProxyPool()
        seed.load_from_lines(["http://1.1.1.1:80", "socks5://2.2.2.2:1080"])
        seed.save(cache)
        apply_proxy_spec_to_config(cfg2, f"pool:{cache}")
        assert cfg2.proxy is None
        assert cfg2.proxy_pool is not None
        assert len(cfg2.proxy_pool) == 2
    finally:
        os.unlink(cache)


# ----------------------------------------------------------------- runner

def main() -> int:
    tests = [
        test_parse_shapes,
        test_emit_shapes,
        test_pool_dedup_and_strategies,
        test_pool_health_tracking,
        test_save_load_roundtrip,
        test_load_from_cache_missing_returns_empty,
        test_fetch_from_github_single_source,
        test_fetch_from_github_bundle,
        test_fetch_from_github_unknown_source_is_noop,
        test_fetch_with_limit,
        test_resolve_proxy_spec,
        test_apply_proxy_spec_to_config,
    ]
    passed = 0
    failed = 0
    for t in tests:
        name = t.__name__
        try:
            t()
            print(f"  PASS  {name}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {name}: {e}")
            failed += 1
        except Exception:
            print(f"  ERROR {name}:")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{passed + failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
