"""Dump one IG post page's JSON Relay blob and walk to find media URLs.

We just want to map:
- where in the Relay payload is the media node?
- what are the keys for image_versions2 and video_versions?
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search import core
from agent_search.core import safe_goto, human_delay


URL = "https://www.instagram.com/reel/DTfS7SMEk8B/"


def _walk(obj, hits, path=()):
    """Walk a JSON tree collecting paths to keys we care about."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in ("video_versions", "image_versions2", "carousel_media",
                    "edge_sidecar_to_children", "edge_media_to_caption",
                    "xdt_shortcode_media", "shortcode_media", "code"):
                hits.append((path + (k,), v if not isinstance(v, (dict, list)) else f"<{type(v).__name__}>"))
            _walk(v, hits, path + (k,))
    elif isinstance(obj, list):
        for i, item in enumerate(obj[:3]):  # only first 3 of any list
            _walk(item, hits, path + (i,))


def main():
    logging.basicConfig(level=logging.WARNING)
    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        if not safe_goto(page, URL, timeout=25000, retries=1):
            print("goto failed")
            return
        human_delay(2.5, 4.0)

        # Grab all JSON script blobs containing video_versions
        scripts = page.evaluate(
            """
            () => Array.from(document.querySelectorAll('script[type="application/json"]'))
                .map(s => s.textContent || '')
                .filter(t => t.includes('video_versions') || t.includes('xdt_shortcode_media') || t.includes('xdt_api__v1__media__shortcode__web_info'))
            """
        ) or []
        print(f"found {len(scripts)} candidate scripts")

        for idx, txt in enumerate(scripts):
            print(f"\n--- script {idx}: length={len(txt)} ---")
            try:
                data = json.loads(txt)
            except Exception as e:
                print(f"  json parse failed: {e}")
                continue

            hits: list = []
            _walk(data, hits)
            print(f"  found {len(hits)} interesting keys")
            for path, val in hits[:30]:
                p = " -> ".join(str(x) for x in path)
                v = val if isinstance(val, str) else str(val)
                if len(v) > 100:
                    v = v[:100] + "..."
                print(f"    {p} = {v}")

            # Also: regex-grep for any cdninstagram URL in raw text
            urls = re.findall(r'https?://[a-z0-9._-]+\.cdninstagram\.com/[^\s"\'\\]+', txt)
            print(f"\n  cdninstagram URLs in script: {len(urls)}")
            for u in urls[:5]:
                print(f"    {u[:140]}")

    finally:
        try:
            browser.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
