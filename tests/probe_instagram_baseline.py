"""Baseline probe: run InstagramEngine across many query shapes and tabulate
which path (direct vs google), how many results, which fields are present.

Goal: find the weak spots before we extend the engine.

Run:
    ~/tools/cloakbrowser/venv/bin/python tests/probe_instagram_baseline.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search import core
from agent_search.engines.instagram import InstagramEngine


# Each entry: (label, query, comment about what we're probing)
QUERIES = [
    ("en_common",   "travel",                "very popular en hashtag"),
    ("en_topic",    "coffee",                "popular en topic"),
    ("en_long",     "san francisco coffee",  "multi-word — gets slugged to one tag"),
    ("en_niche",    "kubernetes",            "niche tech tag"),
    ("zh_common",   "旅行",                   "Chinese — does IG render?"),
    ("zh_food",     "料理",                   "Chinese food tag"),
    ("hash_form",   "#mountain",             "user prepended # symbol"),
    ("user_name",   "natgeo",                "this is a username, not a hashtag"),
    ("phrase",      "morning routine",       "two-word free-text query"),
    ("emoji",       "🌸",                    "single emoji — IG slugifier strips it"),
]


def _summarise(results: list) -> dict:
    """Per-result-list field-presence summary."""
    n = len(results)
    if n == 0:
        return {
            "n": 0,
            "with_user": 0,
            "with_caption": 0,
            "with_likes": 0,
            "with_comments": 0,
            "by_post_type": {},
            "by_source": {},
        }
    pt: dict = {}
    src: dict = {}
    with_user = with_caption = with_likes = with_comments = 0
    for r in results:
        if getattr(r, "user", ""):
            with_user += 1
        if getattr(r, "caption", ""):
            with_caption += 1
        if getattr(r, "likes", None) is not None:
            with_likes += 1
        if getattr(r, "comments", None) is not None:
            with_comments += 1
        pt[getattr(r, "post_type", "?")] = pt.get(getattr(r, "post_type", "?"), 0) + 1
        src[getattr(r, "source", "?")] = src.get(getattr(r, "source", "?"), 0) + 1
    return {
        "n": n,
        "with_user": with_user,
        "with_caption": with_caption,
        "with_likes": with_likes,
        "with_comments": with_comments,
        "by_post_type": pt,
        "by_source": src,
    }


def _probe_one(label: str, query: str, *, limit: int = 6) -> dict:
    """Run one search end-to-end with a fresh browser. Returns a dict row."""
    print(f"\n=== probe {label!r} :: query={query!r} ===")
    started = time.time()
    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    row: dict = {
        "label": label,
        "query": query,
        "ok": False,
        "elapsed_s": 0.0,
        "error": "",
    }
    try:
        page = core.new_page(browser)
        engine = InstagramEngine(page)
        try:
            results = engine._do_search(query, limit)
        except Exception as e:
            traceback.print_exc()
            row["error"] = f"{type(e).__name__}: {e}"
            results = []

        row["last_status"] = dict(engine.last_status)
        row["page_url"] = ""
        try:
            row["page_url"] = page.url
        except Exception:
            pass

        summary = _summarise(results)
        row.update(summary)
        row["ok"] = summary["n"] > 0
        row["elapsed_s"] = round(time.time() - started, 1)

        # Print the row inline so we get incremental feedback even if the
        # process is cut short.
        print(json.dumps(row, ensure_ascii=False, indent=2))

        # First two results, brief.
        for i, r in enumerate(results[:2], start=1):
            cap = (getattr(r, "caption", "") or "").replace("\n", " ")
            if len(cap) > 100:
                cap = cap[:100] + "..."
            print(
                f"  [{i}] {getattr(r, 'post_type', '?'):4s} "
                f"@{getattr(r, 'user', '') or '-':<24s} "
                f"src={getattr(r, 'source', '?'):<10s} "
                f"likes={getattr(r, 'likes', None)} "
                f"sc={getattr(r, 'shortcode', '')} :: {cap}"
            )
    finally:
        try:
            browser.close()
        except Exception:
            pass
    return row


def main() -> int:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    print(f"=== Instagram BASELINE probe — {len(QUERIES)} queries ===")

    rows: list[dict] = []
    for label, query, _comment in QUERIES:
        rows.append(_probe_one(label, query))

    print("\n=== SUMMARY TABLE ===")
    print(
        f"{'label':<12s} {'query':<26s} {'ok':<4s} {'n':<3s} "
        f"{'src':<26s} {'usr/cap/lk/cm':<14s} {'time':<6s} reason"
    )
    for r in rows:
        src = ",".join(f"{k}:{v}" for k, v in (r.get("by_source") or {}).items())
        present = (
            f"{r.get('with_user', 0)}/{r.get('with_caption', 0)}/"
            f"{r.get('with_likes', 0)}/{r.get('with_comments', 0)}"
        )
        reason = (
            (r.get("last_status") or {}).get("block_reason")
            or (r.get("last_status") or {}).get("error")
            or r.get("error", "")
        )
        print(
            f"{r['label']:<12s} {r['query']:<26s} "
            f"{'✓' if r['ok'] else '✗':<4s} {r.get('n', 0):<3d} "
            f"{src:<26s} {present:<14s} {r.get('elapsed_s', 0):<6.1f} {reason}"
        )

    # Persist for later comparison.
    out_path = os.path.join(
        _PROJECT_ROOT, "tests", "instagram_baseline.json"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"queries": rows}, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
