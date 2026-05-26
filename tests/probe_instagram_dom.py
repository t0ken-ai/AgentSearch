"""Probe what can be extracted from a guest IG post detail page beyond og:*.

Visits a few /p/ and /reel/ URLs, scans:
- All <script type="application/json"> blobs
- Inline <script> blobs containing media keywords (display_url, video_url, etc.)
- <video> element src + poster
- <img> srcset / src
- Any element with data-* attribute carrying CDN URLs

Run:
    ~/tools/cloakbrowser/venv/bin/python tests/probe_instagram_dom.py
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


URLS = [
    ("reel",       "https://www.instagram.com/reel/DTfS7SMEk8B/"),
    ("post_p",     "https://www.instagram.com/p/C5U7ovNPAMJ/"),
    # Pick a known sidecar (multi-image) post — natgeo has plenty
    ("profile",    "https://www.instagram.com/natgeo/"),
]


JS_DUMP = r"""
() => {
    const out = {};
    out.url = location.href;
    out.title = document.title;

    // 1) All <video> elements
    out.videos = Array.from(document.querySelectorAll('video')).map(v => ({
        src: v.src || v.currentSrc || '',
        poster: v.poster || '',
        duration: v.duration || null,
    }));

    // 2) All <img> elements with srcset (limit to avoid huge dumps)
    out.images = Array.from(document.querySelectorAll('img'))
        .slice(0, 20)
        .map(i => ({
            src: i.src || '',
            srcset: i.srcset || '',
            alt: i.alt || '',
            class: i.className || '',
        }))
        .filter(i => i.src && i.src.includes('cdninstagram'));

    // 3) Application/json script tags
    out.json_scripts = [];
    const json_scripts = document.querySelectorAll('script[type="application/json"]');
    for (const s of json_scripts) {
        const txt = s.textContent || '';
        // Look only at scripts containing media keywords
        const interesting = ['display_url', 'video_url', 'video_versions',
                             'image_versions2', 'edge_sidecar_to_children',
                             'edge_media_to_caption', 'shortcode_media',
                             'xdt_shortcode_media', 'xdt_api'];
        for (const kw of interesting) {
            if (txt.includes(kw)) {
                out.json_scripts.push({
                    id: s.id || '',
                    keyword: kw,
                    length: txt.length,
                    preview: txt.slice(0, 400),
                });
                break;
            }
        }
    }

    // 4) Inline scripts (no type) containing media URLs
    out.inline_scripts_with_cdn = [];
    const inline_scripts = document.querySelectorAll('script:not([src])');
    let inline_count = 0;
    for (const s of inline_scripts) {
        const txt = s.textContent || '';
        if (txt.includes('cdninstagram') || txt.includes('display_url')) {
            inline_count++;
            if (out.inline_scripts_with_cdn.length < 3) {
                out.inline_scripts_with_cdn.push({
                    length: txt.length,
                    preview: txt.slice(0, 300),
                });
            }
        }
    }
    out.inline_scripts_with_cdn_total = inline_count;

    // 5) Article element check (post layout)
    out.has_article = !!document.querySelector('article');

    // 6) Total <a> with /p/ or /reel/ on the page
    out.post_anchors = Array.from(document.querySelectorAll('a[href]'))
        .map(a => a.getAttribute('href') || '')
        .filter(h => /^\/(p|reel)\//.test(h));

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
            human_delay(2.5, 4.0)
            try:
                data = page.evaluate(JS_DUMP)
            except Exception as e:
                print(f"  js err: {e}")
                continue
            print(f"  final url: {data['url']}")
            print(f"  title    : {data['title']}")
            print(f"  has_article: {data['has_article']}")
            print(f"  post_anchors: {len(data['post_anchors'])}")

            print(f"\n  videos ({len(data['videos'])}):")
            for v in data['videos']:
                src = v.get('src', '') or ''
                print(f"    src={src[:90]}... poster={v.get('poster', '')[:60]}... duration={v.get('duration')}")

            print(f"\n  cdn images ({len(data['images'])}):")
            for img in data['images'][:5]:
                src = img.get('src', '') or ''
                ss = img.get('srcset', '') or ''
                cls = img.get('class', '') or ''
                print(f"    src={src[:90]}...")
                if ss:
                    # show widths from srcset
                    widths = re.findall(r'(\d+w)', ss)
                    print(f"      srcset widths: {widths}")
                print(f"      alt={img.get('alt', '')[:50]!r} class={cls[:40]!r}")

            print(f"\n  json_scripts hit ({len(data['json_scripts'])}):")
            for s in data['json_scripts']:
                print(f"    id={s.get('id', '')!r} keyword={s.get('keyword')!r} length={s.get('length')}")
                print(f"      preview: {s.get('preview', '')[:200]}")

            print(f"\n  inline_scripts_with_cdn ({data.get('inline_scripts_with_cdn_total', 0)}):")
            for s in data.get('inline_scripts_with_cdn', []):
                print(f"    length={s.get('length')} preview: {s.get('preview', '')[:200]}")
    finally:
        try:
            browser.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
