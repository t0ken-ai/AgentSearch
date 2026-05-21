"""Engine canary — run a small smoke search through every adapter.

Each engine is treated as PASS / EMPTY / FAIL based on whether it
returns >= MIN_RESULTS hits within TIMEOUT seconds for a single canary
query. Output is a JSON report and (optionally) a markdown issue body
plus an automatically-filed GitHub issue.

Designed to run **on the user's own machine on a residential IP**, not
on shared CI runners. GitHub Actions runners use Azure datacenter IPs
that are pre-blocked / rate-throttled by Reddit, Cloudflare, and most
anti-bot vendors, which would produce false-positive failures.

CLI entry points::

    agentsearch canary
    agentsearch canary --engines duckduckgo,reddit,arxiv
    agentsearch canary --gh-issue                   # auto file via `gh`
    agentsearch canary --issue-md /tmp/issue.md     # write markdown only

Schedule it via launchd (macOS), systemd-timer (Linux), or cron.
See docs/CANARY.md for ready-made templates.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from .cli import _engine_registry, _get_engine
from .core import BrowserConfig, launch, new_page

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


def _build_issue_markdown(summary: dict) -> tuple[str, str]:
    """Format ``(title, body)`` for a GitHub issue describing the regression.

    The body lists every non-PASS engine plus a short remediation hint.
    Title is short enough to fit GitHub's UI without truncation.
    """
    bad = [r for r in summary["engines"] if r["status"] != "PASS"]
    bad.sort(key=lambda r: (r["status"], r["engine"]))
    title = (
        f"🚨 Canary regression: {summary['failed'] + summary['empty']}/"
        f"{summary['total']} engines unhealthy "
        f"(pass rate {summary['pass_rate'] * 100:.1f}%)"
    )
    lines = [
        f"**Pass rate:** {summary['pass_rate'] * 100:.1f}% "
        f"({summary['passed']} PASS · {summary['empty']} EMPTY · {summary['failed']} FAIL)",
        f"**Wall-clock:** {summary['elapsed_s']}s",
        f"**Host:** {os.uname().sysname} {os.uname().nodename}",
        "",
        "## Unhealthy engines",
        "",
    ]
    for r in bad:
        line = (
            f"- **{r['engine']}** — `{r['status']}` "
            f"({r['results']} hits, {r['elapsed_s']}s, query: `{r['query']}`)"
        )
        if r.get("error"):
            line += f"\n   error: `{r['error']}`"
        lines.append(line)

    lines += [
        "",
        "## How to reproduce locally",
        "",
        "```bash",
        f"agentsearch canary --engines {','.join(r['engine'] for r in bad)}",
        "```",
        "",
        "Or for a single engine with full logs:",
        "",
        "```bash",
        f"agentsearch search '<query>' --engine {bad[0]['engine'] if bad else '<name>'} --limit 3 --json",
        "```",
        "",
        "## Likely causes",
        "",
        "1. The site changed its DOM and our selectors stopped matching → fix the engine adapter.",
        "2. Site is rate-limiting / temporarily blocking the IP → re-run later, possibly with a different network.",
        "3. CloakBrowser update changed fingerprint behavior → check `pip show cloakbrowser` and the engine's stealth args.",
        "",
        "_Filed automatically by `agentsearch canary --gh-issue` — see `docs/CANARY.md`._",
    ]
    return title, "\n".join(lines)


def _file_gh_issue(title: str, body: str, label: str = "canary-regression") -> bool:
    """Use the local `gh` CLI to file (or comment on) a GitHub issue.

    Looks for an existing open issue with the same label first; if found,
    appends the new body as a comment instead of creating a duplicate.
    Returns True on success, False otherwise.
    """
    if not shutil.which("gh"):
        log.warning("[canary] `gh` CLI not found — install + run `gh auth login`, or use --issue-md instead")
        return False

    # Look for an existing open issue.
    try:
        existing = subprocess.run(
            ["gh", "issue", "list", "--label", label, "--state", "open", "--json", "number,title", "--limit", "5"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        items = json.loads(existing.stdout or "[]") if existing.returncode == 0 else []
    except Exception as e:
        log.warning("[canary] gh issue list failed: %s", e)
        items = []

    try:
        if items:
            num = items[0]["number"]
            log.info("[canary] commenting on existing issue #%s", num)
            r = subprocess.run(
                ["gh", "issue", "comment", str(num), "--body", body],
                capture_output=True,
                text=True,
                timeout=30,
            )
        else:
            log.info("[canary] creating new issue")
            r = subprocess.run(
                ["gh", "issue", "create", "--title", title, "--body", body, "--label", label],
                capture_output=True,
                text=True,
                timeout=30,
            )
        if r.returncode != 0:
            log.warning("[canary] gh exited %s: %s", r.returncode, r.stderr.strip())
            return False
        log.info("[canary] gh: %s", (r.stdout or "").strip())
        return True
    except Exception as e:
        log.warning("[canary] gh issue create/comment failed: %s", e)
        return False


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
    ap = argparse.ArgumentParser(prog="agentsearch canary",
                                 description="Run a smoke search through every engine, report regressions")
    ap.add_argument("--engines", default=None, help="Comma-separated subset (default: all unique)")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    ap.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    ap.add_argument("--parallel", type=int, default=DEFAULT_PARALLEL)
    ap.add_argument("--report", default="canary_report.json", help="JSON report path")
    ap.add_argument("--fail-threshold", type=float, default=0.20,
                    help="Exit non-zero (and trigger --gh-issue) when (FAIL+EMPTY)/total exceeds this fraction (default 0.20)")
    ap.add_argument("--issue-md", default=None,
                    help="When the regression threshold trips, also write a markdown issue body to this path")
    ap.add_argument("--gh-issue", action="store_true",
                    help="When the regression threshold trips, file (or comment on) a GitHub issue via the `gh` CLI. Requires `gh auth login`.")
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
    regressed = bad_fraction > args.fail_threshold

    if regressed and (args.issue_md or args.gh_issue):
        title, body = _build_issue_markdown(summary)
        if args.issue_md:
            Path(args.issue_md).write_text(body)
            print(f"📝 Issue markdown: {args.issue_md}")
        if args.gh_issue:
            if _file_gh_issue(title, body):
                print("📨 Filed GitHub issue (or commented on existing one).")
            else:
                print(
                    "⚠️  --gh-issue failed (gh CLI missing or unauthenticated). "
                    "Try: gh auth login, or fall back to --issue-md.",
                    file=sys.stderr,
                )

    if regressed:
        print(f"💥 {bad_fraction:.0%} engines unhealthy — exceeds {args.fail_threshold:.0%} threshold")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
