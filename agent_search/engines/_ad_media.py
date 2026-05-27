"""Cross-platform ad-creative media downloader.

Once an ad-library engine returns ``SearchResult`` / ``AdRecord``
objects with their image / video URLs, downstream code typically wants
to download the actual files (for sweep-up archives, AI-driven copy
analysis, swipe-file building, etc.). This module provides one
:class:`AdMediaDownloader` that does that uniformly across all five
engines (Meta / Instagram / TikTok CC / TikTok Library / Google ATC).

Design notes
------------
* The downloader **never raises** to its caller. Every download attempt
  produces a :class:`DownloadResult` carrying ``success`` / ``error`` /
  ``local_path`` / ``file_size``. Callers can fan out hundreds of URLs
  knowing one bad one won't kill the batch.
* Files stream straight to disk in 64 KiB chunks so a 500 MB video
  never blows up RAM.
* Filenames follow ``{platform}_{ad_id}_{idx}_{kind}.{ext}`` so two
  ads from the same advertiser never collide. ``ext`` is detected
  from the URL path first, then the ``Content-Type`` header, then
  falls back to ``.bin``.
* Concurrency is opt-in via :meth:`download_many` (default 4 workers).
* The downloader takes an optional ``proxy_url``; when set, every HTTP
  request routes through it so engines that needed a proxy to
  *retrieve* the ad metadata can use the same proxy to retrieve the
  *media* without extra plumbing.
* Inputs accepted:
    1. :class:`AdRecord` (preferred — has ``platform``, ``ad_id``,
       ``media_urls[]`` already normalized).
    2. :class:`SearchResult` from any engine (we extract image / video
       URLs and infer ``platform`` / ``ad_id`` heuristically).
    3. Plain ``dict`` with the same field names.

The module deliberately does *not* shell out to ffmpeg or anything
fancy. It just fetches whatever URL the engine handed us. If you
want HLS playlists resolved, transcoding, etc., do that downstream.
"""

from __future__ import annotations

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence
from urllib.parse import urlparse

log = logging.getLogger(__name__)


# --------------------------------------------------------------------- consts


_URL_EXTS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".svg",
    ".mp4", ".webm", ".avi", ".mov", ".m4v", ".mkv", ".m3u8",
})

_CONTENT_TYPE_EXT = {
    "image/jpeg":   ".jpg",
    "image/jpg":    ".jpg",
    "image/png":    ".png",
    "image/gif":    ".gif",
    "image/webp":   ".webp",
    "image/bmp":    ".bmp",
    "image/svg+xml": ".svg",
    "video/mp4":    ".mp4",
    "video/webm":   ".webm",
    "video/quicktime": ".mov",
    "video/x-msvideo": ".avi",
    "video/x-matroska": ".mkv",
    "application/vnd.apple.mpegurl": ".m3u8",
    "application/x-mpegurl":         ".m3u8",
    "application/octet-stream":      ".bin",
}

_CHUNK_SIZE = 64 * 1024  # 64 KiB

# Filename character whitelist — strip everything else for safety.
_FN_SAFE = re.compile(r"[^A-Za-z0-9._-]")


def _safe_token(s: str, fallback: str = "x") -> str:
    """Sanitize ``s`` for use in a filename. Returns ``fallback`` for empty."""
    s = (s or "").strip()
    if not s:
        return fallback
    out = _FN_SAFE.sub("_", s)
    return out[:80] or fallback


def detect_ext_from_url(url: str) -> Optional[str]:
    """Return ``.jpg`` / ``.mp4`` / ... if the URL ends with a known ext."""
    if not url:
        return None
    try:
        path = urlparse(url).path
    except Exception:
        return None
    ext = Path(path).suffix.lower()
    return ext if ext in _URL_EXTS else None


def detect_ext_from_content_type(content_type: Optional[str]) -> Optional[str]:
    """Map a Content-Type header to a file extension (``None`` if unknown)."""
    if not content_type:
        return None
    mime = content_type.split(";")[0].strip().lower()
    return _CONTENT_TYPE_EXT.get(mime)


# ---------------------------------------------------------------- result type


@dataclass(frozen=True)
class DownloadResult:
    """One download attempt. Never raises; failure is communicated here."""

    url: str
    local_path: Optional[str] = None
    success: bool = False
    error: Optional[str] = None
    file_size: Optional[int] = None
    content_type: Optional[str] = None
    kind: str = ""        # "image" / "video" / "thumbnail" / "preview"
    ad_id: str = ""
    platform: str = ""

    def to_dict(self) -> dict:
        return {
            "url": self.url, "local_path": self.local_path,
            "success": self.success, "error": self.error,
            "file_size": self.file_size, "content_type": self.content_type,
            "kind": self.kind, "ad_id": self.ad_id, "platform": self.platform,
        }


# ---------------------------------------------------------------- downloader


class AdMediaDownloader:
    """Streams media files from any ad-library engine output to disk.

    Typical use::

        from agent_search.engines._ad_media import AdMediaDownloader

        dl = AdMediaDownloader("./swipe_files",
                               proxy_url=os.environ.get("FLUXISP_PROXY"))
        # For one record:
        results = dl.download_record(meta_search_result)
        # For many, concurrent:
        all_results = dl.download_many(meta_results, max_workers=8)
    """

    def __init__(
        self,
        output_dir: str | Path,
        *,
        proxy_url: Optional[str] = None,
        timeout: int = 30,
        max_retries: int = 2,
        session: Any = None,
    ) -> None:
        import requests  # late-imported

        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()
        if proxy_url:
            self.session.proxies.update({"http": proxy_url, "https": proxy_url})
        # Send a realistic UA so CDNs don't 403 us.
        self.session.headers.setdefault(
            "user-agent",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 "
            "Safari/537.36",
        )

    # ── extraction helpers (record → list of (url, kind) tuples) ─────

    @staticmethod
    def _quality_score(url: str, kind: str) -> int:
        """Heuristic quality rank — higher is better.

        Used so ``max_per_record=1`` always returns the most
        representative asset (typically a 1080p video). Order:

            video_hd > video > video_sd > image > thumbnail > preview > media

        Resolution hints in the URL bump the score. Two URLs with the
        same kind but different resolutions thus rank deterministically.
        """
        kind_base = {
            "video":     100,
            "video_hd":  105,   # explicit hd label wins narrowly over plain video
            "video_sd":  90,
            "image":     50,
            "media":     40,
            "thumbnail": 30,
            "preview":   20,
            "other":     10,
        }.get(kind, 10)

        bonus = 0
        u = url.lower()
        if "1080p" in u or "1080" in u:
            bonus += 10
        elif "720p" in u or "720" in u:
            bonus += 5
        elif "540p" in u or "540" in u:
            bonus += 3
        elif "480p" in u or "480" in u:
            bonus += 1
        # Penalise obvious low-res / placeholder paths
        if any(s in u for s in ("/thumb_", "_small", "/preview/", "_lowres")):
            bonus -= 5
        return kind_base + bonus

    @staticmethod
    def _extract_urls(record: Any) -> list[tuple[str, str]]:
        """Pull (url, kind) pairs from any of the supported input shapes.

        Order matters — we de-duplicate while preserving order so the
        first occurrence wins. ``kind`` is one of ``image`` / ``video``
        / ``thumbnail`` / ``preview`` / ``other``.
        """
        if record is None:
            return []
        # Allow plain dicts.
        d = (
            record if isinstance(record, dict)
            else getattr(record, "__dict__", {}) or {}
        )

        out: list[tuple[str, str]] = []

        def _add(url: Any, kind: str) -> None:
            if isinstance(url, str) and url.startswith(("http://", "https://")):
                out.append((url, kind))

        # AdRecord normalizes everything into media_urls[].
        for u in d.get("media_urls") or []:
            _add(u, "media")

        # Engine-specific lists.
        for u in d.get("image_urls") or []:
            _add(u, "image")
        for u in d.get("video_urls") or []:
            _add(u, "video")
        # video_urls can also be a dict {res: url} for TikTok CC.
        vd = d.get("video_urls")
        if isinstance(vd, dict):
            # Prefer 1080p > 720p > 540p > 480p > 360p > anything.
            preferred = ("1080p", "720p", "540p", "480p", "360p")
            for k in preferred:
                if isinstance(vd.get(k), str):
                    _add(vd[k], "video")
            for k, v in vd.items():
                if k not in preferred:
                    _add(v, "video")

        # Singletons.
        _add(d.get("video_url"), "video")
        _add(d.get("image_url"), "image")
        _add(d.get("cover_image_url"), "thumbnail")
        _add(d.get("avatar_url"), "thumbnail")
        _add(d.get("preview_url"), "preview")
        _add(d.get("preview_link"), "preview")

        # Carousel creatives.
        for c in d.get("creatives") or []:
            if not isinstance(c, dict):
                continue
            _add(c.get("video_url"), "video")
            _add(c.get("image_url"), "image")
            _add(c.get("thumbnail_url"), "thumbnail")

        # Dedup preserving order, then re-rank by quality score so the
        # highest-resolution asset of each kind ends up first. This
        # makes max_per_record=1 deterministically pick the best file.
        seen: set[str] = set()
        uniq: list[tuple[str, str]] = []
        for u, k in out:
            if u in seen:
                continue
            seen.add(u)
            uniq.append((u, k))
        # Stable-sort by quality desc; preserves original order for ties.
        uniq.sort(
            key=lambda pair: -AdMediaDownloader._quality_score(pair[0], pair[1]),
        )
        return uniq

    @staticmethod
    def _platform_and_id(record: Any) -> tuple[str, str]:
        """Best-effort guess of the platform + ad_id for filename keying."""
        if record is None:
            return ("unknown", "x")
        d = (
            record if isinstance(record, dict)
            else getattr(record, "__dict__", {}) or {}
        )
        platform = d.get("platform") or d.get("collection_source")
        if not platform:
            if d.get("ad_archive_id"):
                platform = "meta"
            elif d.get("creative_id") and d.get("advertiser_id"):
                platform = "google_atc"
            elif d.get("brand_name") and d.get("ad_id"):
                platform = "tiktok_cc"
            elif d.get("ad_id") and d.get("advertiser_name"):
                platform = "tiktok_lib"
            else:
                platform = "unknown"
        ad_id = (
            d.get("ad_id")
            or d.get("ad_archive_id")
            or d.get("creative_id")
            or d.get("collation_id")
            or d.get("clip_id")
            or "x"
        )
        return (_safe_token(str(platform)), _safe_token(str(ad_id)))

    # ── single download ──────────────────────────────────────────────

    def _build_filename(
        self, *, platform: str, ad_id: str, idx: int,
        kind: str, ext: str,
    ) -> str:
        return f"{platform}_{ad_id}_{idx:02d}_{_safe_token(kind, 'm')}{ext}"

    def download_one(
        self, url: str, *, filename: Optional[str] = None,
        kind: str = "media", ad_id: str = "", platform: str = "",
    ) -> DownloadResult:
        """Download a single URL. Always returns a :class:`DownloadResult`."""
        if not url or not url.startswith(("http://", "https://")):
            return DownloadResult(
                url=url or "", success=False,
                error="invalid url",
                kind=kind, ad_id=ad_id, platform=platform,
            )

        last_err: Optional[str] = None
        for attempt in range(self.max_retries + 1):
            try:
                with self.session.get(
                    url, stream=True, timeout=self.timeout,
                ) as r:
                    if r.status_code != 200:
                        last_err = f"HTTP {r.status_code}"
                        if 400 <= r.status_code < 500:
                            break  # don't retry 4xx
                        continue

                    # Resolve extension.
                    ext = (
                        detect_ext_from_url(url)
                        or detect_ext_from_content_type(r.headers.get("Content-Type"))
                        or ".bin"
                    )

                    if not filename:
                        # Fall back to a hash-y URL slug if caller didn't
                        # give us platform/ad_id. Always sanitized.
                        slug = _safe_token(
                            urlparse(url).path.rsplit("/", 1)[-1] or "media",
                        )
                        # Drop the original ext from slug to avoid duplicates.
                        slug = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", slug)
                        filename = (
                            f"{platform or 'unk'}_{ad_id or 'x'}_{slug}{ext}"
                        )
                    else:
                        # Fix up extension if caller's guess was wrong /
                        # absent.
                        if not Path(filename).suffix:
                            filename += ext

                    out_path = self.output_dir / filename
                    written = 0
                    with out_path.open("wb") as f:
                        for chunk in r.iter_content(chunk_size=_CHUNK_SIZE):
                            if not chunk:
                                continue
                            f.write(chunk)
                            written += len(chunk)
                    return DownloadResult(
                        url=url,
                        local_path=str(out_path),
                        success=True,
                        file_size=written,
                        content_type=r.headers.get("Content-Type"),
                        kind=kind, ad_id=ad_id, platform=platform,
                    )

            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"

        return DownloadResult(
            url=url, success=False, error=last_err or "unknown",
            kind=kind, ad_id=ad_id, platform=platform,
        )

    # ── per-record / batch ───────────────────────────────────────────

    def download_record(
        self, record: Any, *, max_per_record: Optional[int] = None,
    ) -> list[DownloadResult]:
        """Download every media URL discovered on a single record.

        ``max_per_record`` caps the per-ad fan-out (e.g. don't grab all
        5 video resolutions when 1 will do); ``None`` = no cap.
        """
        platform, ad_id = self._platform_and_id(record)
        urls = self._extract_urls(record)
        if max_per_record is not None:
            urls = urls[:max_per_record]

        results: list[DownloadResult] = []
        for i, (url, kind) in enumerate(urls):
            ext_guess = (detect_ext_from_url(url) or "")
            fn = self._build_filename(
                platform=platform, ad_id=ad_id, idx=i,
                kind=kind, ext=ext_guess,
            )
            results.append(self.download_one(
                url, filename=fn, kind=kind,
                ad_id=ad_id, platform=platform,
            ))
        return results

    def download_many(
        self, records: Iterable[Any], *,
        max_per_record: Optional[int] = None,
        max_workers: int = 4,
    ) -> list[DownloadResult]:
        """Download media for many records concurrently.

        Order of results is undefined. Each result still carries
        ``ad_id`` / ``platform`` / ``kind`` so callers can re-group.
        """
        records = list(records)
        if not records:
            return []

        all_results: list[DownloadResult] = []
        # Pre-expand into individual download jobs so the thread pool
        # doesn't sit idle while one big record is processed serially.
        jobs: list[tuple[str, str, str, int, str]] = []
        # (url, kind, ad_id, idx, platform, filename)
        per_record_items: list[tuple[Any, str, str, list[tuple[str, str]]]] = []
        for rec in records:
            platform, ad_id = self._platform_and_id(rec)
            urls = self._extract_urls(rec)
            if max_per_record is not None:
                urls = urls[:max_per_record]
            per_record_items.append((rec, platform, ad_id, urls))

        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = []
            for rec, platform, ad_id, urls in per_record_items:
                for i, (url, kind) in enumerate(urls):
                    ext_guess = detect_ext_from_url(url) or ""
                    fn = self._build_filename(
                        platform=platform, ad_id=ad_id, idx=i,
                        kind=kind, ext=ext_guess,
                    )
                    futures.append(ex.submit(
                        self.download_one,
                        url, filename=fn, kind=kind,
                        ad_id=ad_id, platform=platform,
                    ))
            for fut in as_completed(futures):
                try:
                    all_results.append(fut.result())
                except Exception as e:
                    # Should be impossible since download_one never
                    # re-raises, but guard anyway.
                    log.warning("[ad_media] worker raised: %s", e)
        return all_results
