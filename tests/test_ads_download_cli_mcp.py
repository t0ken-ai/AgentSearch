"""End-to-end smoke for the ``ads-download`` CLI subcommand and the
``download_ad_media`` MCP tool.

Drives the full pipeline against a local HTTP server (no network /
proxy required), so the test is fast, hermetic, and deterministic.

Run::

    ~/tools/cloakbrowser/venv/bin/python tests/test_ads_download_cli_mcp.py
"""
from __future__ import annotations

import asyncio
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)


_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0dIHDR"
    + b"\x00" * 13
    + b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(_PNG)))
        self.end_headers()
        self.wfile.write(_PNG)


def _server() -> tuple[socketserver.TCPServer, int]:
    s = socketserver.TCPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s, s.server_address[1]


def _records(port: int) -> list[dict]:
    return [
        {
            "ad_archive_id": "M1",
            "page_name": "Brand X",
            "image_urls": [
                f"http://127.0.0.1:{port}/img-1.png",
                f"http://127.0.0.1:{port}/img-2.png",
            ],
            "video_url": f"http://127.0.0.1:{port}/vid-1.png",
        },
        {
            "ad_archive_id": "M2",
            "page_name": "Brand Y",
            "image_urls": [f"http://127.0.0.1:{port}/img-3.png"],
        },
    ]


# ── CLI ─────────────────────────────────────────────────────────────


def t_cli_jsonl_input() -> int:
    """ads-download <file.jsonl>"""
    srv, port = _server()
    tmpdir = tempfile.mkdtemp(prefix="ads-cli-")
    jsonl_path = os.path.join(tmpdir, "in.jsonl")
    try:
        with open(jsonl_path, "w") as f:
            for r in _records(port):
                f.write(json.dumps(r) + "\n")

        out_dir = os.path.join(tmpdir, "out")
        py = sys.executable
        proc = subprocess.run(
            [py, "-m", "agent_search.cli", "ads-download",
             jsonl_path, "-o", out_dir, "--quiet"],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            print(f"  FAIL: returncode={proc.returncode}")
            print(f"    stdout: {proc.stdout}")
            print(f"    stderr: {proc.stderr}")
            return 1
        files = sorted(os.listdir(out_dir))
        # 2 records, 3 + 1 urls = 4 files expected
        if len(files) != 4:
            print(f"  FAIL: expected 4 files, got {len(files)}: {files}")
            return 1
        if not all(f.startswith("meta_M") for f in files):
            print(f"  FAIL: filenames missing prefix: {files}")
            return 1
        # Summary line should mention 4/4
        if "4/4" not in proc.stderr:
            print(f"  FAIL: stderr summary missing 4/4:\n{proc.stderr}")
            return 1
        print(f"  PASS: 4 files via JSONL ({files[0]}...)")
        return 0
    finally:
        srv.shutdown()
        srv.server_close()
        shutil.rmtree(tmpdir, ignore_errors=True)


def t_cli_stdin() -> int:
    """ads-download - via stdin"""
    srv, port = _server()
    tmpdir = tempfile.mkdtemp(prefix="ads-cli2-")
    try:
        out_dir = os.path.join(tmpdir, "out")
        jsonl_blob = "\n".join(json.dumps(r) for r in _records(port))
        proc = subprocess.run(
            [sys.executable, "-m", "agent_search.cli", "ads-download",
             "-", "-o", out_dir, "--json"],
            input=jsonl_blob, capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            print(f"  FAIL: returncode={proc.returncode}\nstderr: {proc.stderr}")
            return 1
        # --json output should parse and include 4 entries
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            print(f"  FAIL: --json output not parseable: {e}\n{proc.stdout[:200]}")
            return 1
        if len(data) != 4:
            print(f"  FAIL: expected 4 in JSON, got {len(data)}")
            return 1
        succ = sum(1 for d in data if d.get("success"))
        if succ != 4:
            print(f"  FAIL: only {succ}/4 succeeded")
            return 1
        print(f"  PASS: stdin pipe + --json ({succ}/4)")
        return 0
    finally:
        srv.shutdown()
        srv.server_close()
        shutil.rmtree(tmpdir, ignore_errors=True)


def t_cli_full_engine_response() -> int:
    """A whole engine JSON dump (with .results array) is also accepted."""
    srv, port = _server()
    tmpdir = tempfile.mkdtemp(prefix="ads-cli3-")
    try:
        path = os.path.join(tmpdir, "engine_response.json")
        with open(path, "w") as f:
            json.dump({
                "engine": "meta_ad_library",
                "query": "shopify",
                "count": 2,
                "results": _records(port),
            }, f)

        out_dir = os.path.join(tmpdir, "out")
        proc = subprocess.run(
            [sys.executable, "-m", "agent_search.cli", "ads-download",
             path, "-o", out_dir, "--quiet"],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0 or "4/4" not in proc.stderr:
            print(f"  FAIL: rc={proc.returncode}\nstderr: {proc.stderr}")
            return 1
        print("  PASS: full engine response JSON accepted")
        return 0
    finally:
        srv.shutdown()
        srv.server_close()
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── MCP tool ────────────────────────────────────────────────────────


def t_mcp_tool() -> int:
    """Invoke the FastMCP tool function directly (it's a coroutine)."""
    from agent_search.mcp_server import download_ad_media

    srv, port = _server()
    tmpdir = tempfile.mkdtemp(prefix="ads-mcp-")
    try:
        async def _go():
            # FastMCP wraps the function in an MCP tool; depending on
            # version it may expose a .run or be directly awaitable.
            fn = download_ad_media
            if hasattr(fn, "fn"):
                fn = fn.fn
            return await fn(_records(port), output_dir=tmpdir,
                            max_workers=2)

        result = asyncio.run(_go())
        if result["total"] != 4 or result["succeeded"] != 4:
            print(f"  FAIL: result={result}")
            return 1
        if not result.get("files") or len(result["files"]) != 4:
            print(f"  FAIL: files list wrong: {result.get('files')}")
            return 1
        if result["bytes"] <= 0:
            print(f"  FAIL: bytes={result['bytes']}")
            return 1
        print(f"  PASS: MCP tool downloaded {result['succeeded']}/{result['total']}, "
              f"{result['bytes']} bytes")
        return 0
    finally:
        srv.shutdown()
        srv.server_close()
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── runner ──────────────────────────────────────────────────────────


def main() -> int:
    print("=== test_ads_download_cli_mcp ===")
    failures = 0
    for label, fn in [
        ("cli.jsonl_input",            t_cli_jsonl_input),
        ("cli.stdin_pipe",             t_cli_stdin),
        ("cli.full_engine_response",   t_cli_full_engine_response),
        ("mcp.download_ad_media",      t_mcp_tool),
    ]:
        print(f"\n[{label}]")
        try:
            failures += fn()
        except Exception:
            traceback.print_exc()
            failures += 1
    print(f"\n{'PASS' if failures == 0 else f'{failures} FAIL'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
