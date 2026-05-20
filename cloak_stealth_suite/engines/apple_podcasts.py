"""Apple Podcasts search adapter via the iTunes Search API.

API
---
``https://itunes.apple.com/search?term=<q>&media=podcast&limit=<n>&country=US``
returns JSON. Public, no auth, documented at
https://performance-partners.apple.com/search-api . Returns one result
per podcast show; episode-level search uses ``entity=podcastEpisode``.

We accept ``country`` (defaulting to ``"US"``) so callers can scope
results to a regional iTunes catalog.

Each :class:`SearchResult` carries:

* ``track_id``     — Apple's numeric podcast id (also the show id)
* ``content_type`` — ``"podcast"`` or ``"episode"``
* ``artist``       — show creator name
* ``genre``        — primary genre
* ``country``      — country code used
* ``feed_url``     — RSS feed URL (only for podcast type)
* ``release_date`` — show or episode release date
* ``image_url``    — 600x600 artwork URL
"""

from __future__ import annotations

import json
import logging
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

ITUNES_HOME = "https://itunes.apple.com/"
ITUNES_API = "https://itunes.apple.com/search"


class ApplePodcastsEngine(BaseEngine):
    """Apple Podcasts search via the iTunes Search API."""

    name = "apple_podcasts"
    max_retries = 1

    def __init__(self, page, country: str = "US", entity: str = "podcast"):
        super().__init__(page)
        self.country = country
        self.entity = entity  # "podcast" or "podcastEpisode"
        self.last_status: dict = {}

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # Prime origin to share TLS chain with the API call.
        if not safe_goto(self.page, ITUNES_HOME, timeout=20000, retries=1):
            log.warning("[apple_podcasts] failed to reach origin")
            return []
        human_delay(0.3, 0.7)

        params = {
            "term": query,
            "media": "podcast",
            "entity": self.entity,
            "limit": str(max(limit, 5)),
            "country": self.country,
        }
        api_url = f"{ITUNES_API}?{urllib.parse.urlencode(params)}"
        log.info("[apple_podcasts] fetching %s", api_url)

        try:
            resp = self.page.evaluate(
                """async (url) => {
                  const r = await fetch(url, {credentials: 'omit'});
                  return {ok: r.ok, status: r.status, text: await r.text()};
                }""",
                api_url,
            )
        except Exception as e:
            log.warning("[apple_podcasts] fetch raised: %s", e)
            return []

        if not (resp and resp.get("ok")):
            log.warning("[apple_podcasts] HTTP %s", resp and resp.get("status"))
            return []

        try:
            data = json.loads(resp.get("text") or "{}")
        except json.JSONDecodeError as e:
            log.warning("[apple_podcasts] JSON parse failed: %s", e)
            return []

        items = data.get("results") or []
        self.last_status = {
            "url": api_url,
            "result_count": data.get("resultCount", 0),
            "http_status": resp.get("status"),
        }

        results: list[SearchResult] = []
        for item in items:
            if len(results) >= limit:
                break
            kind = (item.get("kind") or item.get("wrapperType") or "").lower()
            is_episode = "episode" in kind
            content_type = "episode" if is_episode else "podcast"

            title = (item.get("trackName") or item.get("collectionName") or "").strip()
            url = (item.get("trackViewUrl") or item.get("collectionViewUrl") or "").strip()
            if not title or not url:
                continue

            artist = (item.get("artistName") or "").strip()
            genre = (item.get("primaryGenreName") or "").strip()
            track_id = str(item.get("trackId") or item.get("collectionId") or "")
            feed_url = (item.get("feedUrl") or "").strip()
            release_date = (item.get("releaseDate") or "")[:10]
            image_url = (item.get("artworkUrl600")
                         or item.get("artworkUrl100")
                         or item.get("artworkUrl60") or "").strip()
            description = (item.get("description") or "").strip()

            head = []
            if artist:
                head.append(artist)
            if genre:
                head.append(genre)
            if release_date:
                head.append(release_date)
            head_text = " · ".join(head)
            snippet_parts = []
            if head_text:
                snippet_parts.append(head_text)
            if description:
                snippet_parts.append(" ".join(description.split())[:240])
            snippet = " — ".join(snippet_parts)[:400]

            r = SearchResult(title=title, url=url, snippet=snippet)
            r.track_id = track_id          # type: ignore[attr-defined]
            r.content_type = content_type  # type: ignore[attr-defined]
            r.artist = artist              # type: ignore[attr-defined]
            r.genre = genre                # type: ignore[attr-defined]
            r.country = self.country       # type: ignore[attr-defined]
            r.feed_url = feed_url          # type: ignore[attr-defined]
            r.release_date = release_date  # type: ignore[attr-defined]
            r.image_url = image_url        # type: ignore[attr-defined]
            r.description = description    # type: ignore[attr-defined]
            results.append(r)
        return results
