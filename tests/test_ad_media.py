"""AdMediaDownloader regression tests.

Layered:

1. **offline helpers** — ext detection from URL / Content-Type, filename
   sanitization, URL extraction from each engine's result shape.
2. **offline single-download stub** — uses a tiny inline HTTP server
   (built-in ``http.server``) so no network needed.
3. **live image download** — fetches a real Google ATC simgad image
   that we proved live earlier in the session. Counts as PASS when
   the image lands on disk and is non-empty.

Run::

    ~/tools/cloakbrowser/venv/bin/python tests/test_ad_media.py
"""
from __future__ import annotations

import http.server
import os
import shutil
import socketserver
import sys
import tempfile
import threading
import time

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search.engines._ad_media import (
    AdMediaDownloader, DownloadResult,
    detect_ext_from_url, detect_ext_from_content_type,
    _safe_token,
)
from agent_search.engines.base import SearchResult


# ── 1. offline helpers ──────────────────────────────────────────────


def t_quality_score() -> int:
    """Higher resolution / larger asset should score higher."""
    q = AdMediaDownloader._quality_score
    fail = 0
    if not (q("https://v/x.mp4", "video") > q("https://i/x.jpg", "image")):
        print("  FAIL: video should outrank image"); fail += 1
    if not (q("https://i/x.jpg", "image") > q("https://t/x.jpg", "thumbnail")):
        print("  FAIL: image should outrank thumbnail"); fail += 1
    if not (q("https://v/1080p.mp4", "video") > q("https://v/720p.mp4", "video")):
        print("  FAIL: 1080p should outrank 720p"); fail += 1
    if not (q("https://v/720p.mp4", "video") > q("https://v/540p.mp4", "video")):
        print("  FAIL: 720p should outrank 540p"); fail += 1
    # Penalty paths
    if not (q("https://x/full.png", "image") > q("https://x/thumb_full.png", "image")):
        print("  FAIL: thumb_ path should be penalised"); fail += 1
    if fail == 0:
        print("  PASS: quality score ordering")
    return fail


def t_extract_urls_ranked() -> int:
    """A video at 720p should outrank both images on the same record."""
    r = SearchResult(title="x", url="x", snippet="")
    r.__dict__.update({
        "ad_archive_id": "A1",
        "image_urls": ["https://i/full.png", "https://i/thumb_small.png"],
        "video_url": "https://v/720p.mp4",
    })
    urls = AdMediaDownloader._extract_urls(r)
    if not urls or urls[0][0] != "https://v/720p.mp4":
        print(f"  FAIL: expected video first, got {urls}")
        return 1
    # Image full should outrank image thumb
    image_urls = [u for u, k in urls if k == "image"]
    if image_urls != ["https://i/full.png", "https://i/thumb_small.png"]:
        print(f"  FAIL: image order wrong: {image_urls}")
        return 1
    print(f"  PASS: extract_urls ranks by quality "
          f"(top: {urls[0][0]})")
    return 0


def t_ext_from_url() -> int:
    fail = 0
    cases = [
        ("https://x.com/foo.jpg", ".jpg"),
        ("https://x.com/foo.JPG?token=abc", ".jpg"),
        ("https://x.com/path/to/video.mp4", ".mp4"),
        ("https://x.com/no-ext", None),
        ("", None),
    ]
    for u, expected in cases:
        got = detect_ext_from_url(u)
        if got != expected:
            print(f"  FAIL {u!r} -> {got!r}, expected {expected!r}")
            fail += 1
    if fail == 0:
        print("  PASS: detect_ext_from_url")
    return fail


def t_ext_from_content_type() -> int:
    fail = 0
    cases = [
        ("image/jpeg", ".jpg"),
        ("image/jpeg; charset=utf-8", ".jpg"),
        ("video/mp4", ".mp4"),
        ("application/x-mpegURL", ".m3u8"),
        ("garbage", None),
        ("", None),
        (None, None),
    ]
    for ct, expected in cases:
        got = detect_ext_from_content_type(ct)
        if got != expected:
            print(f"  FAIL {ct!r} -> {got!r}, expected {expected!r}")
            fail += 1
    if fail == 0:
        print("  PASS: detect_ext_from_content_type")
    return fail


def t_safe_token() -> int:
    fail = 0
    cases = [
        ("AR0126/foo", "AR0126_foo"),
        ("hello world!", "hello_world_"),
        ("", "x"),
        ("a" * 100, "a" * 80),  # truncated at 80
    ]
    for s, expected in cases:
        got = _safe_token(s)
        if got != expected:
            print(f"  FAIL {s!r} -> {got!r}, expected {expected!r}")
            fail += 1
    if fail == 0:
        print("  PASS: _safe_token")
    return fail


def t_extract_urls_meta() -> int:
    """SearchResult from meta_ad_library shape."""
    r = SearchResult(title="x", url="x", snippet="")
    r.__dict__.update({
        "ad_archive_id": "777",
        "page_name": "Brand",
        "image_urls": ["https://i/1.jpg", "https://i/2.jpg"],
        "video_url": "https://v/720p.mp4",
        "video_urls": ["https://v/720p.mp4", "https://v/sd.mp4"],
    })
    urls = AdMediaDownloader._extract_urls(r)
    just_urls = [u for u, _ in urls]
    fail = 0
    if "https://i/1.jpg" not in just_urls or "https://v/720p.mp4" not in just_urls:
        print(f"  FAIL extract_urls(meta): {urls}")
        fail += 1
    # No duplicates even though video_urls and video_url overlap.
    if len(set(just_urls)) != len(just_urls):
        print(f"  FAIL: duplicates remain: {just_urls}")
        fail += 1
    if fail == 0:
        print("  PASS: extract_urls(meta)")
    return fail


def t_extract_urls_tiktok_cc() -> int:
    """video_urls is a dict of {res: url} for TikTok CC."""
    r = SearchResult(title="x", url="x", snippet="")
    r.__dict__.update({
        "ad_id": "TT-1",
        "brand_name": "Acme",
        "video_url": "https://v/720p",
        "video_urls": {
            "1080p": "https://v/1080",
            "720p":  "https://v/720p",
            "540p":  "https://v/540",
            "480p":  "https://v/480",
            "360p":  "https://v/360",
        },
        "cover_image_url": "https://c/cover.jpg",
    })
    urls = AdMediaDownloader._extract_urls(r)
    just_urls = [u for u, _ in urls]
    # 1080 should come first in preferred order
    if just_urls[0] != "https://v/1080":
        print(f"  FAIL: 1080p should be first, got order: {just_urls}")
        return 1
    if "https://c/cover.jpg" not in just_urls:
        print("  FAIL: cover_image_url missing")
        return 1
    print("  PASS: extract_urls(tiktok_cc)")
    return 0


def t_extract_urls_google() -> int:
    """Google creative_detail: image_url + video_url + youtube id."""
    r = SearchResult(title="x", url="x", snippet="")
    r.__dict__.update({
        "advertiser_id": "AR1",
        "creative_id": "CR1",
        "image_url": "https://tpc.googlesyndication.com/archive/simgad/abc",
        "video_url": "",
        "preview_url": "https://displayads-formats.googleusercontent.com/ads/preview/x",
    })
    urls = AdMediaDownloader._extract_urls(r)
    just = [u for u, _ in urls]
    if "https://tpc.googlesyndication.com/archive/simgad/abc" not in just:
        print(f"  FAIL: simgad image missing: {just}")
        return 1
    print("  PASS: extract_urls(google)")
    return 0


def t_platform_id_inference() -> int:
    fail = 0
    cases = [
        ({"ad_archive_id": "1"}, ("meta", "1")),
        ({"creative_id": "CR", "advertiser_id": "AR"}, ("google_atc", "CR")),
        ({"ad_id": "TT", "brand_name": "X"}, ("tiktok_cc", "TT")),
        ({"ad_id": "L1", "advertiser_name": "Y"}, ("tiktok_lib", "L1")),
        ({"platform": "instagram", "ad_archive_id": "9"},
         ("instagram", "9")),
    ]
    for d, expected in cases:
        got = AdMediaDownloader._platform_and_id(d)
        if got != expected:
            print(f"  FAIL platform_and_id({d}) -> {got}, expected {expected}")
            fail += 1
    if fail == 0:
        print("  PASS: platform_and_id inference")
    return fail


# ── 2. offline single-download with localhost server ────────────────


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_PNG_BODY = _PNG_MAGIC + b"\x00" * 256


class _LocalHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silence

    def do_GET(self):
        if self.path.startswith("/img"):
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(_PNG_BODY)))
            self.end_headers()
            self.wfile.write(_PNG_BODY)
        elif self.path.startswith("/404"):
            self.send_response(404)
            self.end_headers()
        elif self.path.startswith("/no-ext"):
            self.send_response(200)
            self.send_header("Content-Type", "video/mp4")
            self.end_headers()
            self.wfile.write(b"\x00\x00\x00\x18ftypmp42")
        else:
            self.send_response(404)
            self.end_headers()


def _start_local_server() -> tuple[socketserver.TCPServer, int]:
    srv = socketserver.TCPServer(("127.0.0.1", 0), _LocalHandler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, port


def t_offline_download() -> int:
    srv, port = _start_local_server()
    tmpdir = tempfile.mkdtemp(prefix="adm-")
    try:
        dl = AdMediaDownloader(tmpdir, timeout=5, max_retries=0)
        # 1. PNG with explicit ext in URL.
        r1 = dl.download_one(f"http://127.0.0.1:{port}/img.png",
                             kind="image", ad_id="A1", platform="meta")
        # 2. Content-Type fallback (URL has no ext).
        r2 = dl.download_one(f"http://127.0.0.1:{port}/no-ext",
                             kind="video", ad_id="A2", platform="meta")
        # 3. 404 → success=False, no crash.
        r3 = dl.download_one(f"http://127.0.0.1:{port}/404",
                             kind="image", ad_id="A3", platform="meta")
        # 4. invalid url
        r4 = dl.download_one("not-a-url", kind="image")

        fail = 0
        if not r1.success or r1.file_size != len(_PNG_BODY):
            print(f"  FAIL r1: {r1}")
            fail += 1
        if not r1.local_path.endswith(".png"):
            print(f"  FAIL r1 ext: {r1.local_path}")
            fail += 1
        if not r2.success or not r2.local_path.endswith(".mp4"):
            print(f"  FAIL r2 ext: {r2}")
            fail += 1
        if r3.success or "404" not in (r3.error or ""):
            print(f"  FAIL r3: {r3}")
            fail += 1
        if r4.success or "invalid url" not in (r4.error or ""):
            print(f"  FAIL r4: {r4}")
            fail += 1
        if fail == 0:
            print("  PASS: offline single download (4 cases)")
        return fail
    finally:
        srv.shutdown()
        srv.server_close()
        shutil.rmtree(tmpdir, ignore_errors=True)


def t_offline_record_batch() -> int:
    srv, port = _start_local_server()
    tmpdir = tempfile.mkdtemp(prefix="adm-")
    try:
        dl = AdMediaDownloader(tmpdir, timeout=5, max_retries=0)
        # Build 3 fake records with multi-URL payloads.
        records = []
        for i in range(3):
            r = SearchResult(title=f"a{i}", url="x", snippet="")
            r.__dict__.update({
                "ad_archive_id": f"A{i}",
                "page_name": "Brand",
                "image_urls": [
                    f"http://127.0.0.1:{port}/img-{i}-a.png",
                    f"http://127.0.0.1:{port}/img-{i}-b.png",
                ],
                "video_url": f"http://127.0.0.1:{port}/img-{i}-vid.png",  # mock as image
            })
            records.append(r)

        results = dl.download_many(records, max_workers=4)
        # 3 records × 3 urls = 9 results.
        if len(results) != 9:
            print(f"  FAIL: expected 9 results, got {len(results)}")
            return 1
        ok = sum(1 for r in results if r.success)
        if ok != 9:
            failed_summary = [(r.url, r.error) for r in results if not r.success]
            print(f"  FAIL: only {ok}/9 succeeded; failures: {failed_summary}")
            return 1
        # Filenames respect platform_adid prefix.
        names = [os.path.basename(r.local_path) for r in results if r.local_path]
        if not all(n.startswith("meta_A") for n in names):
            print(f"  FAIL: filename prefix wrong, sample: {names[:3]}")
            return 1
        print(f"  PASS: batch download ({ok}/9, prefix sample: {names[0]})")
        return 0
    finally:
        srv.shutdown()
        srv.server_close()
        shutil.rmtree(tmpdir, ignore_errors=True)


# ── 3. live download (Google ATC simgad image) ───────────────────────


def t_live_simgad() -> int:
    """Hit a real Google CDN image. Doesn't need a proxy or auth."""
    proxy_url = os.environ.get("FLUXISP_PROXY")
    tmpdir = tempfile.mkdtemp(prefix="adm-live-")
    try:
        dl = AdMediaDownloader(tmpdir, timeout=20, max_retries=1,
                               proxy_url=proxy_url)
        url = ("https://tpc.googlesyndication.com/archive/simgad/"
               "9084352035427252114")
        t0 = time.time()
        r = dl.download_one(url, kind="image",
                            ad_id="CR0210", platform="google_atc")
        if not r.success or not r.file_size:
            print(f"  FAIL: download failed in {time.time()-t0:.1f}s — {r.error}")
            return 1
        print(f"  PASS: simgad image fetched in {time.time()-t0:.1f}s "
              f"({r.file_size} bytes, {r.content_type}, {r.local_path})")
        return 0
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main() -> int:
    print("=== test_ad_media ===")
    failures = 0
    for label, fn in [
        # offline
        ("ext_from_url",         t_ext_from_url),
        ("ext_from_content",     t_ext_from_content_type),
        ("safe_token",           t_safe_token),
        ("quality_score",        t_quality_score),
        ("extract_urls_ranked",  t_extract_urls_ranked),
        ("extract_urls_meta",    t_extract_urls_meta),
        ("extract_urls_tiktok",  t_extract_urls_tiktok_cc),
        ("extract_urls_google",  t_extract_urls_google),
        ("platform_id",          t_platform_id_inference),
        ("offline_dl_single",    t_offline_download),
        ("offline_dl_batch",     t_offline_record_batch),
        # live (skip if FLUXISP_PROXY unset and the host is APAC; otherwise
        # works direct from US/EU)
        ("live_simgad",          t_live_simgad),
    ]:
        print(f"\n[{label}]")
        try:
            failures += fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            failures += 1
    print(f"\n{'PASS' if failures == 0 else f'{failures} FAIL'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
