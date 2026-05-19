"""VirusTotal search adapter.

VirusTotal's web GUI (``https://www.virustotal.com/gui/search/<q>``) is a
heavy lit-element SPA whose results live deep inside nested shadow DOM,
so straight-shot DOM scraping is fragile. This adapter therefore prefers
the public **VirusTotal API v3** when an API key is available and falls
back to a best-effort shadow-DOM walk on the GUI for unauthenticated
callers.

Mode 1 — API (preferred)
------------------------
Activated when ``VIRUSTOTAL_API_KEY`` (or ``VT_API_KEY``) is set, or when
the caller passes ``api_key=`` to the constructor.

* Hash query (32 / 40 / 64 hex chars → MD5 / SHA-1 / SHA-256) →
  ``GET /api/v3/files/{hash}``
* Anything else                                              →
  ``GET /api/v3/search?query=<q>&limit=<n>``

The HTTP requests are issued from the **page context** (after navigating
to ``https://www.virustotal.com/`` so the call is same-origin and skips
the CORS preflight) using ``page.evaluate(fetch ...)`` — same trick the
PubMed adapter uses to keep traffic on the user's stealth TLS / proxy
chain and avoid pulling in `requests` / `httpx` at runtime.

Mode 2 — GUI (fallback)
-----------------------
Without an API key we navigate to ``/gui/search/<query>``. For a hash
the SPA auto-redirects to ``/gui/file/<hash>``. We give the lit
components a few seconds to mount, then walk every shadow root looking
for the detection ratio (typically a "60/72" string inside a
``<vt-ui-shield-score>`` / ``<vt-ui-detections-grid>`` component). If
extraction fails we still return a single :class:`SearchResult`
pointing at the canonical file URL so the caller can open it manually.

Returned ``SearchResult`` extension fields
------------------------------------------
* ``r.entity_type``     — ``"file"`` / ``"url"`` / ``"domain"`` /
                          ``"ip_address"`` / ``"search"``
* ``r.api_used``        — ``True`` when the result came from API mode
* ``r.detections``      — e.g. ``"60/72"``
* ``r.malicious``       — int, e.g. ``60``
* ``r.suspicious``      — int (API mode only)
* ``r.total_scanners``  — int, e.g. ``72``
* ``r.community_score`` — int (VT "reputation"; may be negative)
* ``r.file_name``       — first known meaningful name
* ``r.file_type``       — e.g. ``"DOS COM"``, ``"Win32 EXE"``
* ``r.file_size``       — int bytes
* ``r.md5`` / ``r.sha1`` / ``r.sha256`` — when known
"""

from __future__ import annotations

import json
import logging
import os
import re
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)


_GUI_BASE = "https://www.virustotal.com"
_API_BASE = _GUI_BASE + "/api/v3"
_GUI_SEARCH_TPL = _GUI_BASE + "/gui/search/{q}"
_GUI_FILE_TPL = _GUI_BASE + "/gui/file/{h}"

# The same-origin we navigate to before issuing API fetches. A trailing
# slash matters: page.url comparisons elsewhere include it.
_FETCH_ORIGIN = _GUI_BASE + "/"

_NAV_TIMEOUT_MS = 40_000
_HTTP_TIMEOUT_MS = 30_000

_HASH_RE = re.compile(r"^[A-Fa-f0-9]+$")
# 32 = MD5, 40 = SHA-1, 64 = SHA-256
_HASH_LENGTHS: dict[int, str] = {32: "md5", 40: "sha1", 64: "sha256"}


# ---- in-page fetch helper ----------------------------------------------------
#
# Single-arg form (Playwright passes one positional arg through evaluate),
# unpacked at the top of the JS.
_FETCH_JS = r"""
async ({url, headers}) => {
  const r = await fetch(url, {
    credentials: 'omit',
    headers: headers || {},
  });
  let text = '';
  try { text = await r.text(); } catch(e) {}
  return { ok: r.ok, status: r.status, text };
}
"""


# ---- shadow-DOM walker for the file detail page -----------------------------
#
# Walks every node, descending into shadow roots, looking for:
#   1. A "<malicious> / <total>" detection ratio (60/72 etc.). VT renders
#      this in <vt-ui-shield-score> / <vt-ui-detections-grid>; we pin to
#      those tags first, then loosen to any short text matching the
#      fraction shape.
#   2. The page <h1> (typically "VirusTotal" or the file name) and title,
#      plus a body length sanity check so callers can tell whether the
#      SPA actually rendered.
_SCRAPE_FILE_JS = r"""
() => {
  const all = [];
  const walk = (root) => {
    if (!root) return;
    if (root.querySelectorAll) {
      try {
        for (const el of root.querySelectorAll('*')) all.push(el);
      } catch(e) {}
    }
    const children = root.children ? Array.from(root.children) : [];
    for (const c of children) walk(c);
    if (root.shadowRoot) walk(root.shadowRoot);
  };
  walk(document);

  const tagged = (names) => names.map(n => n.toUpperCase());
  const PRIMARY = tagged([
    'vt-ui-shield-score',
    'vt-ui-detections-grid',
    'vt-ui-file-card',
    'vt-ui-file-detection',
    'vt-ui-detection-ratio',
  ]);

  let detections = '';
  let malicious = null;
  let total = null;

  // Pass 1: pinned tag names (most reliable).
  for (const el of all) {
    const t = (el.tagName || '').toUpperCase();
    if (PRIMARY.indexOf(t) === -1) continue;
    const txt = (el.textContent || '').replace(/\s+/g, ' ').trim();
    const m = txt.match(/(\d{1,3})\s*\/\s*(\d{2,3})/);
    if (m) {
      const a = parseInt(m[1], 10), b = parseInt(m[2], 10);
      if (b >= 30 && b <= 120 && a <= b) {
        detections = a + '/' + b;
        malicious = a;
        total = b;
        break;
      }
    }
  }

  // Pass 2: any short-text node shaped exactly like "X / Y" with Y in
  // the AV-vendor-count range. Helps when VT renames a custom element.
  if (!detections) {
    for (const el of all) {
      const txt = (el.textContent || '').replace(/\s+/g, ' ').trim();
      if (txt.length === 0 || txt.length > 25) continue;
      const m = txt.match(/^(\d{1,3})\s*\/\s*(\d{2,3})$/);
      if (!m) continue;
      const a = parseInt(m[1], 10), b = parseInt(m[2], 10);
      if (b >= 30 && b <= 120 && a <= b) {
        detections = a + '/' + b;
        malicious = a;
        total = b;
        break;
      }
    }
  }

  // <h1> from anywhere in the deep tree — useful as the title.
  let h1 = '';
  for (const el of all) {
    if ((el.tagName || '').toUpperCase() === 'H1') {
      const t = (el.textContent || '').trim();
      if (t) { h1 = t; break; }
    }
  }

  let bodyLen = 0;
  try { bodyLen = (document.body && document.body.innerText || '').length; } catch(e) {}

  return {
    detections,
    malicious,
    total,
    title: (document.title || '').trim(),
    h1,
    url: location.href,
    bodyLen,
    deepNodeCount: all.length,
  };
}
"""


class VirusTotalEngine(BaseEngine):
    """Search VirusTotal — API-first, GUI fallback."""

    name = "virustotal"
    # The API path is one network call so retries beyond 1 are noise; the
    # GUI fallback already gives us a second shot anyway.
    max_retries = 2

    def __init__(self, page, api_key: str | None = None):
        super().__init__(page)
        self.api_key = (
            api_key
            or os.environ.get("VIRUSTOTAL_API_KEY")
            or os.environ.get("VT_API_KEY")
            or ""
        ).strip()
        # Diagnostics for callers / tests.
        self.last_status: dict = {}
        self.last_strategy: str = ""

    # -------------------------------------------------------------- helpers
    @staticmethod
    def _is_hash(query: str) -> bool:
        q = (query or "").strip()
        return len(q) in _HASH_LENGTHS and bool(_HASH_RE.match(q))

    @staticmethod
    def _hash_kind(query: str) -> str:
        return _HASH_LENGTHS.get(len((query or "").strip()), "")

    # ---------------------------------------------------------- in-page HTTP
    def _ensure_fetch_origin(self) -> None:
        """Make sure the page is on virustotal.com so fetch() is same-origin."""
        try:
            cur = (self.page.url or "").lower()
        except Exception:
            cur = ""
        if cur.startswith(_FETCH_ORIGIN):
            return
        try:
            self.page.goto(
                _FETCH_ORIGIN,
                wait_until="domcontentloaded",
                timeout=_NAV_TIMEOUT_MS,
            )
        except Exception as e:
            log.warning("[virustotal] could not reach fetch origin: %s", e)

    def _fetch(self, url: str, headers: dict | None = None) -> dict:
        """Issue an in-page fetch and return ``{ok, status, text}``."""
        self._ensure_fetch_origin()
        result = self.page.evaluate(_FETCH_JS, {"url": url, "headers": headers or {}})
        if not isinstance(result, dict):
            raise RuntimeError(
                f"unexpected fetch result type: {type(result).__name__}"
            )
        return result

    # ------------------------------------------------------------- API mode
    def _api_get_json(self, path: str) -> dict | None:
        url = _API_BASE + path
        headers = {"x-apikey": self.api_key, "Accept": "application/json"}
        try:
            res = self._fetch(url, headers)
        except Exception as e:
            log.error("[virustotal] api fetch error: %s", e)
            return None

        status = res.get("status")
        text = res.get("text") or ""
        if not res.get("ok"):
            log.warning("[virustotal] api %s -> HTTP %s: %s", path, status, text[:200])
            return None
        try:
            return json.loads(text)
        except Exception as e:
            log.error("[virustotal] api %s returned non-JSON (%s): %r", path, e, text[:200])
            return None

    def _api_file_lookup(self, hash_value: str) -> SearchResult | None:
        h = hash_value.strip().lower()
        body = self._api_get_json(f"/files/{urllib.parse.quote(h)}")
        if not body:
            return None
        return self._build_result_from_file_object(body.get("data") or {})

    def _api_search(self, query: str, limit: int) -> list[SearchResult]:
        params = urllib.parse.urlencode(
            {"query": query, "limit": max(1, min(int(limit), 40))}
        )
        body = self._api_get_json(f"/search?{params}")
        if not body:
            return []

        out: list[SearchResult] = []
        for obj in body.get("data") or []:
            entity_type = obj.get("type") or ""
            if entity_type == "file":
                r = self._build_result_from_file_object(obj)
            elif entity_type == "url":
                r = self._build_result_from_url_object(obj)
            elif entity_type == "domain":
                r = self._build_result_from_domain_object(obj)
            elif entity_type == "ip_address":
                r = self._build_result_from_ip_object(obj)
            else:
                # Unknown entity types (collections, comments, ...) — skip.
                continue
            if r is not None:
                out.append(r)
            if len(out) >= max(1, int(limit)):
                break
        return out

    # ---- result builders for each VT entity ----
    def _build_result_from_file_object(self, obj: dict) -> SearchResult | None:
        attr = obj.get("attributes") or {}
        sha256 = (attr.get("sha256") or obj.get("id") or "").lower()
        md5 = (attr.get("md5") or "").lower()
        sha1 = (attr.get("sha1") or "").lower()
        names = attr.get("names") or []
        meaningful_name = (
            attr.get("meaningful_name") or (names[0] if names else "")
        )
        file_type = (
            attr.get("type_description") or attr.get("type_tag") or ""
        )
        size = int(attr.get("size") or 0)

        last_stats = attr.get("last_analysis_stats") or {}
        malicious = int(last_stats.get("malicious", 0) or 0)
        suspicious = int(last_stats.get("suspicious", 0) or 0)
        # Total = sum of all verdict buckets so we report the same denominator
        # the VT GUI shows in its X/Y badge.
        total = sum(int(v or 0) for v in last_stats.values())

        community = attr.get("reputation")
        title = meaningful_name or sha256 or "VirusTotal file"
        url = _GUI_FILE_TPL.format(h=sha256 or md5 or sha1)

        snippet_parts: list[str] = []
        if file_type:
            snippet_parts.append(file_type)
        if size:
            snippet_parts.append(f"{size} bytes")
        if total:
            snippet_parts.append(f"{malicious}/{total} detections")
        snippet = " · ".join(snippet_parts)

        sr = SearchResult(
            title=title,
            url=url,
            snippet=snippet,
            score=malicious if total else None,
        )
        sr.entity_type = "file"
        sr.api_used = True
        sr.md5 = md5
        sr.sha1 = sha1
        sr.sha256 = sha256
        sr.file_name = meaningful_name
        sr.file_type = file_type
        sr.file_size = size
        sr.malicious = malicious
        sr.suspicious = suspicious
        sr.total_scanners = total
        sr.detections = f"{malicious}/{total}" if total else ""
        sr.community_score = community if isinstance(community, int) else None
        sr.names = names
        return sr

    def _build_result_from_url_object(self, obj: dict) -> SearchResult | None:
        attr = obj.get("attributes") or {}
        url_value = attr.get("url") or ""
        last_stats = attr.get("last_analysis_stats") or {}
        malicious = int(last_stats.get("malicious", 0) or 0)
        total = sum(int(v or 0) for v in last_stats.values())
        gui_url = f"{_GUI_BASE}/gui/url/{obj.get('id', '')}"
        title = url_value or obj.get("id") or "VirusTotal URL"
        snippet = f"{malicious}/{total} detections" if total else ""

        sr = SearchResult(title=title, url=gui_url, snippet=snippet, score=malicious if total else None)
        sr.entity_type = "url"
        sr.api_used = True
        sr.malicious = malicious
        sr.total_scanners = total
        sr.detections = f"{malicious}/{total}" if total else ""
        return sr

    def _build_result_from_domain_object(self, obj: dict) -> SearchResult | None:
        domain = obj.get("id") or ""
        attr = obj.get("attributes") or {}
        last_stats = attr.get("last_analysis_stats") or {}
        malicious = int(last_stats.get("malicious", 0) or 0)
        total = sum(int(v or 0) for v in last_stats.values())
        title = domain or "VirusTotal domain"
        gui_url = f"{_GUI_BASE}/gui/domain/{urllib.parse.quote(domain)}"
        snippet = f"{malicious}/{total} detections" if total else ""

        sr = SearchResult(title=title, url=gui_url, snippet=snippet, score=malicious if total else None)
        sr.entity_type = "domain"
        sr.api_used = True
        sr.malicious = malicious
        sr.total_scanners = total
        sr.detections = f"{malicious}/{total}" if total else ""
        return sr

    def _build_result_from_ip_object(self, obj: dict) -> SearchResult | None:
        ip = obj.get("id") or ""
        attr = obj.get("attributes") or {}
        last_stats = attr.get("last_analysis_stats") or {}
        malicious = int(last_stats.get("malicious", 0) or 0)
        total = sum(int(v or 0) for v in last_stats.values())
        title = ip or "VirusTotal IP"
        gui_url = f"{_GUI_BASE}/gui/ip-address/{urllib.parse.quote(ip)}"
        snippet = f"{malicious}/{total} detections" if total else ""

        sr = SearchResult(title=title, url=gui_url, snippet=snippet, score=malicious if total else None)
        sr.entity_type = "ip_address"
        sr.api_used = True
        sr.malicious = malicious
        sr.total_scanners = total
        sr.detections = f"{malicious}/{total}" if total else ""
        return sr

    # ------------------------------------------------------------- GUI mode
    def _scrape_gui(self, query: str, limit: int) -> list[SearchResult]:
        is_hash = self._is_hash(query)
        if is_hash:
            target = _GUI_FILE_TPL.format(h=query.strip().lower())
        else:
            target = _GUI_SEARCH_TPL.format(q=urllib.parse.quote(query))

        log.info("[virustotal] (gui) navigating to %s", target)
        if not safe_goto(self.page, target, timeout=_NAV_TIMEOUT_MS):
            log.warning("[virustotal] gui nav failed")
            return []

        # The SPA needs a few seconds for lit components to mount and the
        # XHR to /ui/files/<hash> to resolve. There is no single stable
        # selector to wait on, so we just budget time.
        human_delay(4.0, 6.0)

        try:
            payload = self.page.evaluate(_SCRAPE_FILE_JS)
        except Exception as e:
            log.warning("[virustotal] page.evaluate (gui) failed: %s", e)
            payload = {}
        if not isinstance(payload, dict):
            payload = {}

        cur_url = (payload.get("url") or "").lower()
        body_len = int(payload.get("bodyLen") or 0)
        deep_node_count = int(payload.get("deepNodeCount") or 0)
        scraped_detections = payload.get("detections") or ""

        self.last_status = {
            "url": payload.get("url") or "",
            "title": payload.get("title") or "",
            "h1": payload.get("h1") or "",
            "body_len": body_len,
            "deep_node_count": deep_node_count,
            "detections": scraped_detections,
        }

        # Sanity: did we end up somewhere on virustotal.com that looks
        # like a search/file page (and not a Cloudflare splash)?
        if "virustotal.com" not in cur_url:
            log.warning("[virustotal] GUI ended up off-site: %s", cur_url)
            return []

        # The VT SPA renders entirely inside nested shadow roots, so
        # ``document.body.innerText`` is typically empty even on fully
        # rendered pages. Treat a populated deep DOM (or a parsed
        # detection ratio) as proof-of-life in addition to body text.
        rendered = (
            body_len >= 80
            or deep_node_count >= 500
            or bool(scraped_detections)
        )
        if not rendered:
            log.warning(
                "[virustotal] GUI not rendered "
                "(body_len=%d, deep_nodes=%d, detections=%r) — likely blocked",
                body_len,
                deep_node_count,
                scraped_detections,
            )
            return []

        if not is_hash:
            # Generic search via GUI lives in a multi-tab UI whose tab
            # contents are loaded lazily. Returning the search URL as a
            # placeholder is the most honest thing to do without an API key.
            sr = SearchResult(
                title=f"VirusTotal search: {query}",
                url=target,
                snippet=(
                    "GUI search results require interactive rendering — "
                    "open the URL in a browser, or set VIRUSTOTAL_API_KEY"
                ),
            )
            sr.entity_type = "search"
            sr.api_used = False
            return [sr]

        # Hash case: build a single result, populated with whatever
        # detection data the shadow-DOM walk managed to find.
        h = query.strip().lower()
        kind = self._hash_kind(query)
        title = payload.get("h1") or payload.get("title") or h
        detections = payload.get("detections") or ""
        malicious = payload.get("malicious")
        total = payload.get("total")

        snippet_parts: list[str] = []
        if detections:
            snippet_parts.append(f"{detections} detections")
        else:
            snippet_parts.append(
                "page loaded; detection ratio not parsed (shadow DOM)"
            )
        snippet = " · ".join(snippet_parts)

        sr = SearchResult(
            title=title,
            url=cur_url or target,
            snippet=snippet,
            score=malicious if isinstance(malicious, int) else None,
        )
        sr.entity_type = "file"
        sr.api_used = False
        sr.detections = detections
        sr.malicious = malicious if isinstance(malicious, int) else None
        sr.total_scanners = total if isinstance(total, int) else None
        if kind == "md5":
            sr.md5 = h
        elif kind == "sha1":
            sr.sha1 = h
        elif kind == "sha256":
            sr.sha256 = h
        return [sr]

    # ----------------------------------------------------------------- main
    def search(self, query: str, limit: int = 10) -> list[SearchResult]:
        """Override BaseEngine.search() because VT's SPA renders entirely
        inside shadow DOM. ``check_blocked()`` reads ``document.body``
        innerText, which is empty on every VT GUI page — so the inherited
        retry loop would discard valid results as ``empty_page``.
        """
        from ..core import human_delay
        from ..stealth.enhance import check_blocked

        last_results: list[SearchResult] = []
        for attempt in range(self.max_retries):
            try:
                results = self._do_search(query, limit)
            except Exception as e:
                log.error(
                    "[%s] error (attempt %d): %s", self.name, attempt + 1, e
                )
                results = []

            if results:
                return results

            blocked = check_blocked(self.page)
            # ``empty_page`` is a false positive on VT (shadow DOM); only
            # treat *other* block reasons as a reason to retry-with-delay.
            if blocked and blocked != "empty_page":
                log.warning(
                    "[%s] blocked (attempt %d): %s",
                    self.name, attempt + 1, blocked,
                )
                human_delay(3, 6)
            else:
                log.warning(
                    "[%s] no results (attempt %d)", self.name, attempt + 1
                )
                human_delay(2, 4)
            last_results = results
        return last_results

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # Strategy 1 — API mode when we have a key.
        if self.api_key:
            try:
                if self._is_hash(query):
                    r = self._api_file_lookup(query)
                    if r is not None:
                        self.last_strategy = "api_file"
                        return [r]
                else:
                    rs = self._api_search(query, limit)
                    if rs:
                        self.last_strategy = "api_search"
                        return rs
                log.info("[virustotal] API mode returned nothing, falling back to GUI")
            except Exception as e:
                log.warning("[virustotal] API mode raised, falling back to GUI: %s", e)

        # Strategy 2 — GUI scrape (always available, less reliable).
        results = self._scrape_gui(query, limit)
        if results:
            self.last_strategy = self.last_strategy or "gui"
        return results
