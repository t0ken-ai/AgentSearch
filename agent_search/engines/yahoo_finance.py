"""Yahoo Finance adapter — public ticker / company / news search.

Yahoo Finance is the cheapest reliable financial data source for AI
agents. Their search returns a structured response when you hit
``https://finance.yahoo.com/lookup?s=<query>`` (or
``/lookup/equity?s=<q>`` for stocks specifically), with rows that
include symbol, name, last price, % change, exchange, type.

For news on a ticker, hitting ``https://finance.yahoo.com/quote/<TICKER>/news``
returns the news feed (still readable anonymously as of 2026).

This adapter handles the lookup case (free-text → ticker matches).
For deeper financial fetches (chart data, fundamentals, news bodies),
use ``agentsearch extract <quote_url>`` afterward.
"""

import logging
import re
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

# Yahoo's lookup table — each row is a ticker match.
ROW_SELECTORS = [
    "table[data-test='lookup-results-table'] tbody tr",
    "table tbody tr",
    "tr[data-symbol]",
]

# Cookie consent banner (EU users get hit hard).
CONSENT_BUTTONS = [
    'button[name="agree"]',
    'button:has-text("Accept all")',
    'button[aria-label*="Accept" i]',
]

PCT_RE = re.compile(r"-?\d+(?:\.\d+)?%")


class YahooFinanceEngine(BaseEngine):
    name = "yahoo_finance"

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        q = urllib.parse.quote(query)
        url = f"https://finance.yahoo.com/lookup?s={q}"
        log.info("[yfin] %s", url)
        if not safe_goto(self.page, url, timeout=30000):
            return []
        human_delay(1.5, 2.5)

        self._dismiss_consent()

        rows = []
        used = None
        for sel in ROW_SELECTORS:
            try:
                rows = self.page.query_selector_all(sel)
            except Exception:
                rows = []
            if rows:
                used = sel
                break
        if not rows:
            log.warning("[yfin] no rows")
            return []
        log.info("[yfin] selector %s → %d rows", used, len(rows))

        results: list[SearchResult] = []
        for r in rows[: limit * 2]:
            row = self._parse_row(r)
            if row and row.title:
                results.append(row)
            if len(results) >= limit:
                break
        return results

    def _dismiss_consent(self):
        for sel in CONSENT_BUTTONS:
            try:
                btn = self.page.query_selector(sel)
                if btn:
                    btn.click(timeout=2000)
                    human_delay(0.5, 1.0)
                    return
            except Exception:
                continue

    def _parse_row(self, row) -> SearchResult | None:
        # Symbol is usually in the first <a> on the row, with the company
        # name in the second cell.
        try:
            cells = row.query_selector_all("td")
        except Exception:
            cells = []
        if not cells or len(cells) < 2:
            return None

        symbol = ""
        href = ""
        try:
            a = cells[0].query_selector("a")
            if a:
                symbol = (a.inner_text() or "").strip()
                href = a.get_attribute("href") or ""
        except Exception:
            pass
        if href.startswith("/"):
            href = "https://finance.yahoo.com" + href

        # Cell content extraction is positional but tolerant — Yahoo's
        # column order is Symbol | Name | Last Price | Industry | Type | Exchange.
        def cell_text(i: int) -> str:
            try:
                return (cells[i].inner_text() or "").strip() if i < len(cells) else ""
            except Exception:
                return ""

        name = cell_text(1)
        last_price = cell_text(2)
        industry_or_change = cell_text(3)
        type_ = cell_text(4)
        exchange = cell_text(5)

        # On the lookup page we may not always have Last Price; sometimes
        # column 3 holds % change. Detect that.
        pct_change = ""
        if PCT_RE.fullmatch(industry_or_change):
            pct_change = industry_or_change

        snippet_bits = []
        if name:
            snippet_bits.append(name)
        if last_price:
            snippet_bits.append(last_price)
        if pct_change:
            snippet_bits.append(pct_change)
        if exchange:
            snippet_bits.append(exchange)
        if type_:
            snippet_bits.append(type_)
        snippet = " · ".join(snippet_bits)

        if not symbol:
            return None
        result = SearchResult(title=f"{symbol} — {name}" if name else symbol, url=href, snippet=snippet)
        result.__dict__.update({
            "symbol": symbol,
            "name": name,
            "last_price": last_price,
            "pct_change": pct_change,
            "exchange": exchange,
            "asset_type": type_,
        })
        return result
