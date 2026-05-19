"""VirusTotal search adapter smoke test.

Searches the EICAR test file MD5 (44d88612fea8a8f36de82e1278abb02f).
EICAR is a known harmless test sample that nearly every AV vendor on
VirusTotal flags, so a successful lookup should report a high detection
ratio (typically 60+/72).

Two test modes:
* If ``VIRUSTOTAL_API_KEY`` (or ``VT_API_KEY``) is set in the environment
  the adapter uses VT API v3 and we expect structured detection data.
* Otherwise the adapter falls back to scraping the SPA. Detection
  parsing through shadow DOM is best-effort, so we accept "page loaded
  on /gui/file/<hash>" as success even if the ratio could not be parsed.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_virustotal.py
"""

from __future__ import annotations

import logging
import os
import sys
import time
import traceback

from cloak_stealth_suite import core
from cloak_stealth_suite.engines.virustotal import VirusTotalEngine
from cloak_stealth_suite.stealth.enhance import check_blocked


QUERY = "44d88612fea8a8f36de82e1278abb02f"  # EICAR test file MD5
LIMIT = 5
MAX_ATTEMPTS = 2


def _attempt(engine: VirusTotalEngine, attempt: int) -> list:
    """Run a single search attempt and dump diagnostics."""
    print(f"\n--- attempt {attempt}/{MAX_ATTEMPTS} ---")
    # Bypass BaseEngine.search()'s retry loop to expose per-attempt state.
    results = engine._do_search(QUERY, LIMIT)

    page = engine.page
    try:
        title = page.title()
    except Exception as e:
        title = f"<title err: {e}>"
    try:
        url = page.url
    except Exception as e:
        url = f"<url err: {e}>"

    print(f"  page title  : {title!r}")
    print(f"  page url    : {url}")
    print(f"  strategy    : {engine.last_strategy or '<none>'}")
    if engine.last_status:
        for k, v in engine.last_status.items():
            print(f"  status.{k:<14}: {v!r}")

    blocked = check_blocked(page)
    if blocked:
        print(f"  check_blocked   : {blocked}")

    print(f"  results         : {len(results)}")
    return results


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    has_api_key = bool(
        os.environ.get("VIRUSTOTAL_API_KEY") or os.environ.get("VT_API_KEY")
    )

    print("=== VirusTotal search adapter test ===")
    print(f"Query      : {QUERY!r}  (EICAR test file MD5)")
    print(f"Limit      : {LIMIT}")
    print(f"Max attempts: {MAX_ATTEMPTS}")
    print(f"API key    : {'<set>' if has_api_key else '<not set, GUI fallback>'}")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = VirusTotalEngine(page)

        results: list = []
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                results = _attempt(engine, attempt)
            except Exception:
                print(f"  attempt {attempt} raised:")
                traceback.print_exc()
                results = []
            if results:
                break
            if attempt < MAX_ATTEMPTS:
                wait = 4 + attempt * 2
                print(f"  no results -- sleeping {wait}s before retry")
                time.sleep(wait)

        if not results:
            print("\n=== FAIL === no results after all attempts", file=sys.stderr)
            return 1

        print(f"\nReturned {len(results)} result(s) (strategy: {engine.last_strategy})")
        print("\n--- Results ---")
        for i, r in enumerate(results, start=1):
            print(f"\n[{i}] {r.title}")
            print(f"    URL          : {r.url}")
            print(f"    Snippet      : {r.snippet}")
            for attr in (
                "entity_type", "api_used", "detections",
                "malicious", "suspicious", "total_scanners", "community_score",
                "file_name", "file_type", "file_size",
                "md5", "sha1", "sha256",
            ):
                if hasattr(r, attr):
                    val = getattr(r, attr)
                    if val not in (None, "", 0):
                        print(f"    {attr:<14}: {val}")
            names = getattr(r, "names", None)
            if names:
                shown = ", ".join(str(n) for n in names[:5])
                if len(names) > 5:
                    shown += f" (+{len(names) - 5} more)"
                print(f"    {'names':<14}: {shown}")

        # ----- PASS criteria --------------------------------------------------
        # Strong: API mode produced structured detection data with malicious
        #         count >= 1 (EICAR should be 60+).
        # Weak  : GUI mode at least landed on /gui/file/<hash> with a non-trivial
        #         body length (proves the page rendered).
        top = results[0]
        api_used = bool(getattr(top, "api_used", False))
        detections = getattr(top, "detections", "") or ""
        malicious = getattr(top, "malicious", None)

        if api_used:
            if not detections or not isinstance(malicious, int) or malicious < 1:
                print(
                    "\n=== FAIL === API mode returned no detection data "
                    f"(detections={detections!r}, malicious={malicious!r})",
                    file=sys.stderr,
                )
                return 1
            print(
                f"\nAPI mode confirmed: {detections} detections "
                f"(malicious={malicious})"
            )
        else:
            url = (top.url or "").lower()
            if "/gui/file/" not in url:
                print(
                    f"\n=== FAIL === GUI did not reach a file page (url={url!r})",
                    file=sys.stderr,
                )
                return 1
            body_len = int(engine.last_status.get("body_len", 0) or 0)
            deep_nodes = int(engine.last_status.get("deep_node_count", 0) or 0)
            parsed_ratio = bool(engine.last_status.get("detections"))

            # VT's SPA renders entirely inside nested shadow DOM, so
            # ``document.body.innerText`` is essentially always empty.
            # Accept *either*: a non-trivial deep DOM node count, OR a
            # parsed detection ratio, OR a populated body — any one is
            # proof the page actually rendered.
            if body_len < 200 and deep_nodes < 500 and not parsed_ratio:
                print(
                    f"\n=== FAIL === GUI did not render "
                    f"(body_len={body_len}, deep_nodes={deep_nodes}, "
                    f"detections={detections!r}) — likely Cloudflare / blocked",
                    file=sys.stderr,
                )
                return 1
            print(
                f"\nGUI fallback confirmed: file page rendered "
                f"(body_len={body_len}, deep_nodes={deep_nodes}, "
                f"detections={detections or '<unparsed>'})"
            )

        print("\n=== PASS ===")
        return 0
    except AssertionError as e:
        print(f"\n=== FAIL === assertion: {e}", file=sys.stderr)
        return 1
    except Exception:
        print("\n=== FAIL === unexpected exception:", file=sys.stderr)
        traceback.print_exc()
        return 1
    finally:
        try:
            browser.close()
        except Exception as e:
            print(f"warning: browser.close() raised: {e}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
