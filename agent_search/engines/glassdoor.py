"""Glassdoor adapter — company search.

Glassdoor's full review/salary content is paywalled and increasingly
login-walled, but the public *company search* SERP is reachable
anonymously and gives the agent the most actionable data point: the
list of employers matching a query, plus their overall rating, review
count, and primary industry/location.

For deeper data (per-review text, salary breakdowns), the user can run
``agentsearch login glassdoor`` and pass ``--profile glassdoor`` — the
extract command will then carry their session.

Strategy:
1. Hit https://www.glassdoor.com/Search/results.htm?keyword=<query>
   (deep-links to the "Find companies" tab, no JS gate).
2. Parse the result list. Glassdoor uses heavily class-mangled DOM but
   the cards consistently include an ``[data-test='employer-card']``
   wrapper and an ``[data-test='employer-name']`` anchor. We try those
   first, then fall back to text scanning.
3. Extract: name, url, rating, review_count, industry/location summary.

Anti-bot: Glassdoor uses Akamai / DataDome on some pages — CloakBrowser
gets through, but expect occasional empty SERPs. The BaseEngine retry
loop catches those.
"""

import logging
import re
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

CARD_SELECTORS = [
    "[data-test='employer-card']",
    "[data-test='employer-result']",
    "div.employerCard",
    "li[data-test='employer-result']",
    "li.search-result",
]

NAME_SELECTORS = [
    "[data-test='employer-name']",
    "a[data-test='employer-name']",
    "h2 a",
    "h3 a",
    "a.employerName",
]

RATING_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:/\s*5)?\s*(?:\bstars?\b|★)?", re.IGNORECASE)
REVIEWS_RE = re.compile(r"([\d,]+)\s*review", re.IGNORECASE)


class GlassdoorEngine(BaseEngine):
    name = "glassdoor"

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        q = urllib.parse.quote(query)
        url = (
            "https://www.glassdoor.com/Search/results.htm"
            f"?keyword={q}&locType=N&locId=1"
        )
        log.info("[glassdoor] %s", url)
        if not safe_goto(self.page, url, timeout=30000):
            return []
        human_delay(2.0, 3.5)

        # Glassdoor often pops a sign-up modal. Best-effort dismiss.
        for sel in [
            'button[aria-label="Close"]',
            'button[aria-label*="close" i]',
            "button.modal_closeIcon",
            ".SVGInline-svg",
        ]:
            try:
                btn = self.page.query_selector(sel)
                if btn:
                    btn.click(timeout=1500)
                    human_delay(0.5, 1.0)
                    break
            except Exception:
                continue

        cards = []
        used_sel = None
        for sel in CARD_SELECTORS:
            try:
                cards = self.page.query_selector_all(sel)
            except Exception:
                cards = []
            if cards:
                used_sel = sel
                break

        if not cards:
            log.warning("[glassdoor] no cards matched any selector")
            return []
        log.info("[glassdoor] selector %s → %d cards", used_sel, len(cards))

        results: list[SearchResult] = []
        for card in cards[: limit * 2]:
            r = self._parse_card(card)
            if r and r.title:
                results.append(r)
            if len(results) >= limit:
                break
        return results

    def _parse_card(self, card) -> SearchResult | None:
        try:
            text = (card.inner_text() or "").strip()
        except Exception:
            text = ""

        name = ""
        href = ""
        for sel in NAME_SELECTORS:
            try:
                el = card.query_selector(sel)
                if not el:
                    continue
                name = (el.inner_text() or "").strip()
                href = el.get_attribute("href") or ""
                if name:
                    break
            except Exception:
                continue

        if href.startswith("/"):
            href = "https://www.glassdoor.com" + href

        # Rating — Glassdoor often exposes a [data-test='rating'] container.
        rating: float | None = None
        try:
            rel = card.query_selector("[data-test='rating'], .ratingNumber, .ratingNum")
            if rel:
                t = (rel.inner_text() or "").strip()
                m = RATING_RE.match(t)
                if m:
                    rating = float(m.group(1))
        except Exception:
            pass

        review_count: int | None = None
        rm = REVIEWS_RE.search(text)
        if rm:
            try:
                review_count = int(rm.group(1).replace(",", ""))
            except ValueError:
                pass

        # Industry / location is usually rendered as a short subtitle line.
        sub = ""
        try:
            for el in card.query_selector_all("[data-test='employer-industry'], [data-test='employer-short-desc'], div.industry"):
                sub_text = (el.inner_text() or "").strip()
                if sub_text:
                    sub = sub_text
                    break
        except Exception:
            pass

        snippet_bits = []
        if rating is not None:
            snippet_bits.append(f"⭐ {rating}")
        if review_count is not None:
            snippet_bits.append(f"{review_count} reviews")
        if sub:
            snippet_bits.append(sub)
        snippet = " · ".join(snippet_bits)

        if not name:
            return None
        result = SearchResult(title=name, url=href, snippet=snippet, score=review_count)
        result.__dict__.update({
            "rating": rating,
            "review_count": review_count,
            "industry": sub,
        })
        return result
