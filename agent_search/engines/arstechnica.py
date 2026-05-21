"""Ars Technica search adapter (tech / science).

Direct path: https://arstechnica.com/search/?q=<query>
Ars Technica's on-site search is a Google CSE iframe; we don't try to
parse it. Instead we go straight to the Google/Bing/DDG site: fallback
chain (the direct extractor is provided as a no-op so subclass contract
is preserved).
"""

from __future__ import annotations

import re
from ._news_base import NewsBaseEngine


class ArsTechnicaEngine(NewsBaseEngine):
    name = "arstechnica"
    HOME_URL = "https://arstechnica.com/"
    SEARCH_URL = "https://arstechnica.com/search/?q={query}"
    HOST_RE = re.compile(
        r"https?://(?:www\.)?arstechnica\.com/(?:[a-z\-]+/)+", re.IGNORECASE,
    )
    SITE_TITLE_SUFFIXES = (" - Ars Technica", " | Ars Technica")

    # Direct path: Google CSE iframe — we skip the iframe and fall back.
    def _parse_direct(self, limit: int) -> list[dict]:
        return []
