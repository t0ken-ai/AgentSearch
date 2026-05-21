"""HuggingFace Hub model search adapter via the official REST API.

API
---
``https://huggingface.co/api/models?search=<query>&limit=<n>&full=true&config=true``
returns a JSON array of model objects. Public, no auth needed for read
(rate-limited at ~1000 req/h per IP). Documented at
https://huggingface.co/docs/hub/api .

Each :class:`SearchResult` carries:

* ``model_id``    — full hub id, e.g. ``"meta-llama/Llama-3.1-8B"``
* ``author``      — namespace owner (org or user)
* ``downloads``   — last 30-day downloads (integer)
* ``likes``       — total likes (integer)
* ``pipeline_tag`` — task tag, e.g. ``"text-generation"``
* ``library``     — main library (``"transformers"`` / ``"diffusers"`` / …)
* ``tags``        — list of tags (license, language, model-type, …)
* ``last_modified`` — ISO8601 modified timestamp
"""

from __future__ import annotations

import json
import logging
import urllib.parse

from ..core import safe_goto, human_delay
from .base import BaseEngine, SearchResult

log = logging.getLogger(__name__)

HF_HOME = "https://huggingface.co/"
HF_API = "https://huggingface.co/api/models"


class HuggingFaceEngine(BaseEngine):
    """HuggingFace Hub model search via the official REST API."""

    name = "huggingface"
    max_retries = 1

    def __init__(self, page, kind: str = "models"):
        """``kind`` is ``"models"`` (default), ``"datasets"`` or ``"spaces"``."""
        super().__init__(page)
        self.kind = kind
        self.last_status: dict = {}

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # Prime origin to share TLS chain.
        if not safe_goto(self.page, HF_HOME, timeout=20000, retries=1):
            log.warning("[huggingface] failed to reach origin")
            return []
        human_delay(0.3, 0.7)

        params = {
            "search": query,
            "limit": str(max(limit, 5)),
            "sort": "downloads",
            "direction": "-1",
        }
        api_url = f"https://huggingface.co/api/{self.kind}?{urllib.parse.urlencode(params)}"
        log.info("[huggingface] fetching %s", api_url)

        try:
            resp = self.page.evaluate(
                """async (url) => {
                  const r = await fetch(url, {credentials: 'omit',
                                              headers: {'Accept': 'application/json'}});
                  return {ok: r.ok, status: r.status, text: await r.text()};
                }""",
                api_url,
            )
        except Exception as e:
            log.warning("[huggingface] fetch raised: %s", e)
            return []

        if not (resp and resp.get("ok")):
            log.warning("[huggingface] HTTP %s", resp and resp.get("status"))
            self.last_status = {
                "url": api_url,
                "http_status": resp and resp.get("status"),
            }
            return []

        try:
            items = json.loads(resp.get("text") or "[]")
        except json.JSONDecodeError as e:
            log.warning("[huggingface] JSON parse failed: %s", e)
            return []

        if not isinstance(items, list):
            log.warning("[huggingface] expected list, got %s", type(items))
            return []

        self.last_status = {
            "url": api_url,
            "result_count": len(items),
            "http_status": resp.get("status"),
        }

        results: list[SearchResult] = []
        for item in items:
            if len(results) >= limit:
                break
            model_id = (item.get("id") or item.get("modelId") or "").strip()
            if not model_id:
                continue

            url = f"https://huggingface.co/{model_id}"
            if self.kind == "datasets":
                url = f"https://huggingface.co/datasets/{model_id}"
            elif self.kind == "spaces":
                url = f"https://huggingface.co/spaces/{model_id}"

            author = (item.get("author") or "").strip()
            if not author and "/" in model_id:
                author = model_id.split("/", 1)[0]

            downloads = item.get("downloads") or 0
            likes = item.get("likes") or 0
            pipeline_tag = (item.get("pipeline_tag") or "").strip()
            library = (item.get("library_name") or "").strip()
            tags = item.get("tags") or []
            if not isinstance(tags, list):
                tags = []
            last_modified = (item.get("lastModified") or item.get("last_modified") or "")[:10]

            head = []
            if author:
                head.append(author)
            if pipeline_tag:
                head.append(pipeline_tag)
            if library:
                head.append(library)
            if downloads:
                head.append(f"⬇ {self._fmt_count(downloads)}")
            if likes:
                head.append(f"♥ {self._fmt_count(likes)}")
            if last_modified:
                head.append(last_modified)
            head_text = " · ".join(head)
            tag_text = ""
            if tags:
                # Pick the first 5 distinctive tags (skip generic markers).
                useful = [t for t in tags if isinstance(t, str)
                          and not t.startswith(("region:", "license:other"))]
                if useful:
                    tag_text = "tags: " + ", ".join(useful[:5])
            snippet_parts = []
            if head_text:
                snippet_parts.append(head_text)
            if tag_text:
                snippet_parts.append(tag_text)
            snippet = " — ".join(snippet_parts)[:400]

            r = SearchResult(title=model_id, url=url, snippet=snippet)
            r.model_id = model_id              # type: ignore[attr-defined]
            r.author = author                  # type: ignore[attr-defined]
            r.downloads = int(downloads)       # type: ignore[attr-defined]
            r.likes = int(likes)               # type: ignore[attr-defined]
            r.pipeline_tag = pipeline_tag      # type: ignore[attr-defined]
            r.library = library                # type: ignore[attr-defined]
            r.tags = tags                      # type: ignore[attr-defined]
            r.last_modified = last_modified    # type: ignore[attr-defined]
            results.append(r)
        return results

    @staticmethod
    def _fmt_count(n: int) -> str:
        if n >= 1_000_000_000:
            return f"{n / 1_000_000_000:.1f}B"
        if n >= 1_000_000:
            return f"{n / 1_000_000:.1f}M"
        if n >= 1_000:
            return f"{n / 1_000:.1f}K"
        return str(n)
