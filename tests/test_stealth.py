"""Comprehensive anti-detection / stealth test suite.

What it covers in a single headless CloakBrowser session:

1. **bot.sannysoft.com** — extracts every row of the `#fp2`/`#main` test
   tables and classifies each cell as ``passed`` / ``failed`` / ``warn`` /
   ``info`` based on the cell's class. Specifically reports the canonical
   detections (WebDriver, WebDriver Advanced, Chrome (New), Chrome (Old),
   Permissions, Plugins Length, Languages, WebGL Vendor, Broken Image
   Dimensions). Saves a full-page screenshot.

2. **CreepJS** (``abrahamjuliot.github.io/creepjs``) — waits ~25s for the
   fingerprint to render, then pulls the trust score, the lies count, the
   FP ID, and the visible bot-signal section. Saves a screenshot.

3. **pixelscan.net** — visits the homepage, waits for the fingerprint
   verdict, extracts the textual verdict ("Your fingerprint looks ..." /
   "You appear to be using ...") and the consistency check rows. Saves a
   screenshot. Cloudflare interstitial / challenge pages are reported as a
   blocked status rather than a hard failure.

4. **Engine smoke tests** — runs one ``search()`` per engine on a fresh
   ``new_page()`` for: Google, Bing, DuckDuckGo, Reddit, Twitter. PASS if
   ``len(results) > 0``; otherwise records the page title / URL /
   ``check_blocked()`` reason for the report.

5. **Final summary** — prints a JSON report to stdout (so the calling
   harness can splice it into PROGRESS.md verbatim), tallies pass/fail per
   section, and lists every artifact path under ``tests/screenshots/stealth/``.

Exit code is 0 if every section ran to completion (even if some sub-checks
flagged the browser); non-zero only on uncaught exceptions, since the
report's own pass/fail flags are the real signal.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import os
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
import time
import traceback
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from agent_search import core
from agent_search.stealth.enhance import apply_stealth, check_blocked
from agent_search.engines.google import GoogleEngine
from agent_search.engines.bing import BingEngine
from agent_search.engines.duckduckgo import DuckDuckGoEngine
from agent_search.engines.reddit import RedditEngine
from agent_search.engines.twitter import TwitterEngine


ROOT = Path(__file__).resolve().parent
SHOT_DIR = ROOT / "screenshots" / "stealth"

# Fingerprint sites take time to fully execute their JS; give them headroom.
SANNY_WAIT_MS = 8_000      # tables fill within ~3s but late tests trickle in
CREEPJS_WAIT_MS = 25_000   # CreepJS scoring loop is heavy
PIXELSCAN_WAIT_MS = 18_000 # consistency checks run async after page load

GOTO_TIMEOUT_MS = 45_000


@dataclass
class SectionResult:
    name: str
    status: str = "PENDING"          # PASS / FAIL / BLOCKED / ERROR
    details: dict[str, Any] = field(default_factory=dict)
    screenshot: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _save_screenshot(page, name: str) -> str | None:
    """Take a full-page screenshot; return the relative path or None."""
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    path = SHOT_DIR / f"{name}.png"
    try:
        page.screenshot(path=str(path), full_page=True)
        return str(path.relative_to(ROOT.parent))
    except Exception as e:
        logging.warning("screenshot %s failed: %s", name, e)
        try:
            # fall back to viewport-only screenshot
            page.screenshot(path=str(path))
            return str(path.relative_to(ROOT.parent))
        except Exception as e2:
            logging.warning("viewport screenshot %s also failed: %s", name, e2)
            return None


def _safe_text(page, selector: str, default: str = "") -> str:
    try:
        el = page.query_selector(selector)
        if el:
            return (el.inner_text() or "").strip()
    except Exception:
        pass
    return default


# ---------------------------------------------------------------------------
# 1) bot.sannysoft.com
# ---------------------------------------------------------------------------


# Canonical detections we want to report on by name. The label text on
# sannysoft is the first <td> of each row; we match case-insensitively and
# allow a substring so "WebDriver Advanced" / "Webdriver(New)" both match.
SANNY_KEY_CHECKS = [
    "WebDriver",
    "WebDriver Advanced",
    "Chrome (New)",
    "Chrome (Old)",
    "Permissions",
    "Plugins Length",
    "Plugins is of type PluginArray",
    "Languages",
    "WebGL Vendor",
    "WebGL Renderer",
    "Broken Image Dimensions",
    "User Agent (Old)",
    "User Agent (New)",
    "Hairline Feature",
]


def run_sannysoft(browser) -> SectionResult:
    res = SectionResult(name="bot.sannysoft.com")
    page = core.new_page(browser)
    apply_stealth(page)
    try:
        ok = core.safe_goto(page, "https://bot.sannysoft.com/", timeout=GOTO_TIMEOUT_MS, retries=2)
        if not ok:
            res.status = "ERROR"
            res.error = "navigation failed"
            return res
        page.wait_for_timeout(SANNY_WAIT_MS)

        # Extract every row from every test table on the page. Sannysoft uses
        # td.passed / td.failed / td.warn classes. We capture (label, value, klass).
        rows: list[dict[str, str]] = page.evaluate(
            """() => {
              const out = [];
              const trs = document.querySelectorAll("table tr");
              trs.forEach(tr => {
                const tds = tr.querySelectorAll("td");
                if (tds.length < 2) return;
                const label = (tds[0].innerText || "").trim();
                if (!label) return;
                // last cell is the verdict cell on sannysoft
                const verdict = tds[tds.length - 1];
                const value = (verdict.innerText || "").trim();
                const klass = (verdict.className || "").trim();
                out.push({ label, value, klass });
              });
              return out;
            }"""
        )

        # Classify each row.
        passed = 0
        failed = 0
        warned = 0
        infos = 0
        by_label: dict[str, dict[str, str]] = {}
        for row in rows:
            klass = row["klass"].lower()
            label = row["label"]
            if "passed" in klass:
                passed += 1
            elif "failed" in klass:
                failed += 1
            elif "warn" in klass:
                warned += 1
            else:
                infos += 1
            # First occurrence wins (sannysoft has duplicate label rows in
            # different tables; the first table is the canonical "old tests").
            by_label.setdefault(label, row)

        # Resolve canonical checks.
        key_results: list[dict[str, str]] = []
        for want in SANNY_KEY_CHECKS:
            match = None
            for label, row in by_label.items():
                if want.lower() in label.lower():
                    match = row
                    break
            if match:
                key_results.append({
                    "check": want,
                    "matched_label": match["label"],
                    "verdict": match["value"],
                    "klass": match["klass"],
                })
            else:
                key_results.append({
                    "check": want,
                    "matched_label": None,
                    "verdict": "<not found>",
                    "klass": "",
                })

        # Status = PASS if no row is marked "failed"; if WebDriver-related
        # rows are failed → FAIL. Otherwise PASS-with-warnings.
        critical_failed = [
            r for r in key_results
            if "failed" in (r["klass"] or "").lower()
            and any(k in r["check"].lower() for k in ["webdriver", "chrome", "permissions"])
        ]
        if critical_failed:
            res.status = "FAIL"
        elif failed == 0:
            res.status = "PASS"
        else:
            res.status = "WARN"

        res.details = {
            "url": page.url,
            "title": page.title(),
            "totals": {"passed": passed, "failed": failed, "warn": warned, "info": infos, "rows": len(rows)},
            "key_checks": key_results,
            "critical_failed": [r["check"] for r in critical_failed],
            "blocked": check_blocked(page),
        }
        res.screenshot = _save_screenshot(page, "sannysoft")
    except Exception:
        res.status = "ERROR"
        res.error = traceback.format_exc()
    finally:
        try:
            page.close()
        except Exception:
            pass
    return res


# ---------------------------------------------------------------------------
# 2) CreepJS
# ---------------------------------------------------------------------------


def run_creepjs(browser) -> SectionResult:
    res = SectionResult(name="CreepJS")
    page = core.new_page(browser)
    apply_stealth(page)
    try:
        ok = core.safe_goto(
            page,
            "https://abrahamjuliot.github.io/creepjs/",
            timeout=GOTO_TIMEOUT_MS,
            retries=2,
        )
        if not ok:
            res.status = "ERROR"
            res.error = "navigation failed"
            return res

        # CreepJS renders the score lazily; wait for the trust-score block
        # to appear, fall back to a fixed sleep if the selector never lands.
        try:
            page.wait_for_selector("#fingerprint-data", timeout=CREEPJS_WAIT_MS)
        except Exception:
            page.wait_for_timeout(CREEPJS_WAIT_MS)
        # extra settle time for the late "lies" / "trust" panels
        page.wait_for_timeout(5_000)

        data = page.evaluate(
            """() => {
              const out = {};
              const grab = (sel) => {
                const el = document.querySelector(sel);
                return el ? (el.innerText || "").trim() : null;
              };
              // Trust score header — it lives in different DOM positions
              // across CreepJS versions, so try several.
              out.trust = grab(".unblurred .pb-md-3, .pb-md-3, .trust-score, [class*='trust']");
              // Lies / Bot signals counters near the top of the report.
              out.lies = grab(".lies-section, [class*='lies']");
              out.bot = grab(".bot-section, [class*='bot']");
              // Fingerprint id (hash) — usually inside the "FP ID" header.
              const fpHeader = Array.from(document.querySelectorAll("strong, .fingerprint-header, span, div"))
                .find(el => /fp[\\s-]*id|fingerprint id/i.test(el.innerText || ""));
              out.fp_id = fpHeader ? (fpHeader.parentElement?.innerText || fpHeader.innerText).trim() : null;

              // Capture the entire #fingerprint-data text so we have a full record.
              const fp = document.querySelector("#fingerprint-data");
              out.full_text = fp ? (fp.innerText || "").trim() : "";
              out.full_text_truncated = out.full_text.slice(0, 4000);
              return out;
            }"""
        )

        # crude scoring extraction (best-effort)
        full = (data.get("full_text") or "").lower()
        # CreepJS marks lies like "5 lies" and trust like "trust score: 60%"
        import re

        score_match = re.search(r"trust\s*score[^0-9%]*([0-9.]+)\s*%", full)
        lies_match = re.search(r"(\d+)\s+lie[s]?", full)
        bot_match = re.search(r"(\d+)\s+bot\s*signal", full)

        res.details = {
            "url": page.url,
            "title": page.title(),
            "trust_score_pct": float(score_match.group(1)) if score_match else None,
            "lies_count": int(lies_match.group(1)) if lies_match else None,
            "bot_signals": int(bot_match.group(1)) if bot_match else None,
            "raw": {
                "trust": data.get("trust"),
                "lies": data.get("lies"),
                "bot": data.get("bot"),
                "fp_id": data.get("fp_id"),
            },
            "blocked": check_blocked(page),
            "full_text_truncated": data.get("full_text_truncated"),
        }
        # Heuristic verdict:
        # - PASS  : trust >= 60 AND lies <= 5 AND bot_signals <= 2
        # - WARN  : everything else with a score recovered
        # - BLOCKED if check_blocked()
        if res.details["blocked"]:
            res.status = "BLOCKED"
        elif res.details["trust_score_pct"] is not None:
            ts = res.details["trust_score_pct"]
            ls = res.details["lies_count"] or 0
            bs = res.details["bot_signals"] or 0
            if ts >= 60 and ls <= 5 and bs <= 2:
                res.status = "PASS"
            else:
                res.status = "WARN"
        elif data.get("full_text"):
            # Page rendered but we couldn't parse the score — record as WARN.
            res.status = "WARN"
        else:
            res.status = "FAIL"

        res.screenshot = _save_screenshot(page, "creepjs")
    except Exception:
        res.status = "ERROR"
        res.error = traceback.format_exc()
    finally:
        try:
            page.close()
        except Exception:
            pass
    return res


# ---------------------------------------------------------------------------
# 3) pixelscan.net
# ---------------------------------------------------------------------------


def run_pixelscan(browser) -> SectionResult:
    res = SectionResult(name="pixelscan.net")
    page = core.new_page(browser)
    apply_stealth(page)
    try:
        ok = core.safe_goto(
            page,
            "https://pixelscan.net/",
            timeout=GOTO_TIMEOUT_MS,
            retries=2,
        )
        if not ok:
            res.status = "ERROR"
            res.error = "navigation failed"
            return res

        page.wait_for_timeout(PIXELSCAN_WAIT_MS)

        info = page.evaluate(
            """() => {
              const text = (document.body.innerText || "").trim();
              // Pixelscan splashes a header verdict like
              //   "Your fingerprint looks legit"
              //   "You appear to be using automation tools"
              //   "Looks like you're using a proxy/VPN"
              // and a list of consistency checks. Capture the first ~6kb of
              // body text for inspection plus any element with role/heading.
              const headings = Array.from(document.querySelectorAll("h1,h2,h3"))
                .map(h => (h.innerText || "").trim())
                .filter(Boolean);
              return {
                title: document.title,
                url: location.href,
                body: text.slice(0, 6000),
                full_len: text.length,
                headings,
              };
            }"""
        )

        body_lower = (info.get("body") or "").lower()
        verdict = "unknown"
        bot_flag = None
        if "automation" in body_lower or "bot" in body_lower and "you" in body_lower:
            # try to find a more specific phrase
            if "looks like you" in body_lower or "you appear to" in body_lower or "you are using" in body_lower:
                # capture the surrounding phrase
                idx = max(
                    body_lower.find("looks like you"),
                    body_lower.find("you appear to"),
                    body_lower.find("you are using"),
                )
                if idx >= 0:
                    verdict = info["body"][idx:idx + 200].split("\n")[0].strip()
                else:
                    verdict = "bot/automation phrase present"
                bot_flag = True
            else:
                bot_flag = None
        if "looks legit" in body_lower or "fingerprint looks" in body_lower:
            verdict = "fingerprint looks legit"
            bot_flag = False

        # Cloudflare gate?
        cf_blocked = (
            "checking your browser" in body_lower
            or "verify you are human" in body_lower
            or "just a moment" in (info.get("title") or "").lower()
        )

        res.details = {
            "url": info.get("url"),
            "title": info.get("title"),
            "verdict": verdict,
            "bot_flag": bot_flag,
            "cloudflare_blocked": cf_blocked,
            "headings": info.get("headings"),
            "body_len": info.get("full_len"),
            "body_truncated": info.get("body"),
            "blocked": check_blocked(page),
        }

        if cf_blocked:
            res.status = "BLOCKED"
        elif bot_flag is False:
            res.status = "PASS"
        elif bot_flag is True:
            res.status = "FAIL"
        else:
            res.status = "WARN"

        res.screenshot = _save_screenshot(page, "pixelscan")
    except Exception:
        res.status = "ERROR"
        res.error = traceback.format_exc()
    finally:
        try:
            page.close()
        except Exception:
            pass
    return res


# ---------------------------------------------------------------------------
# 4) engine smoke tests
# ---------------------------------------------------------------------------


ENGINE_SPECS = [
    ("google", GoogleEngine, "open source software"),
    ("bing", BingEngine, "open source software"),
    ("duckduckgo", DuckDuckGoEngine, "open source software"),
    ("reddit", RedditEngine, "Python"),
    ("twitter", TwitterEngine, "open source"),
]


def run_engine(browser, name: str, EngineCls, query: str) -> SectionResult:
    res = SectionResult(name=f"engine:{name}")
    page = core.new_page(browser)
    try:
        engine = EngineCls(page)  # apply_stealth runs in BaseEngine.__init__
        t0 = time.time()
        results = engine.search(query, limit=5)
        elapsed = time.time() - t0

        title = ""
        url = ""
        try:
            title = page.title()
        except Exception:
            pass
        try:
            url = page.url
        except Exception:
            pass

        block_reason = check_blocked(page)
        res.details = {
            "query": query,
            "results_count": len(results),
            "elapsed_s": round(elapsed, 2),
            "page_title": title,
            "page_url": url,
            "blocked": block_reason,
            "top": [
                {"title": r.title, "url": r.url, "snippet": (r.snippet or "")[:200]}
                for r in results[:3]
            ],
        }
        res.screenshot = _save_screenshot(page, f"engine_{name}")
        res.status = "PASS" if results else ("BLOCKED" if block_reason else "FAIL")
    except Exception:
        res.status = "ERROR"
        res.error = traceback.format_exc()
    finally:
        try:
            page.close()
        except Exception:
            pass
    return res


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _print_section(res: SectionResult) -> None:
    print(f"\n--- [{res.status}] {res.name} ---")
    if res.error:
        print(res.error)
    if res.screenshot:
        print(f"  screenshot: {res.screenshot}")
    print("  details:")
    pretty = json.dumps(res.details, indent=2, ensure_ascii=False, default=str)
    for line in pretty.splitlines():
        print(f"    {line}")


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    print("=== CloakBrowser comprehensive stealth test ===")
    print(f"Started: {_dt.datetime.now().isoformat(timespec='seconds')}")
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Screenshots dir: {SHOT_DIR}")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)

    sections: list[SectionResult] = []
    try:
        # Detection probes first (each on its own page, but shared browser context).
        sections.append(run_sannysoft(browser))
        _print_section(sections[-1])
        sections.append(run_creepjs(browser))
        _print_section(sections[-1])
        sections.append(run_pixelscan(browser))
        _print_section(sections[-1])

        # Engine smoke tests.
        for name, cls, query in ENGINE_SPECS:
            sections.append(run_engine(browser, name, cls, query))
            _print_section(sections[-1])
    finally:
        try:
            browser.close()
        except Exception as e:
            print(f"warning: browser.close() raised: {e}", file=sys.stderr)

    # ---------- summary ----------
    print("\n\n=== SUMMARY ===")
    counts: dict[str, int] = {}
    for s in sections:
        counts[s.status] = counts.get(s.status, 0) + 1
    for s in sections:
        marker = {"PASS": "✅", "FAIL": "❌", "BLOCKED": "🛑",
                  "WARN": "⚠️", "ERROR": "💥"}.get(s.status, "❓")
        print(f"  {marker} [{s.status:7s}] {s.name}")
    print("\n  totals:", " ".join(f"{k}={v}" for k, v in sorted(counts.items())))

    # Machine-readable block (used by the report writer).
    print("\n--- JSON_REPORT_START ---")
    print(json.dumps(
        {
            "started": _dt.datetime.now().isoformat(timespec="seconds"),
            "sections": [asdict(s) for s in sections],
            "counts": counts,
            "screenshot_dir": str(SHOT_DIR),
        },
        indent=2,
        ensure_ascii=False,
        default=str,
    ))
    print("--- JSON_REPORT_END ---")

    # We exit 0 unless a section actually crashed (ERROR) — pass/fail of the
    # individual checks is informational.
    had_error = any(s.status == "ERROR" for s in sections)
    return 1 if had_error else 0


if __name__ == "__main__":
    sys.exit(main())
