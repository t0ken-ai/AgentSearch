"""Probe YouTube watch / channel / playlist pages for guest-extractable data.

Maps which JSON paths reliably yield:
- video: like_count, channel subscribers, transcript URL, video description, keywords
- channel: subscriber count, video grid
- playlist: title, video list, owner

Run:
    ~/tools/cloakbrowser/venv/bin/python tests/probe_youtube_watch.py
"""

from __future__ import annotations

import json
import logging
import os
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search import core
from agent_search.core import safe_goto, human_delay


CASES = [
    ("watch_short",   "https://www.youtube.com/watch?v=dQw4w9WgXcQ"),  # Rick Astley, ~1.5B views
    ("watch_recent",  "https://www.youtube.com/watch?v=jNQXAC9IVRw"),  # 'Me at the zoo' — first YT video
    ("channel_handle", "https://www.youtube.com/@MrBeast"),
    ("playlist",       "https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"),
]


def main():
    logging.basicConfig(level=logging.WARNING)
    cfg = core.BrowserConfig(headless=True, humanize=True)
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        # Warm-up homepage so consent settles
        safe_goto(page, "https://www.youtube.com/", timeout=20000, retries=1)
        human_delay(2, 3)
        # Click any consent button
        for sel in ["button[aria-label*='Accept all' i]", "button[aria-label*='Reject all' i]"]:
            try:
                btn = page.query_selector(sel)
                if btn:
                    btn.click(timeout=2000)
                    human_delay(1, 2)
                    break
            except Exception:
                pass

        for label, url in CASES:
            print(f"\n=== {label} :: {url} ===")
            ok = safe_goto(page, url, timeout=25000, retries=1)
            print(f"  goto ok = {ok}")
            human_delay(2, 3.5)
            try:
                page.evaluate("() => window.scrollBy(0, 200)")
            except Exception:
                pass
            human_delay(0.5, 1.0)

            try:
                # Read both JSON blobs
                blobs = page.evaluate(
                    """
                    () => ({
                        title: document.title,
                        url: location.href,
                        ytInitialData_keys: Object.keys(window.ytInitialData || {}),
                        ytInitialPlayerResponse_keys: Object.keys(window.ytInitialPlayerResponse || {}),
                        has_initial_data: !!window.ytInitialData,
                        has_player_response: !!window.ytInitialPlayerResponse,
                    })
                    """
                )
            except Exception as e:
                print(f"  err: {e}")
                continue

            print(f"  page url   : {blobs.get('url')}")
            print(f"  page title : {blobs.get('title')}")
            print(f"  has ytInitialData: {blobs.get('has_initial_data')} ({blobs.get('ytInitialData_keys')})")
            print(f"  has ytInitialPlayerResponse: {blobs.get('has_player_response')} ({blobs.get('ytInitialPlayerResponse_keys')})")

            if blobs.get('has_player_response'):
                # Pull out videoDetails + captionTracks
                vd = page.evaluate(
                    """
                    () => {
                        const pr = window.ytInitialPlayerResponse;
                        if (!pr) return null;
                        const vd = pr.videoDetails || {};
                        const cap = ((pr.captions || {}).playerCaptionsTracklistRenderer || {}).captionTracks || [];
                        return {
                            videoDetails: {
                                title: vd.title,
                                videoId: vd.videoId,
                                lengthSeconds: vd.lengthSeconds,
                                viewCount: vd.viewCount,
                                author: vd.author,
                                channelId: vd.channelId,
                                isLiveContent: vd.isLiveContent,
                                shortDescriptionLen: (vd.shortDescription || '').length,
                                shortDescription_head: (vd.shortDescription || '').slice(0, 120),
                                keywords: (vd.keywords || []).slice(0, 5),
                                thumbnails_count: ((vd.thumbnail || {}).thumbnails || []).length,
                                thumbnail_max: (vd.thumbnail && vd.thumbnail.thumbnails && vd.thumbnail.thumbnails.length)
                                    ? vd.thumbnail.thumbnails[vd.thumbnail.thumbnails.length-1] : null,
                            },
                            captionTracks_count: cap.length,
                            captionTracks_sample: cap.slice(0, 3).map(c => ({
                                languageCode: c.languageCode,
                                kind: c.kind,
                                name: ((c.name || {}).simpleText || (c.name || {}).runs && c.name.runs[0] && c.name.runs[0].text),
                                hasBaseUrl: !!c.baseUrl,
                                baseUrl_prefix: (c.baseUrl || '').slice(0, 100),
                            })),
                            microformat: pr.microformat && pr.microformat.playerMicroformatRenderer && {
                                publishDate: pr.microformat.playerMicroformatRenderer.publishDate,
                                uploadDate: pr.microformat.playerMicroformatRenderer.uploadDate,
                                category: pr.microformat.playerMicroformatRenderer.category,
                            },
                        };
                    }
                    """
                )
                print(f"\n  videoDetails: {json.dumps(vd, ensure_ascii=False, indent=2) if vd else None}")

            if blobs.get('has_initial_data'):
                # Channel header / playlist header / video like-count signals
                header = page.evaluate(
                    """
                    () => {
                        const id = window.ytInitialData;
                        const out = {};
                        // Channel: header.c4TabbedHeaderRenderer | pageHeaderRenderer
                        if (id.header) {
                            out.header_keys = Object.keys(id.header);
                            const c4 = id.header.c4TabbedHeaderRenderer;
                            const ph = id.header.pageHeaderRenderer;
                            if (c4) {
                                out.channel_c4 = {
                                    title: c4.title,
                                    subscriberCountText: c4.subscriberCountText && (c4.subscriberCountText.simpleText || (c4.subscriberCountText.runs && c4.subscriberCountText.runs[0] && c4.subscriberCountText.runs[0].text)),
                                    videosCountText: c4.videosCountText && (c4.videosCountText.simpleText || (c4.videosCountText.runs && c4.videosCountText.runs[0].text)),
                                    channelId: c4.channelId,
                                };
                            } else if (ph) {
                                out.channel_ph = {pageTitle: ph.pageTitle};
                                // metadata is inside content / metadata.contentMetadataViewModel
                                try {
                                    const meta = ph.content && ph.content.pageHeaderViewModel && ph.content.pageHeaderViewModel.metadata && ph.content.pageHeaderViewModel.metadata.contentMetadataViewModel;
                                    if (meta) {
                                        out.channel_ph.metadata_count = (meta.metadataRows || []).length;
                                        out.channel_ph.first_row = JSON.stringify(meta.metadataRows && meta.metadataRows[0]).slice(0, 300);
                                    }
                                } catch (_) {}
                            }
                            // Playlist header
                            const pl = id.header.playlistHeaderRenderer;
                            if (pl) {
                                out.playlist = {
                                    title: pl.title && (pl.title.simpleText || pl.title.runs && pl.title.runs[0].text),
                                    descriptionText: pl.descriptionText && (pl.descriptionText.simpleText || pl.descriptionText.runs && pl.descriptionText.runs[0].text),
                                    numVideosText: pl.numVideosText && pl.numVideosText.runs && pl.numVideosText.runs[0].text,
                                    viewCountText: pl.viewCountText && pl.viewCountText.simpleText,
                                };
                            }
                        }
                        // Watch: dig the video primary info renderer for like count
                        try {
                            const cnt = id.contents && id.contents.twoColumnWatchNextResults && id.contents.twoColumnWatchNextResults.results && id.contents.twoColumnWatchNextResults.results.results && id.contents.twoColumnWatchNextResults.results.results.contents;
                            if (cnt) {
                                const primary = cnt.find(c => c.videoPrimaryInfoRenderer);
                                const secondary = cnt.find(c => c.videoSecondaryInfoRenderer);
                                if (primary) {
                                    const vp = primary.videoPrimaryInfoRenderer;
                                    out.primary = {
                                        title: vp.title && (vp.title.simpleText || vp.title.runs && vp.title.runs[0].text),
                                        viewCount_short: vp.viewCount && vp.viewCount.videoViewCountRenderer && vp.viewCount.videoViewCountRenderer.shortViewCount && vp.viewCount.videoViewCountRenderer.shortViewCount.simpleText,
                                        viewCount_full: vp.viewCount && vp.viewCount.videoViewCountRenderer && vp.viewCount.videoViewCountRenderer.viewCount && vp.viewCount.videoViewCountRenderer.viewCount.simpleText,
                                        dateText: vp.dateText && vp.dateText.simpleText,
                                        relativeDateText: vp.relativeDateText && vp.relativeDateText.simpleText,
                                    };
                                    // Like-count: walk videoActions.menuRenderer.topLevelButtons
                                    try {
                                        const tlb = vp.videoActions && vp.videoActions.menuRenderer && vp.videoActions.menuRenderer.topLevelButtons || [];
                                        for (const b of tlb) {
                                            const sm = b.segmentedLikeDislikeButtonViewModel;
                                            if (sm) {
                                                out.like_count_block = JSON.stringify(sm).slice(0, 500);
                                                break;
                                            }
                                            const blr = b.toggleButtonRenderer;
                                            if (blr) {
                                                out.like_count_block_legacy = JSON.stringify(blr).slice(0, 500);
                                                break;
                                            }
                                        }
                                    } catch (_) {}
                                }
                                if (secondary) {
                                    const vs = secondary.videoSecondaryInfoRenderer;
                                    out.secondary = {
                                        owner_subscriberCountText: vs.owner && vs.owner.videoOwnerRenderer && vs.owner.videoOwnerRenderer.subscriberCountText && (vs.owner.videoOwnerRenderer.subscriberCountText.simpleText || vs.owner.videoOwnerRenderer.subscriberCountText.runs && vs.owner.videoOwnerRenderer.subscriberCountText.runs[0] && vs.owner.videoOwnerRenderer.subscriberCountText.runs[0].text),
                                        owner_title: vs.owner && vs.owner.videoOwnerRenderer && vs.owner.videoOwnerRenderer.title && vs.owner.videoOwnerRenderer.title.runs && vs.owner.videoOwnerRenderer.title.runs[0].text,
                                    };
                                }
                            }
                        } catch (_) {}
                        return out;
                    }
                    """
                )
                if header:
                    print(f"\n  ytInitialData header / watch dig: {json.dumps(header, ensure_ascii=False, indent=2)[:2400]}")

    finally:
        try:
            browser.close()
        except Exception:
            pass


if __name__ == "__main__":
    main()
