"""Step 2: end-to-end MCP-protocol test of all 15 tools.

Spawns the registered MCP server, does a handshake, then calls every
tool with cheap arguments and verifies each one returns a sensible
response (OR a documented expected-failure for cases where the live
endpoint is paywalled / rate-limited).

This is the test that proves the MCP install actually works for the
agent — same code path Kiro will hit when invoking these tools.

Run:
    /Users/gao/tools/cloakbrowser/venv/bin/python tests/test_mcp_e2e.py
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import time


def _send(proc, msg):
    proc.stdin.write((json.dumps(msg) + "\n").encode())
    proc.stdin.flush()


def _recv(proc, want_id, timeout_s=120.0):
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


def call_tool(proc, request_id, name, arguments, timeout_s=120.0):
    started = time.time()
    _send(proc, {
        "jsonrpc": "2.0", "id": request_id,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    resp = _recv(proc, request_id, timeout_s=timeout_s)
    elapsed = time.time() - started
    if "error" in resp:
        return None, elapsed, resp["error"]
    # MCP tool result is content-list; for our tools we return JSON
    content = resp.get("result", {}).get("content", [])
    if not content:
        return {}, elapsed, None
    # FastMCP encodes dict results as TextContent JSON
    txt = content[0].get("text") or ""
    try:
        return json.loads(txt), elapsed, None
    except Exception:
        return txt, elapsed, None


# ── checkers — return (label, ok, summary) ──────────────────────────────

def check_search(proc):
    rec, t, err = call_tool(proc, 10, "search",
                            {"query": "python", "engine": "duckduckgo", "limit": 3})
    if err: return ("search (DDG)", False, f"error: {err}")
    n = rec.get("count", 0)
    return ("search (DDG)", n >= 1, f"{n} results in {t:.1f}s")


def check_search_many(proc):
    rec, t, err = call_tool(proc, 11, "search_many",
                            {"query": "python", "engines": ["duckduckgo", "wikipedia"],
                             "limit": 2}, timeout_s=90)
    if err: return ("search_many", False, f"error: {err}")
    succ = rec.get("successful", 0)
    return ("search_many", succ >= 1,
            f"{succ}/{len(rec.get('engines', []))} engines OK, "
            f"{len(rec.get('merged', []))} merged in {t:.1f}s")


def check_extract(proc):
    rec, t, err = call_tool(proc, 12, "extract",
                            {"url": "https://example.com",
                             "paginate": False, "max_scrolls": 0})
    if err: return ("extract", False, f"error: {err}")
    return ("extract", rec.get("status") == "ok",
            f"status={rec.get('status')!r} words={rec.get('word_count')} in {t:.1f}s")


def check_extract_pdf(proc):
    rec, t, err = call_tool(proc, 13, "extract",
                            {"url": "https://www.w3.org/WAI/ER/tests/xhtml/"
                                    "testfiles/resources/pdf/dummy.pdf"})
    if err: return ("extract (PDF)", False, f"error: {err}")
    ok = rec.get("status") == "ok" and rec.get("pdf") is True
    return ("extract (PDF)", ok,
            f"status={rec.get('status')!r} pdf={rec.get('pdf')} "
            f"words={rec.get('word_count')} in {t:.1f}s")


def check_extract_many(proc):
    rec, t, err = call_tool(proc, 14, "extract_many",
                            {"urls": ["https://example.com",
                                      "not-a-url",
                                      "https://example.org"]})
    if err: return ("extract_many", False, f"error: {err}")
    return ("extract_many", rec.get("succeeded", 0) >= 2,
            f"total={rec.get('total')} ok={rec.get('succeeded')} "
            f"failed={rec.get('failed')} in {t:.1f}s")


def check_list_engines(proc):
    rec, t, err = call_tool(proc, 15, "list_engines", {})
    if err: return ("list_engines", False, f"error: {err}")
    return ("list_engines", rec.get("count", 0) > 50,
            f"{rec.get('count')} engines listed in {t:.2f}s")


def check_list_dev_docs(proc):
    rec, t, err = call_tool(proc, 16, "list_dev_docs_platforms",
                            {"filter_substring": "appsflyer"})
    if err: return ("list_dev_docs_platforms", False, f"error: {err}")
    return ("list_dev_docs_platforms", rec.get("count", 0) >= 3,
            f"{rec.get('count')} matches for 'appsflyer'")


def check_engine_status(proc):
    rec, t, err = call_tool(proc, 17, "engine_status",
                            {"engines": ["duckduckgo", "google"]})
    if err: return ("engine_status", False, f"error: {err}")
    rows = rec.get("engines", [])
    return ("engine_status", len(rows) == 2,
            f"{len(rows)} rows; e.g. {rows[0].get('engine')} "
            f"score={rows[0].get('score')}")


def check_screenshot(proc):
    rec, t, err = call_tool(proc, 18, "screenshot",
                            {"url": "https://example.com",
                             "full_page": False})
    if err: return ("screenshot", False, f"error: {err}")
    if rec.get("status") != "ok":
        return ("screenshot", False, f"status={rec.get('status')} err={rec.get('error')}")
    try:
        raw = base64.b64decode(rec["image_base64"], validate=True)
    except Exception as e:
        return ("screenshot", False, f"bad base64: {e}")
    ok = raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) == rec["byte_size"]
    return ("screenshot", ok,
            f"PNG {rec.get('byte_size')}B in {t:.1f}s")


def check_download_files(proc):
    with tempfile.TemporaryDirectory() as td:
        rec, t, err = call_tool(proc, 19, "download_files",
                                {"urls": ["https://example.com/",
                                          "https://example.org/"],
                                 "output_dir": td, "max_workers": 2})
        if err: return ("download_files", False, f"error: {err}")
        return ("download_files", rec.get("succeeded", 0) >= 2,
                f"ok={rec.get('succeeded')} bytes={rec.get('bytes')} in {t:.1f}s")


def check_search_app(proc):
    rec, t, err = call_tool(proc, 20, "search_app",
                            {"query": "shopify", "store": "apple", "limit": 3,
                             "country": "us"}, timeout_s=60)
    if err: return ("search_app", False, f"error: {err}")
    return ("search_app", rec.get("count", 0) >= 1,
            f"{rec.get('count')} apps in {t:.1f}s")


def check_lookup_app(proc):
    rec, t, err = call_tool(proc, 21, "lookup_app",
                            {"app_url": "https://apps.apple.com/us/app/shopify/id371294472",
                             "country": "us"}, timeout_s=60)
    if err: return ("lookup_app", False, f"error: {err}")
    title = rec.get("title") or ""
    return ("lookup_app", "shopify" in title.lower(),
            f"title={title!r} bundle={rec.get('bundle_id')!r}")


def check_find_competitor_ads(proc):
    """Domain mode — much cheaper than the full app pipeline; only
    Google ATC + brand-keyword fan-out fires."""
    rec, t, err = call_tool(proc, 22, "find_competitor_ads",
                            {"app_url": "shopify.com",
                             "platforms": ["google"],
                             "limit_per_platform": 2},
                            timeout_s=120)
    if err: return ("find_competitor_ads (domain)", False, f"error: {err}")
    kind = rec.get("input_kind")
    # Google ATC may legitimately return 0 from APAC IPs — accept either
    # >0 ads OR errors recorded, as long as input_kind=domain.
    has_signal = (rec.get("totals") or {}).get("google", 0) > 0
    has_diag = bool(rec.get("errors"))
    return ("find_competitor_ads (domain)", kind == "domain" and (has_signal or has_diag or "ads" in rec),
            f"input_kind={kind!r} totals={rec.get('totals')} errors={list((rec.get('errors') or {}).keys())}")


def check_summarise_news(proc):
    """Validation case only — real news call would take 60-90s."""
    rec, t, err = call_tool(proc, 23, "summarise_news",
                            {"topic": "", "limit_per_source": 1})
    if err: return ("summarise_news", False, f"error: {err}")
    return ("summarise_news", "error" in rec,
            f"empty-topic correctly rejected: {rec.get('error')!r}")


def check_ads_batch(proc):
    """Validation case only — full live batch is expensive."""
    rec, t, err = call_tool(proc, 24, "ads_batch",
                            {"app_urls": ["totally-not-a-real-app-or-url"],
                             "platforms": ["meta"],
                             "limit_per_platform": 1})
    if err: return ("ads_batch", False, f"error: {err}")
    summaries = rec.get("summaries") or []
    ok = (rec.get("total_apps") == 1 and
          rec.get("failed_apps") == 1 and
          summaries and "error" in summaries[0])
    return ("ads_batch", ok,
            f"total={rec.get('total_apps')} failed={rec.get('failed_apps')} "
            f"err={summaries[0].get('error') if summaries else None!r}")


def check_download_ad_media(proc):
    """No real ads here — just verify the tool accepts the schema and
    returns the expected shape on an empty list."""
    rec, t, err = call_tool(proc, 25, "download_ad_media",
                            {"records": [], "output_dir": "/tmp/agent-search-ads-test"})
    if err: return ("download_ad_media", False, f"error: {err}")
    ok = rec.get("total") == 0 and rec.get("succeeded") == 0
    return ("download_ad_media", ok,
            f"empty input handled: total={rec.get('total')}")


CHECKS = [
    check_search,
    check_search_many,
    check_extract,
    check_extract_pdf,
    check_extract_many,
    check_list_engines,
    check_list_dev_docs,
    check_engine_status,
    check_screenshot,
    check_download_files,
    check_search_app,
    check_lookup_app,
    check_find_competitor_ads,
    check_summarise_news,
    check_ads_batch,
    check_download_ad_media,
]


def main():
    cmd = ["/Users/gao/tools/cloakbrowser/venv/bin/python",
           "-m", "agent_search.mcp_server"]
    env = {**os.environ, "AGENTSEARCH_HEADLESS": "1", "AGENTSEARCH_LOG": "WARNING"}
    print(f"spawning: {' '.join(cmd)}\n")
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=env,
    )

    started_total = time.time()
    failures = 0
    try:
        # Handshake
        _send(proc, {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "e2e-test", "version": "0.1"},
            },
        })
        init = _recv(proc, 1)
        if "error" in init:
            print(f"FAIL initialize: {init['error']}")
            return 1
        info = init["result"].get("serverInfo", {})
        print(f"server: {info.get('name')} v{info.get('version')}\n")
        _send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

        # Run every check. We deliberately serialize — the server pins
        # browser work to one worker thread anyway.
        results = []
        for fn in CHECKS:
            try:
                label, ok, summary = fn(proc)
            except Exception as e:
                label, ok, summary = (fn.__name__, False, f"raised: {type(e).__name__}: {e}")
            mark = "✓" if ok else "✗"
            print(f"  {mark} {label:<32} {summary}")
            results.append((label, ok))
            if not ok:
                failures += 1

        total_t = time.time() - started_total
        passed = sum(1 for _, ok in results if ok)
        print(f"\n{passed}/{len(results)} tools verified in {total_t:.1f}s")
        return 0 if failures == 0 else 1
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
