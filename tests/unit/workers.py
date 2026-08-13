"""Pickle-safe worker functions used by multiprocessing tests.

The production fan-out deliberately uses the ``spawn`` start method. Test
runners therefore have to live in an importable module instead of being local
closures, matching the constraint production engine runners must satisfy.
"""

from __future__ import annotations

import time
from typing import Any


def fake_engine_runner(
    engine_name: str,
    query: str,
    limit: int,
    headless: bool,
) -> dict[str, Any]:
    """Return deterministic results, with ``sleep:<seconds>`` for hangs."""
    started = time.monotonic()
    if engine_name.startswith("sleep:"):
        time.sleep(float(engine_name.split(":", 1)[1]))
    if engine_name == "raise":
        raise RuntimeError("intentional worker failure")

    safe_name = engine_name.replace(":", "-")
    return {
        "engine": engine_name,
        "ok": True,
        "count": 1,
        "results": [{
            "title": f"{query} from {engine_name}",
            "url": f"https://example.test/{safe_name}",
            "snippet": f"limit={limit}; headless={headless}",
        }],
        "elapsed_s": round(time.monotonic() - started, 3),
    }
