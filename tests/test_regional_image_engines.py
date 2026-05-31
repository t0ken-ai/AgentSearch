"""Live smoke test for the 17 new engines + 3 image MCP tools.

Each engine gets one cheap query against the real site. Pass = at
least 1 result returned without raising. Failures are logged but
don't abort the run — niche regional engines change DOM regularly,
and the goal is to identify the working subset quickly.

Run:
    /Users/gao/tools/cloakbrowser/venv/bin/python tests/test_regional_image_engines.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import tempfile
import time

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search import core, mcp_server as srv
from agent_search.cli import _get_engine


# (engine_handle, cheap_query, kind)
WEB_ENGINES = [
    ("naver",       "파이썬",        "web"),
    ("yahoo_japan", "Python",        "web"),
    ("daum",        "파이썬",        "web"),
    ("seznam",      "počasí",        "web"),
    ("coccoc",      "Python",        "web"),
    ("mail_ru",     "погода",        "web"),
]

IMAGE_ENGINES = [
    ("google_images",      "cat",          "img"),
    ("bing_images",        "cat",          "img"),
    ("duckduckgo_images",  "cat",          "img"),
    ("baidu_images",       "猫",            "img"),
    ("yandex_images",      "cat",          "img"),
    ("sogou_images",       "猫",            "img"),
    ("so360_images",       "猫",            "img"),
    ("brave_images",       "cat",          "img"),
    ("naver_images",       "고양이",         "img"),
    ("yahoo_japan_images", "猫",            "img"),
    ("daum_images",        "고양이",         "img"),
]


def _test_one_engine(handle: str, query: str, kind: str) -> dict:
    """Run a single engine in a fresh browser. Returns a result dict."""
    started = time.time()
    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        try:
            engine_cls = _get_engine(handle)
            inst = engine_cls(page)
            results = inst.search(query, limit=5) or []
            n = len(results)
            sample_url = ""
            sample_title = ""
            if results:
                first = results[0]
                if kind == "img":
                    sample_url = (getattr(first, "image_url", "")
                                  or getattr(first, "url", ""))
                    sample_title = getattr(first, "title", "") or ""
                else:
                    sample_url = getattr(first, "url", "") or ""
                    sample_title = getattr(first, "title", "") or ""
            return {
                "engine": handle, "query": query, "kind": kind,
                "ok": n >= 1, "count": n,
                "sample_url": sample_url[:80],
                "sample_title": sample_title[:60],
                "elapsed_s": round(time.time() - started, 1),
                "error": "",
            }
        except Exception as e:
            return {
                "engine": handle, "query": query, "kind": kind,
                "ok": False, "count": 0,
                "sample_url": "", "sample_title": "",
                "elapsed_s": round(time.time() - started, 1),
                "error": f"{type(e).__name__}: {e}",
            }
        finally:
            try:
                page.close()
            except Exception:
                pass
    finally:
        try:
            browser.close()
        except Exception:
            pass


def case_engines(label: str, engines: list) -> list[dict]:
    print(f"\n=== {label} ({len(engines)} engines) ===")
    rows = []
    for handle, query, kind in engines:
        r = _test_one_engine(handle, query, kind)
        rows.append(r)
        mark = "✓" if r["ok"] else "✗"
        if r["ok"]:
            print(f"  {mark} {r['engine']:<22} {r['count']} hits "
                  f"in {r['elapsed_s']}s — sample: {r['sample_url']}")
        else:
            err = r["error"][:80] if r["error"] else "0 results"
            print(f"  {mark} {r['engine']:<22} FAIL ({r['elapsed_s']}s): {err}")
    return rows


def case_mcp_tools() -> list[dict]:
    print(f"\n=== MCP image tools (3 tools) ===")
    rows: list[dict] = []

    async def go():
        # 1) image_search via Bing (most reliable)
        started = time.time()
        out = await srv.image_search(
            query="kitten", engine="bing_images", limit=5
        )
        rec = {
            "tool": "image_search", "ok": out.get("count", 0) >= 1,
            "count": out.get("count", 0),
            "elapsed_s": round(time.time() - started, 1),
            "error": out.get("error", ""),
            "_results": out.get("results", []),
        }
        rows.append(rec)
        mark = "✓" if rec["ok"] else "✗"
        print(f"  {mark} image_search (bing_images)  {rec['count']} hits "
              f"in {rec['elapsed_s']}s {('— ' + rec['error'][:60]) if rec.get('error') else ''}")

        # 2) image_search_many across cheap subset
        started = time.time()
        out2 = await srv.image_search_many(
            query="kitten",
            engines=["bing_images", "duckduckgo_images"],
            limit=3, timeout_s=60,
        )
        rec2 = {
            "tool": "image_search_many",
            "ok": out2.get("successful", 0) >= 1,
            "count": len(out2.get("merged", [])),
            "successful": out2.get("successful", 0),
            "elapsed_s": out2.get("elapsed_s"),
            "error": out2.get("error", ""),
        }
        rows.append(rec2)
        mark = "✓" if rec2["ok"] else "✗"
        print(f"  {mark} image_search_many          {rec2['successful']}/2 engines OK, "
              f"{rec2['count']} merged in {rec2['elapsed_s']}s")

        # 3) download_images — feed it the image_search results PLUS a
        # known-permissive URL so the test is robust against random
        # CDNs returning 460/465 anti-hotlink errors. We just need to
        # prove the bulk-download wiring works end-to-end.
        with tempfile.TemporaryDirectory() as td:
            started = time.time()
            test_imgs = list(rec.get("_results", []))[:3] + [
                "https://www.python.org/static/img/python-logo.png",
            ]
            out3 = await srv.download_images(
                images=test_imgs,
                output_dir=td,
                max_workers=4,
                timeout=20,
            )
            rec3 = {
                "tool": "download_images",
                "ok": out3.get("succeeded", 0) >= 1,
                "total": out3.get("total", 0),
                "succeeded": out3.get("succeeded", 0),
                "bytes": out3.get("bytes", 0),
                "elapsed_s": round(time.time() - started, 1),
            }
            rows.append(rec3)
            mark = "✓" if rec3["ok"] else "✗"
            print(f"  {mark} download_images            "
                  f"{rec3['succeeded']}/{rec3['total']} files "
                  f"({rec3['bytes']}B) in {rec3['elapsed_s']}s")

    asyncio.run(go())
    return rows


def main() -> int:
    logging.basicConfig(level=logging.WARNING,
                        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    print("=== Regional + Image engines smoke test ===")

    web_rows = case_engines("Regional web engines", WEB_ENGINES)
    img_rows = case_engines("Image search engines", IMAGE_ENGINES)
    mcp_rows = case_mcp_tools()

    # Tear down shared pool used by image_search MCP tool.
    try:
        srv._BROWSER_EXECUTOR.submit(srv._pool.shutdown).result(timeout=10)
    except Exception:
        pass
    finally:
        srv._BROWSER_EXECUTOR.shutdown(wait=True, cancel_futures=True)

    web_ok = sum(1 for r in web_rows if r["ok"])
    img_ok = sum(1 for r in img_rows if r["ok"])
    mcp_ok = sum(1 for r in mcp_rows if r.get("ok"))

    print()
    print("=== Summary ===")
    print(f"  Regional web: {web_ok}/{len(web_rows)} engines OK")
    print(f"  Image search: {img_ok}/{len(img_rows)} engines OK")
    print(f"  MCP tools   : {mcp_ok}/{len(mcp_rows)} tools OK")

    # Print failing engines so we can fix them
    fails = [r for r in web_rows + img_rows if not r["ok"]]
    if fails:
        print("\nFailing engines:")
        for r in fails:
            err = r.get("error") or "0 results"
            print(f"  - {r['engine']:<22} {err[:120]}")
    failing_mcp = [r for r in mcp_rows if not r.get("ok")]
    if failing_mcp:
        print("\nFailing MCP tools:")
        for r in failing_mcp:
            print(f"  - {r['tool']}: {r.get('error') or 'unknown'}")

    # Pass criterion: most engines work + all MCP tools work.
    # Some niche engines may legitimately fail from this IP — we accept
    # ≥70% web and ≥70% image as the bar.
    web_thresh = max(1, int(0.7 * len(web_rows)))
    img_thresh = max(1, int(0.7 * len(img_rows)))
    overall_ok = (web_ok >= web_thresh and img_ok >= img_thresh
                  and mcp_ok == len(mcp_rows))
    print(f"\n{'PASS' if overall_ok else 'PARTIAL — see fails above'}")
    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
