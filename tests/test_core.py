"""Basic CloakBrowser smoke test for cloak_stealth_suite.core.

Steps:
1. Launch headless browser via core.launch().
2. Visit https://httpbin.org/headers and print the returned headers.
3. Visit https://httpbin.org/user-agent and print the UA.
4. Close the browser.
"""

from __future__ import annotations

import json
import logging
import sys
import traceback

from cloak_stealth_suite import core


def _fetch_json(page, url: str) -> dict:
    """Navigate to a JSON endpoint and parse the body text as JSON."""
    ok = core.safe_goto(page, url, timeout=30000, retries=2)
    if not ok:
        raise RuntimeError(f"navigation failed: {url}")

    # httpbin returns application/json; the browser wraps it in a <pre> tag.
    # innerText of <body> contains exactly the JSON document.
    body = page.evaluate("() => document.body.innerText")
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"non-JSON body from {url}: {body[:200]!r}") from e


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== CloakBrowser core smoke test ===")
    cfg = core.BrowserConfig(headless=True, humanize=False)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)

        print("\n--- /headers ---")
        headers_payload = _fetch_json(page, "https://httpbin.org/headers")
        headers = headers_payload.get("headers", headers_payload)
        for k, v in headers.items():
            print(f"  {k}: {v}")

        print("\n--- /user-agent ---")
        ua_payload = _fetch_json(page, "https://httpbin.org/user-agent")
        ua = ua_payload.get("user-agent", ua_payload)
        print(f"  User-Agent: {ua}")

        print("\n=== PASS ===")
        return 0
    except Exception:
        print("\n=== FAIL ===", file=sys.stderr)
        traceback.print_exc()
        return 1
    finally:
        try:
            browser.close()
        except Exception as e:
            print(f"warning: browser.close() raised: {e}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
