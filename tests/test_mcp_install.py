"""Prove the installed MCP server starts and advertises its public tools.

Spawns the current interpreter, performs a JSON-RPC initialize + tools/list
handshake over stdio, prints the advertised names, and shuts down cleanly.
No browser work is performed, so this remains suitable for offline CI.

Run:
    python tests/test_mcp_install.py
"""
from __future__ import annotations

import json
import os
import selectors
import subprocess
import sys
import time
from pathlib import Path

# Anchor both the assertion and child process to the checkout containing this
# test. Otherwise an older globally installed ``agent_search`` can be imported
# when the script is launched by absolute path from another working directory.
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
from agent_search import __version__


def _send(proc, msg):
    raw = json.dumps(msg) + "\n"
    proc.stdin.write(raw.encode())
    proc.stdin.flush()


def _recv(proc, want_id, timeout_s=15.0):
    """Read line-delimited JSON-RPC until we see a response for want_id."""
    deadline = time.monotonic() + timeout_s
    selector = selectors.DefaultSelector()
    selector.register(proc.stdout, selectors.EVENT_READ)
    try:
        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if not selector.select(timeout=max(0.0, remaining)):
                break
            line = proc.stdout.readline()
            if not line:
                if proc.poll() is not None:
                    raise RuntimeError(
                        f"MCP server exited with code {proc.returncode} "
                        f"while waiting for id={want_id}"
                    )
                continue
            try:
                obj = json.loads(line.decode())
            except Exception:
                continue
            if obj.get("id") == want_id:
                return obj
    finally:
        selector.close()
    raise TimeoutError(f"no response for id={want_id} in {timeout_s}s")


def main():
    cmd = [
        sys.executable,
        "-m", "agent_search.mcp_server",
    ]
    env = {
        **os.environ,
        "AGENTSEARCH_HEADLESS": "1",
        "AGENTSEARCH_LOG": "WARNING",
    }
    print(f"spawning: {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=env, cwd=REPO_ROOT,
    )

    try:
        # 1. initialize
        _send(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "install-smoke", "version": "0.1"},
            },
        })
        init = _recv(proc, 1)
        if "error" in init:
            print(f"FAIL initialize: {init['error']}")
            return 1
        info = init.get("result", {}).get("serverInfo", {})
        print(f"  server: {info.get('name')!r} v{info.get('version')!r}")
        if info.get("version") != __version__:
            print(
                f"FAIL server version: expected {__version__!r}, "
                f"got {info.get('version')!r}"
            )
            return 1

        # 2. notifications/initialized
        _send(proc, {
            "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
        })

        # 3. tools/list
        _send(proc, {
            "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {},
        })
        listing = _recv(proc, 2)
        if "error" in listing:
            print(f"FAIL tools/list: {listing['error']}")
            return 1

        tools = listing.get("result", {}).get("tools", [])
        names = sorted(t["name"] for t in tools)
        print(f"\n  advertised tools ({len(names)}):")
        for n in names:
            print(f"    - {n}")

        expected = {
            "search", "extract", "extract_many", "list_engines",
            "list_dev_docs_platforms", "search_app", "lookup_app",
            "find_competitor_ads", "download_ad_media",
            "search_many", "engine_status", "screenshot",
            "download_files", "summarise_news", "ads_batch",
            "image_search", "image_search_many", "download_images",
        }
        missing = expected - set(names)
        extra = set(names) - expected
        if missing:
            print(f"\n  FAIL: missing tools: {sorted(missing)}")
            return 1
        if extra:
            print(f"\n  note: extra tools (not failure): {sorted(extra)}")
        print(f"\n  PASS — all {len(expected)} required tools advertised")
        return 0
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
        # Drain stderr for diagnostic
        try:
            err = proc.stderr.read().decode()
            if err.strip():
                print("\n--- server stderr (last 800B) ---")
                print(err[-800:])
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
