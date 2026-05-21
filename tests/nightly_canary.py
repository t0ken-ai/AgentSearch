"""Nightly canary — run a small smoke search through every engine.

Each engine is treated as PASS / DEGRADED / FAIL based on whether it
returns >= MIN_RESULTS hits within TIMEOUT seconds for a single canary
query. Output is a JSON report at ``canary_report.json`` and a
human-readable summary on stdout. CI consumes the JSON to fail the
build only when the *aggregate* health drops (one flaky engine is
expected; ten is a regression).

Run locally::

    python tests/nightly_canary.py
    python tests/nightly_canary.py --engines duckduckgo,reddit,arxiv

Run in CI: see .github/workflows/nightly-canary.yml.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Make the parent project importable when run directly from tests/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_search.cli import _engine_registry, _get_engine
from agent_search.core import BrowserConfig, launch, new_page

log = logging.getLogger(__name__)

# Canary queries per engine — one per. Picked to be uncontroversial,
# popular, and unlikely to return zero results on a healthy SERP.
DEFAULT_CANARY_QUERY = "python"
PER_ENGINE_QUERY: dict[str, str] = {
    # Code / dev — Python is universal.
    "github": "python",
    "github_search": "python",
    "stackoverflow": "python list comprehension",
    "hackernews": "python",
    "npm": "react",
    "npm_search": "react",
    "devto": "javascript",

    # AI / research — high-traffic stable terms.
    "huggingface": "llama",
    "arxiv": "transformer",
    "semanticscholar": "deep learning",

    # Knowledge.
    "wikipedia": "python",
    "wikivoyage": "tokyo",
    "pubmed": "covid-19",
    "wolfram": "1+1",

    # Forums.
    "reddit": "what is python",
    "reddit_subreddit": "Python",
    "quora": "what is python",
    "blackhatworld": "seo",
    "producthunt": "ai",

    # Social.
    "twitter": "openai",
    "x": "openai",
    "instagram": "travel",

    # Chinese platforms.
    "zhihu": "Python",
    "weibo": "AI",
    "xiaohongshu": "美食",
    "douyin": "美食",
    "toutiao": "AI",
    "bilibili": "Python 教程",
    "baidu": "Python",
    "sogou": "Python",
    "so360": "Python",

    # News.
    "bbc": "AI",
    "guardian": "AI",
    "reuters": "AI",
    "apnews": "AI",
    "cnn": "AI",
    "npr": "AI",
    "aljazeera": "AI",
    "techcrunch": "AI",
    "verge": "AI",
    "arstechnica": "AI",

    # Video / streaming.
    "youtube": "python tutorial",
    "twitch": "minecraft",
    "netflix": "stranger things",
    "tiktok": "ai",

    # Audio / podcasts.
    "spotify": "lex fridman",
    "soundcloud": "lofi",
    "apple_podcasts": "lex fridman",
    "xiaoyuzhou": "AI",

    # Movies / books.
    "imdb": "Inception",
    "goodreads": "Dune",

    # E-commerce.
    "amazon": "mechanical keyboard",
    "ebay": "vintage camera",
    "icecat": "iphone 15",
    "steam": "elden ring",

    # Jobs / local.
    "linkedin_jobs": "software engineer",
    "indeed": "software engineer",
    "yelp": "pizza san francisco",
    "ziprecruiter": "software engineer",
    "glassdoor": "google",

    # Patents / security.
    "google_patents": "neural network",
    "virustotal": "google.com",

    # Archive / files.
    "archive_org": "alice in wonderland",
    "torrent_1337x": "ubuntu",

    # Images.
    "unsplash": "mountains",
    "pixabay": "mountains",
    "pexels": "mountains",
    "pinterest": "interior design",

    # Long-form.
    "medium": "rust async",
    "linkedin": "google product manager",

    # Travel / local biz.
    "google_maps": "coffee san francisco",
    "booking": "tokyo",
    "expedia": "kyoto",

    # Financial.
    "yahoo_finance": "apple",

    # Web docs.
    "mdn": "fetch api",

    # Generic search.
    "duckduckgo": "python",
    "google": "python",
    "bing": "python",
    "brave": "python",
    "yandex": "python",
    "qwant": "python",
    "ecosia": "python",
    "startpage": "python",

    # Aliases reuse the same query as their canonical engine.
    "ddg": "python",
    "archive": "alice in wonderland",
    "1337x": "ubuntu",
    "reddit_sub": "Python",
}

DEFAULT_TIMEOUT_S = 90
DEFAULT_LIMIT = 3
DEFAULT_MIN_RESULTS = 1
DEFAULT_PARALLEL = 4


def run_one_engine(engine_name: str, query: str, limit: int, timeout_s: int) -> dict:
    started = time.time()
    try:
        engine_cls = _get_engine(engine_name)
    except ValueError as e:
        return {
            "engine": engine_name,
            "status": "ERR",
            "results": 0,
            "elapsed_s": 0,
            "error": str(e),
        }

    browser = None
    results = []
    error = ""
    try:
        browser = launch(BrowserConfig(headless=True, humanize=True))
        page = new_page(browser)
        instance = engine_cls(page)
        results = instance.search(query, limit=limit) or []
    except Exception as e:
        error = f"{type(e).__name__}: {e}"
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass

    elapsed = round(time.time() - started, 2)
    if error:
        status = "FAIL"
    elif len(results) >= DEFAULT_MIN_RESULTS:
        status = "PASS"
    else:
        status = "EMPTY"
    return {
        "engine": engine_name,
        "status": status,
        "results": len(results),
        "elapsed_s": elapsed,
        "query": query,
        "error": error,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default=None, help="Comma-separated subset (default: all unique)")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--parallel", type=int, default=DEFAULT_PARALLEL)
    ap.add_argument("--report", default="canary_report.json")
    ap.add_argument("--fail-threshold", type=float, default=0.20,
                    help="Exit non-zero when (FAIL+EMPTY)/total exceeds this fraction (default 0.20)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

    reg = _engine_registry()
    if args.engines:
        engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    else:
        # Skip aliases that point at the same module as a canonical name.
        seen_modules: set[tuple[str, str]] = set()
        engines = []
        for name in sorted(reg.keys()):
            spec = reg[name]
            if spec in seen_modules:
                continue
            seen_modules.add(spec)
            engines.append(name)

    print(f"🧪 Canary run: {len(engines)} engines, parallel={args.parallel}, timeout={args.timeout}s/each")
    print()

    rows: list[dict] = []
    started = time.time()
    with ThreadPoolExecutor(max_workers=args.parallel) as ex:
        futs = {
            ex.submit(
                run_one_engine,
                name,
                PER_ENGINE_QUERY.get(name, DEFAULT_CANARY_QUERY),
                args.limit,
                args.timeout,
            ): name
            for name in engines
        }
        for fut in as_completed(futs):
            name = futs[fut]
            try:
                row = fut.result(timeout=args.timeout + 10)
            except Exception as e:
                row = {"engine": name, "status": "FAIL", "results": 0, "elapsed_s": 0, "error": f"timeout/{e}"}
            rows.append(row)
            icon = {"PASS": "✅", "EMPTY": "⚠️ ", "FAIL": "❌", "ERR": "💥"}[row["status"]]
            err_tail = f" — {row['error']}" if row.get("error") else ""
            print(f"  {icon} {name:<22}  {row['results']:>2} hits  {row['elapsed_s']:>6.2f}s{err_tail}")

    rows.sort(key=lambda r: (r["status"] != "PASS", r["engine"]))
    total = len(rows)
    passed = sum(1 for r in rows if r["status"] == "PASS")
    empty = sum(1 for r in rows if r["status"] == "EMPTY")
    failed = sum(1 for r in rows if r["status"] in ("FAIL", "ERR"))
    elapsed = round(time.time() - started, 1)

    summary = {
        "total": total,
        "passed": passed,
        "empty": empty,
        "failed": failed,
        "pass_rate": round(passed / max(total, 1), 3),
        "elapsed_s": elapsed,
        "engines": rows,
    }
    Path(args.report).write_text(json.dumps(summary, indent=2, ensure_ascii=False))

    print()
    print(f"📊 {passed}/{total} PASS · {empty} EMPTY · {failed} FAIL · {elapsed}s wall-clock")
    print(f"📄 Report: {args.report}")

    bad_fraction = (empty + failed) / max(total, 1)
    if bad_fraction > args.fail_threshold:
        print(f"💥 {bad_fraction:.0%} engines unhealthy — exceeds {args.fail_threshold:.0%} threshold")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
