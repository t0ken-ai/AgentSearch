"""Enhanced probe: same query set as baseline, but exercises the new modes
(hashtag with scroll + serp fallbacks, user mode, post mode, enrich) and
records which path filled which fields.

Run:
    ~/tools/cloakbrowser/venv/bin/python tests/probe_instagram_enhanced.py
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


# Each entry: (label, query, mode, kwargs)
PROBES = [
    # 1) hashtag mode with multi-fallback (Google → DDG → Bing) and scroll.
    ("en_common",   "travel",                "hashtag", {"max_scrolls": 1}),
    ("en_topic",    "coffee",                "hashtag", {"max_scrolls": 1}),
    ("en_long",     "san francisco coffee",  "hashtag", {"max_scrolls": 1}),
    ("en_niche",    "kubernetes",            "hashtag", {"max_scrolls": 0}),
    ("zh_common",   "旅行",                   "hashtag", {"max_scrolls": 0}),
    ("zh_food",     "料理",                   "hashtag", {"max_scrolls": 0}),
    ("hash_form",   "#mountain",             "hashtag", {"max_scrolls": 1}),
    ("phrase",      "morning routine",       "hashtag", {"max_scrolls": 1}),
    ("emoji",       "🌸",                    "hashtag", {}),

    # 2) user mode — should now treat 'natgeo' as the famous account.
    ("user_natgeo", "natgeo",                "user",    {}),
    ("user_nasa",   "nasa",                  "user",    {}),

    # 3) post mode — direct enrichment via og:description.
    #    These shortcodes were collected from the baseline run.
    ("post_reel",   "https://www.instagram.com/reel/DTfS7SMEk8B/", "post", {}),
    ("post_p",      "C5U7ovNPAMJ",           "post",    {}),

    # 4) enrich=True on a hashtag run — turns 6 captionless reels into
    #    6 reels with likes/comments/posted_at.
    ("enrich_travel",  "travel",             "hashtag", {"enrich": True}),
]


def _summarise(results: list) -> dict:
    n = len(results)
    if n == 0:
        return {
            "n": 0, "with_user": 0, "with_caption": 0,
            "with_likes": 0, "with_comments": 0,
            "with_posted_at": 0, "with_image": 0,
            "with_image_urls": 0, "with_video_url": 0,
            "by_post_type": {}, "by_source": {},
        }
    pt: dict = {}
    src: dict = {}
    fields = {
        "with_user": 0, "with_caption": 0, "with_likes": 0,
        "with_comments": 0, "with_posted_at": 0, "with_image": 0,
        "with_image_urls": 0, "with_video_url": 0,
    }
    for r in results:
        if getattr(r, "user", ""):
            fields["with_user"] += 1
        if getattr(r, "caption", ""):
            fields["with_caption"] += 1
        if getattr(r, "likes", None) is not None:
            fields["with_likes"] += 1
        if getattr(r, "comments", None) is not None:
            fields["with_comments"] += 1
        if getattr(r, "posted_at", ""):
            fields["with_posted_at"] += 1
        if getattr(r, "image_url", ""):
            fields["with_image"] += 1
        if getattr(r, "image_urls", None):
            fields["with_image_urls"] += 1
        if getattr(r, "video_url", ""):
            fields["with_video_url"] += 1
        pt[getattr(r, "post_type", "?")] = pt.get(getattr(r, "post_type", "?"), 0) + 1
        src[getattr(r, "source", "?")] = src.get(getattr(r, "source", "?"), 0) + 1
    return {"n": n, **fields, "by_post_type": pt, "by_source": src}


def _probe_one(label: str, query: str, mode: str, kwargs: dict, *, limit: int = 6) -> dict:
    print(f"\n=== probe {label!r} :: query={query!r} mode={mode} kwargs={kwargs} ===")
    started = time.time()
    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    row = {
        "label": label,
        "query": query,
        "mode": mode,
        "kwargs": kwargs,
        "ok": False,
        "elapsed_s": 0.0,
        "error": "",
    }
    try:
        page = core.new_page(browser)
        engine = InstagramEngine(page)
        try:
            results = engine.search(query, limit=limit, mode=mode, **kwargs)
        except Exception as e:
            traceback.print_exc()
            row["error"] = f"{type(e).__name__}: {e}"
            results = []

        row["last_status"] = dict(engine.last_status)
        try:
            row["page_url"] = page.url
        except Exception:
            row["page_url"] = ""

        summary = _summarise(results)
        row.update(summary)
        row["ok"] = summary["n"] > 0
        row["elapsed_s"] = round(time.time() - started, 1)

        print(json.dumps(row, ensure_ascii=False, indent=2))

        for i, r in enumerate(results[:2], start=1):
            cap = (getattr(r, "caption", "") or "").replace("\n", " ")
            if len(cap) > 100:
                cap = cap[:100] + "..."
            print(
                f"  [{i}] {getattr(r, 'post_type', '?'):7s} "
                f"@{getattr(r, 'user', '') or '-':<24s} "
                f"src={getattr(r, 'source', '?'):<22s} "
                f"likes={getattr(r, 'likes', None)} "
                f"comments={getattr(r, 'comments', None)} "
                f"date={getattr(r, 'posted_at', '') or '-'} "
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

    # Optional: filter probes via CLI args, e.g. `... -- en_common post_p`.
    # When no filter is provided, run the full set.
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    selected = PROBES
    if args:
        wanted = set(args)
        selected = [p for p in PROBES if p[0] in wanted]
        if not selected:
            print(f"no probe matches {args!r}; available: "
                  + ", ".join(p[0] for p in PROBES))
            return 2

    print(f"=== Instagram ENHANCED probe — {len(selected)} probes ===")

    rows: list[dict] = []
    for label, query, mode, kwargs in selected:
        rows.append(_probe_one(label, query, mode, kwargs))

    print("\n=== SUMMARY TABLE ===")
    print(
        f"{'label':<14s} {'query':<26s} {'mode':<8s} {'ok':<3s} {'n':<3s} "
        f"{'src':<28s} {'u/c/l/c/d/i':<14s} {'time':<6s} reason"
    )
    for r in rows:
        src = ",".join(f"{k}:{v}" for k, v in (r.get("by_source") or {}).items())
        present = (
            f"{r.get('with_user', 0)}/{r.get('with_caption', 0)}/"
            f"{r.get('with_likes', 0)}/{r.get('with_comments', 0)}/"
            f"{r.get('with_posted_at', 0)}/{r.get('with_image', 0)}"
        )
        reason = (
            (r.get("last_status") or {}).get("block_reason")
            or (r.get("last_status") or {}).get("error")
            or r.get("error", "")
        )
        print(
            f"{r['label']:<14s} {r['query']:<26s} {r['mode']:<8s} "
            f"{'✓' if r['ok'] else '✗':<3s} {r.get('n', 0):<3d} "
            f"{src:<28s} {present:<14s} {r.get('elapsed_s', 0):<6.1f} {reason}"
        )

    out_path = os.path.join(_PROJECT_ROOT, "tests", "instagram_enhanced.json")
    # If we ran a subset, append rather than overwrite — that way an
    # iterative session builds up the full result set across runs.
    if args and os.path.exists(out_path):
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                prev = (json.load(f) or {}).get("probes") or []
        except Exception:
            prev = []
        # Replace any pre-existing rows with the same label.
        labels_now = {r["label"] for r in rows}
        kept = [r for r in prev if r.get("label") not in labels_now]
        rows = kept + rows
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"probes": rows}, f, ensure_ascii=False, indent=2)
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
