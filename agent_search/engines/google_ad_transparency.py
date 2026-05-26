"""Google Ads Transparency Center engine.

Background
----------
Google's Ads Transparency Center (``adstransparency.google.com``) is the
public portal Google launched in 2023 (mandated by the EU DSA) that
exposes every active ad on Google Search / Shopping / Display / YouTube /
Maps with verified-advertiser status, first/last-seen dates, geographic
targeting, and the original creative.

The frontend is a Java/Closure SPA (``anji`` framework) that calls
internal RPC endpoints under ``/anji/_/rpc/<Service>/<Method>``. Those
RPCs use Google's protobuf-style JSON encoding where field names are
**positional integers** (``{"1": ..., "2": ...}``) instead of human
names — this is why scraping it manually is annoying.

Strategy
--------
Same pattern as ``meta_ad_library``: navigate the Creative Center
search page, intercept the ``SearchService/SearchSuggestions`` RPC
response, decode the integer field positions back to human field names.

Modes
-----
``mode="search_advertisers"`` (default)
    Find advertisers by name. Returns one result per matching advertiser
    with its ``advertiser_id``, country, and ad-count info. The
    ``advertiser_id`` can then be used with ``mode="advertiser_ads"``
    or pasted into the URL ``adstransparency.google.com/advertiser/<id>``.

``mode="advertiser_ads"``
    Given ``query="<advertiser_id>"``, list that advertiser's recent
    ads. **Caveat:** the ``SearchService/SearchCreatives`` RPC requires
    additional internal arguments that change frequently — this mode
    is best-effort and may return empty for some advertisers; fall back
    to opening the URL in a browser tab.

Returned fields
---------------
``advertiser_name``, ``advertiser_id``, ``country``, ``ad_count``,
``url`` (canonical advertiser page).
"""

from __future__ import annotations

import json
import logging
import time
import urllib.parse
from typing import Any

from .base import BaseEngine, SearchResult
from ..core import safe_goto

log = logging.getLogger(__name__)


_RPC_PATH = "/anji/_/rpc/"


class GoogleAdTransparencyEngine(BaseEngine):
    """Google Ads Transparency Center adapter via RPC interception."""

    name = "google_ad_transparency"
    max_retries = 2

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}

    # ------------------------------------------------------------------ public API

    def search(  # type: ignore[override]
        self,
        query: str,
        limit: int = 20,
        *,
        mode: str = "search_advertisers",
        region: str = "anywhere",
    ) -> list[SearchResult]:
        m = (mode or "search_advertisers").lower()
        if m not in ("search_advertisers", "advertiser_ads"):
            raise ValueError(f"unknown mode {m!r}")
        self.last_status = {"mode": m, "region": region, "query": query}

        if m == "search_advertisers":
            return self._search_advertisers(query, limit, region)
        return self._advertiser_ads(query, limit, region)

    # ------------------------------------------------------------------ search_advertisers

    def _search_advertisers(self, query: str, limit: int, region: str) -> list[SearchResult]:
        captured: dict[str, Any] = {"body": None}

        def _on_response(resp):
            if "/anji/_/rpc/SearchService/SearchSuggestions" in resp.url and resp.status == 200:
                if captured["body"] is None:
                    try:
                        captured["body"] = resp.json()
                    except Exception:
                        try:
                            text = resp.text()
                            captured["body"] = json.loads(text) if text else None
                        except Exception:
                            pass

        url = f"https://adstransparency.google.com/?region={region}&hl=en"
        self.page.on("response", _on_response)
        try:
            log.info("[g_ads] navigating %s", url)
            if not safe_goto(self.page, url, timeout=30000, retries=1):
                self.last_status["error"] = "navigation failed"
                return []
            self.page.wait_for_timeout(2000)
            # Type the query into the search box. Several selectors are
            # candidates because Google rotates DOM; try them in order.
            box = None
            for sel in (
                'input[type="search"]',
                'input[aria-label*="Search" i]',
                'input.gws-input',
                'input[placeholder*="advertiser" i]',
                'input',
            ):
                try:
                    box = self.page.query_selector(sel)
                    if box and box.is_visible():
                        break
                except Exception:
                    continue
            if box is None:
                self.last_status["error"] = "search input not found"
                return []
            try:
                box.click()
                box.fill(query)
                self.page.wait_for_timeout(800)
            except Exception as e:
                self.last_status["error"] = f"input fill failed: {e}"
                return []

            deadline = time.time() + 12.0
            while time.time() < deadline and captured["body"] is None:
                self.page.wait_for_timeout(400)
        finally:
            try:
                self.page.remove_listener("response", _on_response)
            except Exception:
                pass

        body = captured["body"]
        if not body:
            self.last_status["error"] = "no SearchSuggestions response"
            return []

        # Decode positional fields. Confirmed schema (May 2026):
        #   {"1": [{"1": {"1": <name>, "2": <id>, "3": <country>,
        #                 "4": {"2": {"1": <ad_count>, "2": <ad_count_max>}}}}, ...]}
        rows = (body.get("1") or [])
        results: list[SearchResult] = []
        for entry in rows[:limit]:
            inner = entry.get("1") or entry
            name = str(inner.get("1") or "")
            adv_id = str(inner.get("2") or "")
            country = str(inner.get("3") or "")
            ad_count = None
            cnt_block = (inner.get("4") or {}).get("2") or {}
            if isinstance(cnt_block, dict):
                ad_count = cnt_block.get("1")
            if not adv_id:
                continue
            url = (
                f"https://adstransparency.google.com/advertiser/{adv_id}"
                f"?region={region}&hl=en"
            )
            snippet_parts = [country] if country else []
            if ad_count is not None:
                snippet_parts.append(f"ads={ad_count}")
            r = SearchResult(
                title=name,
                url=url,
                snippet=" · ".join(snippet_parts),
            )
            r.__dict__.update({
                "advertiser_name": name,
                "advertiser_id": adv_id,
                "country": country,
                "ad_count": ad_count,
                "region": region,
            })
            results.append(r)

        self.last_status["found"] = len(results)
        return results

    # ------------------------------------------------------------------ advertiser_ads

    def _advertiser_ads(self, advertiser_id: str, limit: int, region: str) -> list[SearchResult]:
        if not advertiser_id.startswith("AR"):
            log.warning(
                "[g_ads] advertiser_id should start with 'AR' (got %r); "
                "use search_advertisers to find the right ID first",
                advertiser_id[:30],
            )
        captured: dict[str, Any] = {"body": None}

        def _on_response(resp):
            if "/anji/_/rpc/SearchService/SearchCreatives" in resp.url and resp.status == 200:
                if captured["body"] is None:
                    try:
                        captured["body"] = resp.json()
                    except Exception:
                        pass

        url = (
            f"https://adstransparency.google.com/advertiser/{advertiser_id}"
            f"?region={region}&hl=en"
        )
        self.page.on("response", _on_response)
        try:
            if not safe_goto(self.page, url, timeout=30000, retries=1):
                self.last_status["error"] = "navigation failed"
                return []
            deadline = time.time() + 18.0
            while time.time() < deadline and captured["body"] is None:
                self.page.wait_for_timeout(500)
        finally:
            try:
                self.page.remove_listener("response", _on_response)
            except Exception:
                pass

        body = captured["body"] or {}
        # SearchCreatives payload — observed shape (May 2026):
        #   {"1": [{"1": <creative_id>, "2": <advertiser_id>,
        #           "3": {"1": <format_int>, ...},  # format/format_subtype
        #           "5": {"1": <first_shown_ms>, "2": <last_shown_ms>},
        #           "9": <region>, "12": <text_summary>}, ...]}
        # The exact keys vary by ad format. We expose the raw block so
        # callers can inspect; we also try to surface common fields.
        rows = body.get("1") or []
        if not rows:
            self.last_status["error"] = (
                "SearchCreatives returned empty — Google ATC frequently "
                "rejects this RPC without extra session args; try opening "
                f"{url} in a browser tab"
            )
            return []

        results: list[SearchResult] = []
        format_map = {
            1: "search_text",
            2: "image",
            3: "video",
            4: "shopping",
            8: "display",
        }
        for ad in rows[:limit]:
            cid = str(ad.get("1") or "")
            fmt_block = ad.get("3") or {}
            fmt_int = fmt_block.get("1") if isinstance(fmt_block, dict) else None
            fmt = format_map.get(fmt_int, str(fmt_int) if fmt_int else "")
            dates = ad.get("5") or {}
            first_ms = (dates.get("1") if isinstance(dates, dict) else None)
            last_ms = (dates.get("2") if isinstance(dates, dict) else None)
            text_summary = str(ad.get("12") or "")
            country = str(ad.get("9") or region)

            ad_url = (
                f"https://adstransparency.google.com/advertiser/{advertiser_id}/"
                f"creative/{cid}?region={region}&hl=en" if cid else url
            )
            days = None
            if first_ms and last_ms:
                try:
                    days = int((int(last_ms) - int(first_ms)) / (1000 * 86400))
                except Exception:
                    days = None
            snippet_parts = []
            if fmt:
                snippet_parts.append(fmt)
            if days is not None:
                snippet_parts.append(f"{days}d running")
            if text_summary:
                snippet_parts.append(text_summary[:120])

            r = SearchResult(
                title=text_summary[:140] or cid,
                url=ad_url,
                snippet=" · ".join(snippet_parts),
            )
            r.__dict__.update({
                "creative_id": cid,
                "advertiser_id": advertiser_id,
                "format": fmt,
                "format_int": fmt_int,
                "first_seen_ms": first_ms,
                "last_seen_ms": last_ms,
                "days_running": days,
                "country": country,
                "raw": ad,
            })
            results.append(r)
        return results

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        return self.search(query, limit)
