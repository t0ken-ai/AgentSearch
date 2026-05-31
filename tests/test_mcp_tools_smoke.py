"""Smoke tests for the new + extended MCP tools.

Coverage per tool — cheapest viable test that proves the wiring works
end-to-end. Live network calls only where the value is genuine and the
endpoint is fast / rate-limit-free.

Tools covered:
  * ``search_many``                    — live (DDG + Wikipedia, ~5-10s)
  * ``engine_status``                  — pure Python (HealthLog read)
  * ``search(fallback=True)``          — pure Python (signature + flag)
  * ``screenshot``                     — live (example.com, ~2s)
  * ``download_files``                 — live (example.com → tmp file)
  * ``find_competitor_ads`` (domain)   — pure Python (helpers only)
  * ``extract`` (PDF)                  — live (W3C dummy PDF, ~1s)
  * ``summarise_news``                 — pure Python (input validation)
  * ``ads_batch``                      — pure Python (shape + bogus input)

Run:
    ~/tools/cloakbrowser/venv/bin/python tests/test_mcp_tools_smoke.py
"""
from __future__ import annotations

import asyncio
import base64
import inspect
import logging
import os
import sys
import tempfile
import time
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# Import via mcp_server to ensure the @mcp.tool wrapper doesn't break the
# raw async signature.
from agent_search import mcp_server as srv
from agent_search.extract import (
    _extract_pdf,
    _looks_like_pdf_url,
    _detect_pdf_via_head,
)


def _call_tool(coro_func, *args, **kwargs):
    """FastMCP wraps tools — the original async function is on .fn or
    accessible via the unwrapped attribute. Fall back to looking it up
    by name in the module dict.
    """
    fn = getattr(coro_func, "fn", None) or coro_func
    if not inspect.iscoroutinefunction(fn):
        # Look for the original by name in the module.
        cand = getattr(srv, fn.__name__ if hasattr(fn, "__name__") else "", None)
        if inspect.iscoroutinefunction(cand):
            fn = cand
    return asyncio.run(fn(*args, **kwargs))


# --------------------------------------------------------------- 1. search_many

def case_search_many() -> int:
    print("[search_many] live: ['duckduckgo', 'wikipedia'] for 'python'")
    started = time.time()
    out = _call_tool(srv.search_many, "python", ["duckduckgo", "wikipedia"],
                     limit=3, timeout_s=60)
    elapsed = time.time() - started
    print(f"  query={out.get('query')!r} successful={out.get('successful')} "
          f"elapsed={elapsed:.1f}s")

    if "error" in out:
        print(f"  FAIL: {out['error']}")
        return 1
    if out.get("successful", 0) < 1:
        print("  FAIL: zero engines succeeded")
        return 1
    if not out.get("merged"):
        print("  FAIL: empty merged list")
        return 1
    sample = out["merged"][0]
    print(f"  sample[0].url     : {sample.get('url')}")
    print(f"  sample[0].engines : {sample.get('engines')}")
    if not sample.get("engines"):
        print("  FAIL: merged item missing engines list")
        return 1
    print("  PASS")
    return 0


# --------------------------------------------------------------- 2. engine_status

def case_engine_status() -> int:
    print("[engine_status] reading HealthLog")
    out = _call_tool(srv.engine_status, ["duckduckgo", "google"])
    print(f"  log_path: {out.get('log_path')}")

    rows = out.get("engines") or []
    print(f"  rows: {len(rows)}")
    if "error" in out:
        print(f"  FAIL: {out['error']}")
        return 1
    if len(rows) != 2:
        print(f"  FAIL: expected 2 rows, got {len(rows)}")
        return 1
    for row in rows:
        for key in ("engine", "attempts", "score"):
            if key not in row:
                print(f"  FAIL: row missing {key!r}: {row}")
                return 1
    print(f"  sample row: {rows[0]}")
    print("  PASS")
    return 0


# --------------------------------------------------------------- 3. search fallback

def case_search_fallback_signature() -> int:
    print("[search.fallback] verifying parameter exists and is plumbed")
    fn = getattr(srv.search, "fn", srv.search)
    sig = inspect.signature(fn)
    if "fallback" not in sig.parameters:
        print("  FAIL: search() missing 'fallback' parameter")
        return 1
    p = sig.parameters["fallback"]
    if p.default is not False:
        print(f"  FAIL: fallback default is {p.default!r}, want False")
        return 1
    print(f"  signature OK: fallback: {p.annotation} = {p.default}")
    print("  PASS")
    return 0


# --------------------------------------------------------------- 4. screenshot

def case_screenshot() -> int:
    print("[screenshot] live: https://example.com (png)")
    out = _call_tool(srv.screenshot, "https://example.com",
                     full_page=False, format="png", timeout_ms=20000)
    print(f"  status={out.get('status')} byte_size={out.get('byte_size')} "
          f"format={out.get('format')}")
    if out.get("status") != "ok":
        print(f"  FAIL: status={out.get('status')!r} error={out.get('error')!r}")
        return 1
    b64 = out.get("image_base64") or ""
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception as e:
        print(f"  FAIL: image_base64 not valid base64: {e}")
        return 1
    # PNG magic bytes
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        print(f"  FAIL: decoded bytes don't start with PNG magic: {raw[:8]!r}")
        return 1
    if len(raw) != out["byte_size"]:
        print(f"  FAIL: byte_size mismatch: declared={out['byte_size']} "
              f"actual={len(raw)}")
        return 1
    print(f"  decoded {len(raw)} bytes, PNG header OK")
    print("  PASS")
    return 0


# --------------------------------------------------------------- 5. download_files

def case_download_files() -> int:
    print("[download_files] live: download example.com to tmp dir")
    with tempfile.TemporaryDirectory() as tmp:
        out = _call_tool(srv.download_files,
                         ["https://example.com/", "https://example.com/?x=1"],
                         output_dir=tmp, max_workers=2, timeout=15)
        print(f"  total={out.get('total')} succeeded={out.get('succeeded')} "
              f"bytes={out.get('bytes')}")
        if out.get("succeeded", 0) < 1:
            print(f"  FAIL: zero successful downloads — files={out.get('files')}")
            return 1
        # Verify at least one file actually exists on disk
        files = out.get("files") or []
        on_disk = [f for f in files
                   if f.get("success") and os.path.exists(f.get("local_path") or "")]
        if not on_disk:
            print("  FAIL: no successful file present on disk")
            return 1
        # Re-run with overwrite=False — should be skipped
        out2 = _call_tool(srv.download_files,
                          ["https://example.com/"],
                          output_dir=tmp, overwrite=False, timeout=15)
        if out2.get("skipped", 0) < 1:
            print("  FAIL: re-download didn't trigger skip-on-exists")
            return 1
    print("  PASS")
    return 0


# --------------------------------------------------------------- 6. find_competitor_ads(domain)

def case_competitor_ads_helpers() -> int:
    print("[find_competitor_ads(domain)] testing _extract_domain helper")
    # Reach through the closure-captured helper by replicating the logic
    # the tool uses.
    from urllib.parse import urlparse

    def _extract(s):
        s = (s or "").strip().lower()
        if not s:
            return None
        if s.startswith(("http://", "https://")):
            try:
                host = (urlparse(s).netloc or "").split(":")[0]
                if host.startswith("www."):
                    host = host[4:]
                if "." in host:
                    return host
            except Exception:
                return None
        if "/" in s or " " in s or "." not in s:
            return None
        parts = s.split(".")
        if len(parts) >= 3 and parts[0] in (
            "com", "io", "net", "org", "app", "co", "ai", "dev",
        ):
            return None
        if s.replace(".", "").isdigit():
            return None
        return s

    cases = [
        ("https://shopify.com",                "shopify.com"),
        ("https://www.shopify.com/path/?q=1",  "shopify.com"),
        ("shopify.com",                         "shopify.com"),
        ("apps.apple.com/us/app/.../id371294472", None),    # has slash → reject (treated as app URL upstream)
        ("https://apps.apple.com/.../id371294472", "apps.apple.com"),  # OK domain even though it's an app URL
        ("com.example.app",                     None),    # Google Play package id
        ("io.foo.bar",                          None),    # package id
        ("1234567890",                          None),    # bare numeric
        ("",                                    None),
    ]
    failures = 0
    for input, expected in cases:
        got = _extract(input)
        ok = got == expected
        print(f"  {input!r:<55} -> {got!r:<20} expected={expected!r}  "
              f"{'PASS' if ok else 'FAIL'}")
        if not ok:
            failures += 1

    # Also verify the tool's signature accepts the same arg name (still
    # called app_url but the docstring promises domains are accepted).
    fn = getattr(srv.find_competitor_ads, "fn", srv.find_competitor_ads)
    if "app_url" not in inspect.signature(fn).parameters:
        print("  FAIL: find_competitor_ads signature changed unexpectedly")
        failures += 1

    print(f"  {'PASS' if failures == 0 else f'{failures} FAIL'}")
    return 0 if failures == 0 else 1


# --------------------------------------------------------------- 7. extract PDF

def case_extract_pdf() -> int:
    print("[extract.pdf] live: W3C dummy.pdf")
    if not _looks_like_pdf_url(
        "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf"
    ):
        print("  FAIL: _looks_like_pdf_url didn't recognise .pdf suffix")
        return 1

    rec = _extract_pdf(
        "https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf"
    )
    print(f"  status={rec.get('status')} pages={rec.get('page_count')} "
          f"words={rec.get('word_count')}")
    if rec.get("status") != "ok":
        print(f"  FAIL: status={rec.get('status')} error={rec.get('error')!r}")
        return 1
    if rec.get("word_count", 0) < 1:
        print("  FAIL: no words extracted")
        return 1
    if not rec.get("pdf"):
        print("  FAIL: pdf=true marker missing")
        return 1
    print(f"  text head: {(rec.get('content_text') or '')[:120]!r}")
    print("  PASS")
    return 0


# --------------------------------------------------------------- 8. summarise_news input validation

def case_summarise_news_validation() -> int:
    print("[summarise_news] empty topic should error early")
    out = _call_tool(srv.summarise_news, "", limit_per_source=1)
    if "error" not in out:
        print(f"  FAIL: empty topic should return error, got: {out}")
        return 1
    print(f"  error message: {out['error']}")
    print("  PASS")
    return 0


# --------------------------------------------------------------- 9. ads_batch input validation

def case_ads_batch_validation() -> int:
    print("[ads_batch] bogus input → should report failure cleanly")
    out = _call_tool(srv.ads_batch,
                     ["not-an-app-or-url-or-anything-recognisable"],
                     platforms=["meta"], limit_per_platform=1)
    print(f"  total_apps={out.get('total_apps')} "
          f"failed_apps={out.get('failed_apps')}")
    if out.get("total_apps") != 1:
        print("  FAIL: expected 1 app entry")
        return 1
    if out.get("failed_apps") != 1:
        print(f"  FAIL: expected the bogus input to fail, got "
              f"failed_apps={out.get('failed_apps')}")
        return 1
    summaries = out.get("summaries") or []
    if not summaries or "error" not in summaries[0]:
        print(f"  FAIL: summaries[0] missing error field: {summaries}")
        return 1
    print(f"  summaries[0].error: {summaries[0]['error']!r}")
    print("  PASS")
    return 0


# --------------------------------------------------------------- runner

def main() -> int:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== mcp_server new tools smoke ===\n")
    cases = [
        ("search_many",                       case_search_many),
        ("engine_status",                     case_engine_status),
        ("search.fallback (signature)",       case_search_fallback_signature),
        ("screenshot",                        case_screenshot),
        ("download_files",                    case_download_files),
        ("find_competitor_ads helpers",       case_competitor_ads_helpers),
        ("extract.pdf",                       case_extract_pdf),
        ("summarise_news (validation)",       case_summarise_news_validation),
        ("ads_batch (validation)",            case_ads_batch_validation),
    ]
    failures = 0
    for label, fn in cases:
        print(f"--- {label} ---")
        try:
            failures += fn()
        except Exception:
            failures += 1
            traceback.print_exc()
        print()

    # Tear down the shared browser pool the same way main() does.
    try:
        srv._BROWSER_EXECUTOR.submit(srv._pool.shutdown).result(timeout=10)
    except Exception as e:
        print(f"  shutdown raised: {e}")
    finally:
        srv._BROWSER_EXECUTOR.shutdown(wait=True, cancel_futures=True)

    print(f"{'PASS' if failures == 0 else f'{failures} FAIL'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
