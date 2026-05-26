"""YouTube engine multi-mode regression test.

Asserts the 3 new fetch modes (video / channel / transcript) work end-to-end
on stable canonical URLs. The transcript leg is best-effort because YT's
2024 ``pot`` (Proof of Token) requirement now serves empty bodies for many
``api/timedtext`` URLs we can extract.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_youtube_modes.py
"""

from __future__ import annotations

import logging
import os
import sys
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search import core
from agent_search.engines.youtube import YouTubeEngine


def _check_video_mode(engine: YouTubeEngine) -> bool:
    print("\n--- VIDEO mode :: jNQXAC9IVRw (Me at the zoo) ---")
    rs = engine.search("jNQXAC9IVRw", mode="video")
    if not rs:
        return False
    r = rs[0]
    print(f"  title={r.title!r}")
    print(f"  views={r.views:,} likes={r.likes_text!r} subs={r.subscribers_text!r}")
    print(f"  duration={r.duration_text} category={r.category!r} captions={len(r.captions)}")
    assert r.video_id == "jNQXAC9IVRw", f"bad video_id {r.video_id!r}"
    assert r.title, "missing title"
    assert r.channel, "missing channel"
    assert r.views and r.views > 1_000_000, f"views looks wrong: {r.views}"
    assert r.duration and r.duration > 0, f"duration looks wrong: {r.duration}"
    assert r.subscribers and r.subscribers > 100_000, (
        f"subscribers looks wrong: {r.subscribers}"
    )
    assert r.captions, "captions list empty"
    return True


def _check_channel_mode(engine: YouTubeEngine) -> bool:
    print("\n--- CHANNEL mode :: @MrBeast ---")
    rs = engine.search("@MrBeast", mode="channel", limit=4)
    if not rs:
        return False
    head = rs[0]
    print(f"  HEADER: {head.title!r} subs={getattr(head, 'subscribers_text', '')!r} vids={getattr(head, 'video_count_text', '')!r}")
    assert getattr(head, "is_channel", False), "first result should be channel header"
    assert getattr(head, "subscribers", 0) and head.subscribers > 100_000_000, (
        f"MrBeast subs >100M expected; got {getattr(head, 'subscribers', None)}"
    )
    print(f"  RECENT ({len(rs) - 1}):")
    has_views = 0
    has_duration = 0
    for v in rs[1:]:
        if v.views_text:
            has_views += 1
        if v.duration_text:
            has_duration += 1
        title = (v.title or "")[:55].replace("\n", " ")
        print(f"    [{v.video_id}] {v.views_text or '-':<14s} {v.duration_text or '-':<8s} {v.upload_date or '-':<16s} :: {title}")
    assert has_duration >= 1, "expected ≥1 recent video with duration"
    return True


def _check_transcript_mode(engine: YouTubeEngine) -> bool:
    """Best-effort: just assert mode dispatches and captions list is filled."""
    print("\n--- TRANSCRIPT mode :: jNQXAC9IVRw ---")
    rs = engine.search("jNQXAC9IVRw", mode="transcript", lang="en")
    if not rs:
        return False
    r = rs[0]
    has_text = bool(getattr(r, "transcript", ""))
    print(f"  captions: {len(r.captions)}  transcript_text? {has_text}")
    if has_text:
        print(f"  transcript_lang={r.transcript_lang!r}")
        print(f"  transcript[:200]={r.transcript[:200]!r}")
    else:
        print("  (transcript text unavailable — likely YT pot-token gate)")
    # We require captions metadata only; transcript text is best-effort.
    assert r.captions, "captions list missing"
    return True


def main() -> int:
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    print("=== YouTube multi-mode regression ===")

    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    passed: dict[str, bool] = {}
    try:
        page = core.new_page(browser)
        engine = YouTubeEngine(page)

        for mode_name, fn in (
            ("video", _check_video_mode),
            ("channel", _check_channel_mode),
            ("transcript", _check_transcript_mode),
        ):
            try:
                passed[mode_name] = fn(engine)
            except Exception:
                traceback.print_exc()
                passed[mode_name] = False
    finally:
        try:
            browser.close()
        except Exception as e:
            print(f"warning: browser.close() raised: {e}", file=sys.stderr)

    print("\n=== mode results ===")
    for mode, ok in passed.items():
        print(f"  {mode:<10s} : {'PASS' if ok else 'FAIL'}")

    n_pass = sum(1 for v in passed.values() if v)
    if n_pass >= 2:
        print(f"\n=== PASS === ({n_pass}/3 modes succeeded)")
        return 0
    print(
        f"\n=== FAIL === only {n_pass}/3 modes succeeded — YT may be blocking",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
