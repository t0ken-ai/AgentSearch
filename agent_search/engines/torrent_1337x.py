"""1337x torrent search adapter.

1337x has no public API — we scrape the standard search page::

    https://1337x.to/search/<query>/1/

The result page renders a single ``<table class="table-list">`` whose rows
follow the same column shape that 1337x has used for years::

    <tr>
      <td class="coll-1 name">
        <a href="/sub/<id>/<n>/">…icon…</a>
        <a href="/torrent/<id>/<slug>/">Torrent Title</a>
      </td>
      <td class="coll-2 seeds">123</td>
      <td class="coll-3 leeches">45</td>
      <td class="coll-date">Sep. 1st '20</td>
      <td class="coll-4 size mob-vip">1.4 GB<span class="seeds">12</span></td>
      <td class="coll-5 vip">UploaderName</td>
    </tr>

The torrent **detail** link is the *second* anchor in ``td.coll-1`` (the first
anchor is the category icon → ``/sub/...``). The size cell embeds a duplicate
seeds counter inside ``<span class="seeds">`` for the mobile layout, so we
strip span children before reading text.

Cloudflare handling
-------------------
1337x sits behind Cloudflare. CloakBrowser already passes most JS challenges
on its own, so all we need to do is:

* warm up on the homepage so the ``cf_clearance`` cookie settles, then
* navigate to the search URL and wait for ``table.table-list`` (or for the
  Cloudflare interstitial title to disappear).

If we *do* land on a "Just a moment…" / "Checking your browser" page we
poll up to ``CF_WAIT_S`` seconds for it to clear. If Cloudflare returns a
hard block (403 / hCaptcha) we retry on a mirror domain.

Mirrors
-------
1337x publishes a rotating set of mirrors (the project's own /about page
lists them). We try them in order if the primary host is blocked, gives a
hard 403 or the table never appears.

Returned ``SearchResult`` extension fields
------------------------------------------
* ``r.seeders``  — int
* ``r.leechers`` — int
* ``r.size``     — str (e.g. ``"1.4 GB"``)
* ``r.uploader`` — str (best-effort, may be empty)
* ``r.snippet``  — ``"S/L · size · uploader"``
"""

from __future__ import annotations

import logging
import random
import re
import time
from urllib.parse import quote

from ..core import human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)


# Hosts to try in order. The first one that returns a usable result table wins.
_MIRRORS: tuple[str, ...] = (
    "https://1337x.to",
    "https://1337x.tw",
    "https://1337x.st",
    "https://x1337x.ws",
    "https://x1337x.eu",
    "https://x1337x.se",
)

# Title fragments that mean we're stuck on a Cloudflare interstitial.
_CF_TITLES: tuple[str, ...] = (
    "just a moment",
    "checking your browser",
    "attention required",
    "cloudflare",
)

# Selector that signals "results are here".
_RESULT_SELECTOR = "table.table-list tbody tr"

# How long to keep polling for the Cloudflare interstitial to disappear.
_CF_WAIT_S = 25.0

# Per-attempt navigation timeout (ms).
_NAV_TIMEOUT = 35_000


# JS that runs in the page and pulls structured rows out of the table.
# Returning a JSON-friendly list lets the Python side stay trivial.
_PARSE_JS = r"""
(originHost) => {
  const rows = document.querySelectorAll('table.table-list tbody tr');
  const out = [];
  for (const tr of rows) {
    const nameCell = tr.querySelector('td.coll-1, td.name');
    if (!nameCell) continue;

    // The torrent detail link is the *last* anchor in the name cell;
    // the first anchor (when present) is the category icon → /sub/...
    const anchors = nameCell.querySelectorAll('a');
    if (!anchors.length) continue;
    const link = anchors[anchors.length - 1];

    const title = (link.textContent || '').trim();
    let href = link.getAttribute('href') || '';
    if (!title || !href) continue;
    if (href.startsWith('/')) href = originHost + href;

    const seedsCell   = tr.querySelector('td.coll-2, td.seeds');
    const leechCell   = tr.querySelector('td.coll-3, td.leeches');
    // td.coll-4 carries the size; on mobile it also embeds a <span class="seeds">
    // counter. Clone and strip span children before reading text.
    const sizeCell    = tr.querySelector('td.coll-4, td.size');
    const uploaderEl  = tr.querySelector('td.coll-5 a, td.coll-5, td.vip a, td.user a');

    const intText = (el) => {
      if (!el) return 0;
      const t = (el.textContent || '').replace(/[,\s]/g, '');
      const n = parseInt(t, 10);
      return Number.isFinite(n) ? n : 0;
    };

    let size = '';
    if (sizeCell) {
      const clone = sizeCell.cloneNode(true);
      clone.querySelectorAll('span').forEach(s => s.remove());
      size = (clone.textContent || '').replace(/\s+/g, ' ').trim();
    }

    out.push({
      title,
      url: href,
      seeders:  intText(seedsCell),
      leechers: intText(leechCell),
      size,
      uploader: uploaderEl ? (uploaderEl.textContent || '').trim() : '',
    });
  }
  return out;
}
"""


class Torrent1337xEngine(BaseEngine):
    """Search 1337x via the public HTML search page."""

    name = "torrent_1337x"
    max_retries = 2  # one retry inside BaseEngine.search() — mirrors handle the rest

    def __init__(self, page):
        super().__init__(page)
        # Diagnostics for callers / tests.
        self.last_status: dict = {}

    # ----------------------------------------------------------------- helpers
    def _wait_past_cloudflare(self, deadline: float) -> bool:
        """Block until the Cloudflare interstitial clears, or ``deadline`` hits.

        Returns True if we believe we're past the challenge, False on timeout.
        """
        while time.time() < deadline:
            try:
                title = (self.page.title() or "").lower()
            except Exception:
                title = ""
            if not any(frag in title for frag in _CF_TITLES):
                return True
            # Don't spam: short jittered wait.
            time.sleep(random.uniform(0.8, 1.6))
        return False

    def _looks_blocked(self) -> str | None:
        """Return a short reason if we appear to be hard-blocked."""
        try:
            title = (self.page.title() or "").lower()
        except Exception:
            title = ""
        try:
            url = (self.page.url or "").lower()
        except Exception:
            url = ""
        try:
            body = self.page.inner_text("body")
        except Exception:
            body = ""

        if any(frag in title for frag in _CF_TITLES):
            return f"cloudflare_interstitial: {title!r}"
        if "access denied" in title or "forbidden" in title:
            return f"access_denied: {title!r}"
        if "/cdn-cgi/" in url and "challenge" in url:
            return f"cf_challenge_url: {url!r}"
        if len(body.strip()) < 100:
            return f"empty_body: len={len(body)}"
        return None

    def _human_warmup(self, origin: str) -> None:
        """Hit the homepage so cf_clearance / session cookies settle in."""
        try:
            self.page.goto(
                origin + "/",
                wait_until="domcontentloaded",
                timeout=_NAV_TIMEOUT,
            )
        except Exception as e:
            log.warning("[1337x] warmup goto %s failed: %s", origin, e)
            return
        # Try to ride out any CF interstitial *before* we navigate to /search/.
        self._wait_past_cloudflare(time.time() + _CF_WAIT_S)
        try:
            self.page.mouse.move(
                random.randint(120, 480),
                random.randint(120, 380),
                steps=8,
            )
        except Exception:
            pass
        try:
            self.page.evaluate(
                "() => window.scrollBy(0, Math.floor(Math.random()*300) + 80)"
            )
        except Exception:
            pass
        human_delay(0.6, 1.4)

    # ----------------------------------------------------------------- attempt
    def _try_origin(self, origin: str, query: str, limit: int) -> list[SearchResult]:
        """Try one mirror; return [] (and update ``last_status``) on failure."""
        self._human_warmup(origin)

        url = f"{origin}/search/{quote(query)}/1/"
        log.info("[1337x] search: %s", url)
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT)
        except Exception as e:
            self.last_status = {"origin": origin, "url": url, "error": f"goto_failed: {e}"}
            log.warning("[1337x] goto %s failed: %s", url, e)
            return []

        # If we got a CF interstitial, give it a chance to clear.
        self._wait_past_cloudflare(time.time() + _CF_WAIT_S)

        # Wait for the result table or, failing that, the page to finish loading.
        try:
            self.page.wait_for_selector(_RESULT_SELECTOR, timeout=_NAV_TIMEOUT)
        except Exception as e:
            log.info("[1337x] result selector wait failed on %s: %s", origin, e)

        block = self._looks_blocked()
        if block:
            self.last_status = {
                "origin": origin,
                "url": url,
                "block_reason": block,
            }
            log.warning("[1337x] %s blocked: %s", origin, block)
            return []

        try:
            raw = self.page.evaluate(_PARSE_JS, origin) or []
        except Exception as e:
            self.last_status = {"origin": origin, "url": url, "error": f"evaluate_failed: {e}"}
            log.error("[1337x] page.evaluate failed: %s", e)
            return []

        results: list[SearchResult] = []
        for item in raw[: max(1, int(limit))]:
            title = (item.get("title") or "").strip()
            row_url = (item.get("url") or "").strip()
            if not title or not row_url:
                continue

            seeders = int(item.get("seeders") or 0)
            leechers = int(item.get("leechers") or 0)
            size = (item.get("size") or "").strip()
            uploader = (item.get("uploader") or "").strip()

            snippet_parts = [f"S:{seeders}/L:{leechers}"]
            if size:
                snippet_parts.append(size)
            if uploader:
                snippet_parts.append(f"by {uploader}")
            snippet = " · ".join(snippet_parts)

            sr = SearchResult(title=title, url=row_url, snippet=snippet)
            sr.seeders = seeders
            sr.leechers = leechers
            sr.size = size
            sr.uploader = uploader
            results.append(sr)

        self.last_status = {
            "origin": origin,
            "url": url,
            "count": len(results),
        }
        log.info("[1337x] %s returned %d results", origin, len(results))
        return results

    # -------------------------------------------------------------------- main
    def _do_search(self, query: str, limit: int = 10) -> list[SearchResult]:
        last_err: dict | None = None
        for origin in _MIRRORS:
            try:
                results = self._try_origin(origin, query, limit)
            except Exception as e:
                log.warning("[1337x] %s raised: %s", origin, e)
                last_err = {"origin": origin, "error": str(e)}
                continue
            if results:
                return results
            last_err = self.last_status
            # brief jitter before trying the next mirror
            human_delay(0.8, 1.8)

        if last_err:
            self.last_status = last_err
        log.warning("[1337x] all mirrors failed for %r", query)
        return []
