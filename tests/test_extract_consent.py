"""Unit test for extract._dismiss_consent_banners.

Drives the helper against three synthetic pages built via
``page.set_content``:

1. **OneTrust shape** — has ``#onetrust-banner-sdk`` wrapping an
   ``#onetrust-accept-btn-handler`` button. We expect the click pass to
   fire on the accept button (so the CMP cookie is set in real-world
   use) AND/OR the JS-nuke pass to remove the wrapper. Either is a
   PASS — production parity is "the banner is gone after the helper
   returns".
2. **No banner** — a vanilla article page. Helper must be a no-op:
   ``clicked == []`` and ``removed == 0``, without raising.
3. **Generic dialog close** — a ``<div role="dialog">`` newsletter
   modal with an ``aria-label="Close"`` button. Verifies the generic
   fallback fires when there's no recognised CMP id.

Run:
    ~/tools/cloakbrowser/venv/bin/python tests/test_extract_consent.py
"""
from __future__ import annotations

import logging
import os
import sys
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search import core
from agent_search.extract import (
    CONSENT_DISMISS_SELECTORS,
    _dismiss_consent_banners,
)


# ---------------------------------------------------------------- fixtures

ONETRUST_HTML = """
<!doctype html>
<html><head><title>OneTrust fixture</title></head>
<body style="overflow: hidden; position: fixed">
  <main>
    <h1 id="real-content">Real Article Content</h1>
    <p>This is the body text the agent should ultimately extract.</p>
  </main>
  <div id="onetrust-consent-sdk">
    <div id="onetrust-banner-sdk" style="position:fixed;bottom:0;left:0;
         right:0;background:#fff;padding:20px;z-index:9999">
      <p>We value your privacy. Please accept all cookies to continue.</p>
      <button id="onetrust-accept-btn-handler"
              style="padding:10px 20px">Accept All Cookies</button>
      <button id="onetrust-reject-all-handler"
              style="padding:10px 20px">Reject All</button>
    </div>
    <div id="onetrust-pc-dark-filter"
         style="position:fixed;inset:0;background:rgba(0,0,0,0.6);z-index:9998"></div>
  </div>
</body></html>
""".strip()


PLAIN_HTML = """
<!doctype html>
<html><head><title>Plain article</title></head>
<body>
  <main>
    <h1>Hello World</h1>
    <p>Nothing to dismiss here.</p>
  </main>
</body></html>
""".strip()


GENERIC_DIALOG_HTML = """
<!doctype html>
<html><head><title>Newsletter modal fixture</title></head>
<body>
  <main>
    <h1 id="real-content">Article body</h1>
    <p>Underneath a "Get the report" newsletter modal.</p>
  </main>
  <div role="dialog" aria-modal="true"
       style="position:fixed;top:30%;left:30%;width:40%;background:#fff;
              padding:24px;border:1px solid #ccc">
    <h2>Get the full report</h2>
    <input type="email" placeholder="you@company.com" />
    <button>Submit</button>
    <button aria-label="Close" style="position:absolute;top:8px;right:8px">×</button>
  </div>
</body></html>
""".strip()


# ---------------------------------------------------------------- helpers

def _load(page, html: str) -> None:
    """Pump synthetic HTML into the live page via a data: URL.

    We tried ``page.set_content(...)`` first, but under CloakBrowser's
    stealth init the freshly-opened page never fires the
    ``domcontentloaded`` lifecycle event for a content-set call (it's
    waiting on a real navigation that never came), so the call hits
    Playwright's 30s timeout. Navigating to a ``data:text/html`` URL
    works because it's a real navigation.
    """
    import urllib.parse
    url = "data:text/html;charset=utf-8," + urllib.parse.quote(html)
    page.goto(url, wait_until="domcontentloaded", timeout=10000)


def _is_present(page, selector: str) -> bool:
    try:
        return page.query_selector(selector) is not None
    except Exception:
        return False


# ---------------------------------------------------------------- cases

def case_onetrust(page) -> int:
    print("[case_onetrust] loading OneTrust-shaped fixture")
    _load(page, ONETRUST_HTML)
    assert _is_present(page, "#onetrust-banner-sdk"), \
        "fixture should start with the banner present"

    result = _dismiss_consent_banners(page)
    print(f"  helper returned: {result}")

    # The banner-root must be gone afterwards — either by click → DOM removal
    # in real CMPs, or by our JS fallback nuking #onetrust-consent-sdk.
    if _is_present(page, "#onetrust-banner-sdk"):
        print("  FAIL: #onetrust-banner-sdk still in DOM after helper")
        return 1
    if _is_present(page, "#onetrust-pc-dark-filter"):
        print("  FAIL: dim overlay still present (would block lazy-load)")
        return 1

    # The real content must survive untouched.
    if not _is_present(page, "#real-content"):
        print("  FAIL: helper nuked real content (over-aggressive selectors)")
        return 1

    # At least one of the two passes must have done something useful.
    if not result["clicked"] and result["removed"] == 0:
        print("  FAIL: neither click nor remove-pass fired on a known CMP")
        return 1

    # Body scroll lock should have been restored.
    body_overflow = page.evaluate(
        "() => document.body && document.body.style.overflow"
    ) or ""
    if body_overflow not in ("", "auto", "visible"):
        print(f"  FAIL: body.style.overflow not restored ({body_overflow!r})")
        return 1

    print("  PASS")
    return 0


def case_plain(page) -> int:
    print("[case_plain] loading vanilla article (no banner)")
    _load(page, PLAIN_HTML)

    result = _dismiss_consent_banners(page)
    print(f"  helper returned: {result}")

    if result["clicked"]:
        print(f"  FAIL: false-positive click on plain page: {result['clicked']}")
        return 1
    if result["removed"] != 0:
        print(f"  FAIL: removed={result['removed']} on plain page")
        return 1
    print("  PASS")
    return 0


def case_generic_dialog(page) -> int:
    print("[case_generic_dialog] loading generic newsletter modal")
    _load(page, GENERIC_DIALOG_HTML)
    assert _is_present(page, "[role='dialog']"), "fixture should have a dialog"

    result = _dismiss_consent_banners(page)
    print(f"  helper returned: {result}")

    # The generic-close fallback should have fired on the aria-label="Close" button.
    if not any("aria-label" in sel and "close" in sel
               for sel in result["clicked"]):
        print("  FAIL: generic close-button fallback did not fire")
        return 1

    # Real content must survive.
    if not _is_present(page, "#real-content"):
        print("  FAIL: helper nuked real content")
        return 1

    print("  PASS")
    return 0


def case_selectors_sanity() -> int:
    """Pure-Python smoke: selector list is non-empty and unique."""
    print("[case_selectors_sanity] inspecting CONSENT_DISMISS_SELECTORS")
    if not CONSENT_DISMISS_SELECTORS:
        print("  FAIL: selector list is empty")
        return 1
    dups = {s for s in CONSENT_DISMISS_SELECTORS
            if CONSENT_DISMISS_SELECTORS.count(s) > 1}
    if dups:
        print(f"  FAIL: duplicate selectors in list: {dups}")
        return 1
    print(f"  PASS ({len(CONSENT_DISMISS_SELECTORS)} unique selectors)")
    return 0


# ---------------------------------------------------------------- runner

def main() -> int:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== extract._dismiss_consent_banners regression ===")

    failures = 0

    # Pure-Python case first — no browser cost if selectors regress.
    try:
        failures += case_selectors_sanity()
    except Exception:
        failures += 1
        traceback.print_exc()

    # Browser-driven cases share one page instance.
    cfg = core.BrowserConfig(headless=True, humanize=False)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        for label, fn in [
            ("onetrust",         case_onetrust),
            ("plain",            case_plain),
            ("generic_dialog",   case_generic_dialog),
        ]:
            print(f"\n--- {label} ---")
            try:
                failures += fn(page)
            except Exception:
                failures += 1
                traceback.print_exc()
    finally:
        try:
            browser.close()
        except Exception:
            pass

    print(f"\n{'PASS' if failures == 0 else f'{failures} FAIL'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
