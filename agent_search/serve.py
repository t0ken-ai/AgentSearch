"""HTTP server wrapping AgentSearch's search / extract / list_engines.

Companion to the MCP server. Use this when the agent runs in a context
where MCP isn't available (cloud workers, Docker containers, scripts on
remote machines, custom HTTP-only frameworks, ...). The host machine
runs CloakBrowser; remote callers send JSON, get JSON back.

Endpoints
---------

- ``POST /search``        — body: ``{query, engine?, limit?, depth?, profile?}``
- ``POST /search-many``   — body: ``{query, engines, limit?, timeout?}``
- ``POST /extract``       — body: ``{url, paginate?, max_scrolls?, links?, images?}``
- ``GET  /list-engines``  — list every engine
- ``GET  /health``        — liveness probe (``{"status": "ok"}``)

All responses are JSON.

Run locally::

    python -m agent_search.serve --host 127.0.0.1 --port 8088

Optional auth: set ``--token <secret>`` (or env ``AGENTSEARCH_TOKEN``).
Callers must then pass ``Authorization: Bearer <secret>`` on every
request. No token = open server (fine for localhost / VPN, refuse to
bind 0.0.0.0 without a token).

Stay self-hosted: this is intentionally NOT a managed SaaS surface.
The recommended deployment is on a single machine inside the team's
own network or behind their own reverse proxy.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import urlparse

from .cli import _engine_registry, _get_engine, _resolve_profile_dir
from .core import BrowserConfig, launch, new_page
from .extract import extract_page
from .multi import search_many

log = logging.getLogger(__name__)

# Shared browser pool — same idea as the MCP server: keep a single
# CloakBrowser alive and recycle every N requests so we don't pay
# Chromium startup per HTTP call.
RECYCLE_AFTER = int(os.environ.get("AGENTSEARCH_RECYCLE_AFTER", "25"))


def _resolve_default_proxy() -> str | None:
    """Pick a proxy URL from the environment, in priority order.

    Priority:
        1. AGENTSEARCH_PROXY    explicit per-deployment override
        2. FLUXISP_PROXY        the residential pool the project ships with
        3. HTTPS_PROXY          standard Linux convention
        4. HTTP_PROXY           ditto
    Returns ``None`` when nothing is set so the browser launches direct.
    """
    for var in ("AGENTSEARCH_PROXY", "FLUXISP_PROXY", "HTTPS_PROXY", "HTTP_PROXY"):
        v = os.environ.get(var)
        if v:
            return v
    return None


class BrowserPool:
    def __init__(self) -> None:
        self._browser = None
        self._calls = 0
        self._lock = threading.Lock()

    def page(self, user_data_dir: str | None = None):
        with self._lock:
            # When a profile is requested we always launch a fresh
            # browser tied to that user_data_dir — different profiles
            # cannot share a Browser, since user_data_dir is per-launch.
            if user_data_dir or self._calls >= RECYCLE_AFTER or self._browser is None:
                if self._browser is not None:
                    try:
                        self._browser.close()
                    except Exception:
                        pass
                self._browser = launch(BrowserConfig(
                    headless=True,
                    humanize=True,
                    user_data_dir=user_data_dir,
                    proxy=_resolve_default_proxy(),
                ))
                self._calls = 0
            self._calls += 1
            return new_page(self._browser)

    def shutdown(self) -> None:
        with self._lock:
            if self._browser is not None:
                try:
                    self._browser.close()
                except Exception:
                    pass
                self._browser = None


_pool = BrowserPool()


# ----------------------------------------------------------------- handler


class Handler(BaseHTTPRequestHandler):
    server_version = "AgentSearch/1.0"

    # --------------------------------------------------------------- helpers

    def _auth_ok(self) -> bool:
        expected = self.server.token  # type: ignore[attr-defined]
        if not expected:
            return True
        got = self.headers.get("Authorization", "")
        if got.startswith("Bearer ") and got[7:] == expected:
            return True
        return False

    def _read_json(self) -> dict[str, Any]:
        n = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(n) if n > 0 else b""
        if not raw:
            return {}
        try:
            obj = json.loads(raw.decode("utf-8"))
            return obj if isinstance(obj, dict) else {}
        except Exception:
            return {}

    def _reply(self, code: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):  # noqa: A003 — http.server contract
        log.info("%s - %s", self.address_string(), fmt % args)

    # ------------------------------------------------------------- dispatch

    def do_GET(self):  # noqa: N802 — http.server contract
        if not self._auth_ok():
            return self._reply(401, {"error": "unauthorized"})
        path = urlparse(self.path).path
        if path == "/health":
            return self._reply(200, {"status": "ok"})
        if path == "/list-engines":
            reg = _engine_registry()
            return self._reply(200, {
                "count": len(set(reg.values())),
                "engines": sorted(reg.keys()),
            })
        return self._reply(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if not self._auth_ok():
            return self._reply(401, {"error": "unauthorized"})
        path = urlparse(self.path).path
        body = self._read_json()
        if path == "/search":
            return self._reply(*self._do_search(body))
        if path == "/search-many":
            return self._reply(*self._do_search_many(body))
        if path == "/extract":
            return self._reply(*self._do_extract(body))
        return self._reply(404, {"error": "not found"})

    # ------------------------------------------------------------- handlers

    def _do_search(self, body: dict[str, Any]) -> tuple[int, Any]:
        query = body.get("query") or ""
        if not query:
            return 400, {"error": "missing 'query'"}
        engine = body.get("engine") or "duckduckgo"
        limit = max(1, min(int(body.get("limit") or 10), 50))
        depth = max(0, min(int(body.get("depth") or 0), limit))
        profile = body.get("profile")

        try:
            engine_cls = _get_engine(engine)
        except ValueError as e:
            return 400, {"error": str(e)}

        page = _pool.page(user_data_dir=_resolve_profile_dir(profile))
        try:
            instance = engine_cls(page)
            raw = instance.search(query, limit=limit) or []
            if depth > 0:
                for r in raw[:depth]:
                    if not getattr(r, "url", None):
                        continue
                    ep = _pool.page()
                    try:
                        rec = extract_page(ep, url=r.url, paginate=True, max_scrolls=2,
                                           include_links=False, include_images=False)
                        r.__dict__["body_markdown"] = rec.get("content_markdown") or ""
                        r.__dict__["body_word_count"] = rec.get("word_count") or 0
                    except Exception as e:
                        r.__dict__["body_error"] = f"{type(e).__name__}: {e}"
                    finally:
                        try:
                            ep.close()
                        except Exception:
                            pass
        except Exception as e:
            log.exception("search failed")
            return 500, {"error": f"{type(e).__name__}: {e}"}
        finally:
            try:
                page.close()
            except Exception:
                pass

        return 200, {
            "engine": engine,
            "query": query,
            "count": len(raw),
            "results": [r.__dict__ for r in raw],
        }

    def _do_search_many(self, body: dict[str, Any]) -> tuple[int, Any]:
        query = body.get("query") or ""
        engines = body.get("engines") or []
        if not query:
            return 400, {"error": "missing 'query'"}
        if not engines or not isinstance(engines, list):
            return 400, {"error": "engines must be a non-empty list"}
        limit = max(1, min(int(body.get("limit") or 5), 25))
        timeout = max(10, min(int(body.get("timeout") or 90), 300))
        try:
            out = search_many(query, engines, limit=limit, timeout_s=timeout)
        except Exception as e:
            return 500, {"error": f"{type(e).__name__}: {e}"}
        return 200, out

    def _do_extract(self, body: dict[str, Any]) -> tuple[int, Any]:
        url = body.get("url") or ""
        if not url:
            return 400, {"error": "missing 'url'"}
        paginate = bool(body.get("paginate", True))
        max_scrolls = max(0, min(int(body.get("max_scrolls") or 3), 10))
        links = bool(body.get("links", False))
        images = bool(body.get("images", False))
        profile = body.get("profile")

        page = _pool.page(user_data_dir=_resolve_profile_dir(profile))
        try:
            rec = extract_page(page, url=url, paginate=paginate, max_scrolls=max_scrolls,
                               include_links=links, include_images=images)
        except Exception as e:
            return 500, {"error": f"{type(e).__name__}: {e}"}
        finally:
            try:
                page.close()
            except Exception:
                pass
        return 200, rec


# ------------------------------------------------------------------- main


class TokenServer(HTTPServer):
    """Single-threaded HTTPServer that carries an optional bearer token.

    We deliberately don't use ThreadingHTTPServer: CloakBrowser uses
    Playwright's *sync* API, which binds each browser instance to the
    launching thread. Multiple HTTP threads sharing one Browser would
    cross-thread the Playwright connection and crash. Self-hosted
    AgentSearch is a single-user service in practice — serial request
    handling is fine and keeps the implementation simple.
    """

    def __init__(self, *args, token: str | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.token = token


def main() -> int:
    ap = argparse.ArgumentParser(prog="agent_search.serve", description="Self-hosted HTTP API for AgentSearch")
    ap.add_argument("--host", default="127.0.0.1", help="Bind host (default 127.0.0.1)")
    ap.add_argument("--port", type=int, default=8088, help="Bind port (default 8088)")
    ap.add_argument("--token", default=os.environ.get("AGENTSEARCH_TOKEN") or None,
                    help="Bearer token (env AGENTSEARCH_TOKEN). Required when binding 0.0.0.0.")
    args = ap.parse_args()

    if args.host == "0.0.0.0" and not args.token:
        print(
            "Refusing to bind 0.0.0.0 without --token / AGENTSEARCH_TOKEN — "
            "anyone on the network would be able to drive your CloakBrowser. "
            "Pass --token <secret> to enable.",
            file=sys.stderr,
        )
        return 2

    logging.basicConfig(
        level=os.environ.get("AGENTSEARCH_LOG", "INFO"),
        format="%(asctime)s %(levelname)s: %(message)s",
    )

    server = TokenServer((args.host, args.port), Handler, token=args.token)
    print(f"🚀 AgentSearch HTTP API on http://{args.host}:{args.port}")
    print(f"   POST /search · POST /search-many · POST /extract · GET /list-engines · GET /health")
    if args.token:
        print(f"   Auth: Bearer token required (set via --token / env)")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 shutting down")
    finally:
        try:
            server.server_close()
        except Exception:
            pass
        _pool.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
