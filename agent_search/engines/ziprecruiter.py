"""ZipRecruiter job search adapter.

ZipRecruiter has a public search page that doesn't require login for the
SERP — `https://www.ziprecruiter.com/jobs-search?search=<q>&location=<l>`.
Cards include title, company, location, salary, posting age, and a short
summary.

Strategy:
1. Hit the public jobs-search URL.
2. Parse `article` elements (each card is rendered as `<article>`).
3. Extract via a mix of class selectors and aria-labels, with text-pattern
   fallbacks since ZipRecruiter randomly A/B tests its DOM.
"""

import logging
import re
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

# Candidate result containers in priority order.
RESULT_SELECTORS = [
    "article.job_result",
    "article[data-id]",
    "article",
]

# Salary detection — broad regex for "$X-$Y / hour" / "$Xk - $Yk a year".
SALARY_RE = re.compile(
    r"\$\s*[\d,\.]+\s*(?:k|K)?(?:\s*[-–to]+\s*\$?\s*[\d,\.]+\s*(?:k|K)?)?"
    r"(?:\s*(?:per|/|a)\s*(?:hour|hr|year|yr|month|mo|week|wk))?",
    re.IGNORECASE,
)


class ZipRecruiterEngine(BaseEngine):
    name = "ziprecruiter"

    def _wait_past_cloudflare(self, timeout: int = 20000) -> None:
        """Wait until the page title is no longer Cloudflare's interstitial.

        CloakBrowser passes the JS challenge but it takes a few seconds.
        Title transitions from 'Just a moment...' -> the real page title.
        We poll up to ``timeout`` ms.
        """
        import time as _time
        deadline = _time.time() + timeout / 1000.0
        while _time.time() < deadline:
            try:
                t = (self.page.title() or "").lower()
            except Exception:
                t = ""
            if t and "just a moment" not in t and "checking your" not in t and "attention required" not in t:
                return
            _time.sleep(0.4)
        log.warning("[ziprecruiter] still on Cloudflare interstitial after %dms", timeout)

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # Heuristic: split "<role> in <location>" into separate query params
        # (ZipRecruiter's UI does the same when you submit the form).
        location = ""
        q = query
        m = re.match(r"(?i)^(.*?)\s+in\s+(.+)$", query.strip())
        if m:
            q, location = m.group(1).strip(), m.group(2).strip()

        params = {"search": q}
        if location:
            params["location"] = location
        url = "https://www.ziprecruiter.com/jobs-search?" + urllib.parse.urlencode(params)
        log.info("[ziprecruiter] %s", url)

        if not safe_goto(self.page, url, timeout=30000):
            return []

        # ZipRecruiter sits behind Cloudflare. CloakBrowser passes the
        # challenge but we have to wait for it to finish — the title says
        # "Just a moment..." while the JS challenge runs.
        self._wait_past_cloudflare(timeout=20000)
        human_delay(1.5, 3.0)

        items = []
        used = None
        for sel in RESULT_SELECTORS:
            try:
                items = self.page.query_selector_all(sel)
            except Exception:
                items = []
            if items:
                used = sel
                break
        if not items:
            return []

        log.info("[ziprecruiter] selector %s → %d cards", used, len(items))
        results: list[SearchResult] = []
        for card in items[: limit * 2]:
            r = self._parse_card(card)
            if r and r.title:
                results.append(r)
            if len(results) >= limit:
                break
        return results

    # -------------------------------------------------------------- card parse

    def _parse_card(self, card) -> SearchResult | None:
        try:
            text = (card.inner_text() or "").strip()
        except Exception:
            text = ""

        # Title is usually the card's primary anchor.
        title = ""
        href = ""
        try:
            link = (
                card.query_selector("a.job_link")
                or card.query_selector("h2 a")
                or card.query_selector("h3 a")
                or card.query_selector("a[href*='/jobs/']")
            )
            if link:
                title = (link.inner_text() or "").strip()
                href = link.get_attribute("href") or ""
        except Exception:
            pass

        if href.startswith("/"):
            href = "https://www.ziprecruiter.com" + href

        company = ""
        try:
            comp_el = (
                card.query_selector("a.t_org_link")
                or card.query_selector("[data-testid='job-card-company']")
                or card.query_selector(".company_name")
            )
            if comp_el:
                company = (comp_el.inner_text() or "").strip()
        except Exception:
            pass

        location = ""
        try:
            loc_el = (
                card.query_selector("[data-testid='job-card-location']")
                or card.query_selector(".location")
                or card.query_selector(".job_location")
            )
            if loc_el:
                location = (loc_el.inner_text() or "").strip()
        except Exception:
            pass

        # Salary — try a dedicated element first, then regex over text.
        salary = ""
        try:
            sal_el = card.query_selector(".job_salary, [data-testid='job-card-salary']")
            if sal_el:
                salary = (sal_el.inner_text() or "").strip()
        except Exception:
            pass
        if not salary:
            m = SALARY_RE.search(text)
            if m:
                salary = m.group(0).strip()

        snippet_bits = []
        if company:
            snippet_bits.append(company)
        if location:
            snippet_bits.append(location)
        if salary:
            snippet_bits.append(salary)
        snippet = " · ".join(snippet_bits)

        # First non-title paragraph as the description hint.
        if text:
            for line in text.split("\n"):
                line = line.strip()
                if not line or line == title or line == company or line == location:
                    continue
                if any(line.endswith(p) for p in [salary, location, company]):
                    continue
                if len(line) > 40:
                    snippet += " · " + line[:200]
                    break

        if not title:
            return None
        result = SearchResult(title=title, url=href, snippet=snippet)
        result.__dict__.update({
            "company": company,
            "location": location,
            "salary": salary,
        })
        return result
