"""End-to-end smoke test for the MCP server.

Spawns ``python -m cloak_stealth_suite.mcp_server`` as a subprocess and
talks to it over stdio using the MCP JSON-RPC framing. This is the same
contract Claude Desktop / Cursor / Cline use, so a passing test means
the server is plug-and-play in those clients.
"""

import json
import os
import subprocess
import sys
import time

REQS = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "smoke-test", "version": "0.0.1"},
    }},
    {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
        "name": "list_engines", "arguments": {}
    }},
    {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {
        "name": "search",
        "arguments": {"query": "AgentSearch local stealth", "engine": "duckduckgo", "limit": 3}
    }},
]


def main():
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    proc = subprocess.Popen(
        [sys.executable, "-m", "cloak_stealth_suite.mcp_server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
        env=env,
    )

    def send(req):
        line = (json.dumps(req) + "\n").encode("utf-8")
        proc.stdin.write(line)
        proc.stdin.flush()

    def read_response(timeout=60):
        # Wait for a single JSON line that contains an "id" we asked for.
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                time.sleep(0.05)
                continue
            try:
                msg = json.loads(line.decode("utf-8"))
            except Exception:
                continue
            return msg
        raise TimeoutError("no response from MCP server")

    try:
        for req in REQS:
            send(req)
            if "id" not in req:
                continue  # notification, no response expected
            resp = read_response()
            assert resp.get("id") == req["id"], f"id mismatch: req={req['id']} resp={resp}"
            assert "error" not in resp, f"server error: {resp['error']}"
            label = req.get("method") if "method" in req else ""
            if label == "tools/call":
                label = f"tools/call({req['params']['name']})"
            print(f"OK  id={req['id']:<2} {label}")
            if req["id"] == 2:
                names = [t["name"] for t in resp["result"]["tools"]]
                print(f"     registered tools: {names}")
            if req["id"] == 3:
                content = resp["result"]["content"][0]["text"]
                data = json.loads(content)
                print(f"     engines: count={data['count']}, categories={len(data['categories'])}")
            if req["id"] == 4:
                content = resp["result"]["content"][0]["text"]
                # FastMCP returns Python-repr strings for dict returns when
                # there's no explicit content type. Try JSON first, then
                # accept the raw string.
                try:
                    data = json.loads(content)
                    print(f"     search: count={data['count']}, first={data['results'][0]['title'] if data['results'] else None!r}")
                except json.JSONDecodeError:
                    snippet = content[:200].replace("\n", " ")
                    print(f"     search returned non-JSON text (likely Python repr): {snippet!r}")
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        err = proc.stderr.read().decode("utf-8", errors="replace")
        if err.strip():
            # Trim noisy INFO lines but show warnings/errors.
            interesting = "\n".join(
                ln for ln in err.splitlines()
                if not ln.startswith("INFO:") or "ERROR" in ln
            )
            if interesting.strip():
                print("\n--- server stderr ---")
                print(interesting[-1500:])
    print("\nALL OK")


if __name__ == "__main__":
    main()
