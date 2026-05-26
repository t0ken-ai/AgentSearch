# TikTok Creative Center · Enum Dictionaries

These JSON files are the canonical enum dictionaries for the TikTok Creative
Center backend API (industry IDs, country codes, periods, sort orders, metric
names, etc.).

**Source**: <https://github.com/lofe-w/tiktok-creative-center-scraper-public>
(snapshot taken 2026-05-27).

They are imported by `agent_search/engines/tiktok_creative_center.py` to
validate input parameters and to translate human-readable filter values into
the IDs that the backend expects.

`TODO.json` additionally contains a sample response from the
`_next/data/<BUILD_ID>/insight/creativeinsight/pc/en.json` SSG endpoint,
useful as a reference when building the Creative Insights crawler.

The original repo is MIT-licensed; we preserve attribution above.
