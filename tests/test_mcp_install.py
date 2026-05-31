"""Step 1: prove the registered MCP server actually starts.

Spawns the exact command from ~/.kiro/settings/mcp.json, does a
JSON-RPC initialize + tools/list handshake over stdio, prints the
advertised tool count and names, and shuts down cleanly. No browser
work — this is purely a handshake test.

Run:
    /Users/gao/tools/cloakbrowser/venv/bin/python tests/test_mcp_install.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time


def _send(proc, msg):
    raw = json.dumps(msg) + "\n"
    proc.stdin.write(raw.encode())
    proc.stdin.flush()


def _recv(proc, want_id, timeout_s=15.0):
    """Read line-delimited JSON-RPC until we see a response for want_id."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            continue
        try:
            obj = json.loads(line.decode())
        except Exception:
            continue
        if obj.get("id") == want_id:
            return obj
    raise TimeoutError(f"no response for id={want_id} in {timeout_s}s")


def main():
    cmd = [
        "/Users/gao/tools/cloakbrowser/venv/bin/python",
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
        stderr=subprocess.PIPE, env=env,
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
            # New
            "search_many", "engine_status", "screenshot",
            "download_files", "summarise_news", "ads_batch",
        }
        missing = expected - set(names)
        extra = set(names) - expected
        if missing:
            print(f"\n  FAIL: missing tools: {sorted(missing)}")
            return 1
        if extra:
            print(f"\n  note: extra tools (not failure): {sorted(extra)}")
        if len(names) != 15:
            print(f"\n  FAIL: expected 15 tools, got {len(names)}")
            return 1
        print(f"\n  PASS — all 15 tools advertised")
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
