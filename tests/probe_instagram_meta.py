"""One-off probe: visit a few IG post / reel / profile URLs anonymously and
print every og:* meta tag + page state. Used once to confirm what the post
detail enrichment can reliably extract."""

from __future__ import annotations

import json
import logging
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search import core
from agent_search.core import safe_goto, human_delay


URLS = [
    ("reel_anon",    "https://www.instagram.com/reel/DTfS7SMEk8B/"),
    ("post_anon",    "https://www.instagram.com/p/C5U7ovNPAMJ/"),
    ("profile_anon", "https://www.instagram.com/natgeo/"),
    ("profile_anon2", "https://www.instagram.com/nasa/"),
]


JS_DUMP = r"""
() => {
    const out = {url: location.href, title: document.title};
    const metas = document.querySelectorAll('meta');
    out.meta = [];
    for (const m of metas) {
        const name = m.getAttribute('property') || m.getAttribute('name') || '';
        const content = m.getAttribute('content') || '';
        if (name) out.meta.push({name, content});
    }
    // Detect if we landed on the login wall
    out.has_login_form = !!document.querySelector('form[id="loginForm"], input[name="username"]');
    // First og:description / og:title
    out.body_len = (document.body && document.body.innerText || '').length;
    // Counters
    out.images = document.querySelectorAll('img').length;
    out.post_anchors_p = document.querySelectorAll('a[href^="/p/"]').length;
    out.post_anchors_reel = document.querySelectorAll('a[href^="/reel/"]').length;
    return out;
}
"""


def main():
    logging.basicConfig(level=logging.WARNING)
    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        for label, url in URLS:
            print(f"\n=== {label} :: {url} ===")
            ok = safe_goto(page, url, timeout=25000, retries=1)
            print(f"  goto ok = {ok}")
            human_delay(2.0, 3.5)
            try:
                data = page.evaluate(JS_DUMP)
            except Exception as e:
                print(f"  js err: {e}")
                continue
            print(f"  final url: {data['url']}")
            print(f"  title    : {data['title']}")
            print(f"  body_len : {data['body_len']}")
            print(f"  images   : {data['images']}")
            print(f"  /p/ anchors: {data['post_anchors_p']}, /reel/ anchors: {data['post_anchors_reel']}")
            print(f"  login form: {data['has_login_form']}")
            print("  og:* and twitter:* meta:")
            for m in data["meta"]:
                if m["name"].startswith(("og:", "twitter:", "al:", "description", "theme-color")) and m["content"]:
                    c = m["content"]
                    if len(c) > 240:
                        c = c[:240] + "..."
                    print(f"    {m['name']:<28s} :: {c}")
    finally:
        try:
            browser.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
