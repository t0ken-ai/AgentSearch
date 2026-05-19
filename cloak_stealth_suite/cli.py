"""CLI tool for Cloak Stealth Suite."""

import argparse
import json
import logging
import sys

from .core import launch, new_page, BrowserConfig

# Lazy imports to avoid import errors for engines not yet created
ENGINE_REGISTRY = {
    "google": ".engines.google:GoogleEngine",
    "bing": ".engines.bing:BingEngine",
    "duckduckgo": ".engines.duckduckgo:DuckDuckGoEngine",
    "ddg": ".engines.duckduckgo:DuckDuckGoEngine",
    "reddit": ".engines.reddit:RedditEngine",
    "twitter": ".engines.twitter:TwitterEngine",
    "x": ".engines.twitter:TwitterEngine",
    "stackoverflow": ".engines.stackoverflow:StackOverflowEngine",
    "hackernews": ".engines.hackernews:HackerNewsEngine",
    "github": ".engines.github_search:GitHubSearchEngine",
    "medium": ".engines.medium:MediumSearchEngine",
    "quora": ".engines.quora:QuoraSearchEngine",
    "producthunt": ".engines.producthunt:ProductHuntSearchEngine",
    "blackhatworld": ".engines.blackhatworld:BlackHatWorldEngine",
    "wikipedia": ".engines.wikipedia:WikipediaEngine",
    "wikivoyage": ".engines.wikivoyage:WikivoyageEngine",
    "yandex": ".engines.yandex:YandexEngine",
    # Round 2 engines (may not exist yet)
    "steam": ".engines.steam:SteamEngine",
    "1337x": ".engines.torrent_1337x:Torrent1337xEngine",
    "google_patents": ".engines.google_patents:GooglePatentsEngine",
    "linkedin_jobs": ".engines.linkedin_jobs:LinkedInJobsEngine",
    "indeed": ".engines.indeed:IndeedEngine",
    "virustotal": ".engines.virustotal:VirusTotalEngine",
    "icecat": ".engines.icecat:IcecatEngine",
    "amazon": ".engines.amazon:AmazonEngine",
    "brave": ".engines.brave:BraveEngine",
    "wolfram": ".engines.wolfram:WolframEngine",
    "archive": ".engines.archive_org:ArchiveOrgEngine",
    "devto": ".engines.devto:DevToEngine",
    "npm": ".engines.npm_search:NpmSearchEngine",
    # Round 3: camofox-browser 竞品对齐
    "youtube": ".engines.youtube:YouTubeEngine",
    "yelp": ".engines.yelp:YelpEngine",
    "spotify": ".engines.spotify:SpotifyEngine",
    "tiktok": ".engines.tiktok:TikTokEngine",
    "instagram": ".engines.instagram:InstagramEngine",
    "twitch": ".engines.twitch:TwitchEngine",
    "netflix": ".engines.netflix:NetflixEngine",
    "reddit_sub": ".engines.reddit_subreddit:RedditSubredditEngine",
    # Round 4: union-search-skill 竞品对齐
    "baidu": ".engines.baidu:BaiduEngine",
    "sogou": ".engines.sogou:SogouEngine",
    "so360": ".engines.so360:So360Engine",
    "startpage": ".engines.startpage:StartpageEngine",
    "ecosia": ".engines.ecosia:EcosiaEngine",
    "qwant": ".engines.qwant:QwantEngine",
    "bilibili": ".engines.bilibili:BilibiliEngine",
    "zhihu": ".engines.zhihu:ZhihuEngine",
    "xiaohongshu": ".engines.xiaohongshu:XiaohongshuEngine",
    "douyin": ".engines.douyin:DouyinEngine",
    "weibo": ".engines.weibo:WeiboEngine",
    "toutiao": ".engines.toutiao:ToutiaoEngine",
    "unsplash": ".engines.unsplash:UnsplashEngine",
    "pixabay": ".engines.pixabay:PixabayEngine",
    "pexels": ".engines.pexels:PexelsEngine",
    "xiaoyuzhou": ".engines.xiaoyuzhou:XiaoyuzhouEngine",
}


def _get_engine(name: str):
    """Lazily load an engine class."""
    spec = ENGINE_REGISTRY.get(name)
    if not spec:
        raise ValueError(f"Unknown engine: {name}. Available: {', '.join(sorted(set(ENGINE_REGISTRY.values())))}")
    module_path, class_name = spec.rsplit(":", 1)
    import importlib
    module = importlib.import_module(module_path, package="cloak_stealth_suite")
    return getattr(module, class_name)


def cmd_search(args):
    cfg = BrowserConfig(headless=not args.visible, proxy=args.proxy)
    browser = launch(cfg)
    try:
        page = new_page(browser)
        engine_cls = _get_engine(args.engine)
        engine = engine_cls(page)
        results = engine.search(args.query, limit=args.limit)
        if args.json:
            print(json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=2))
        else:
            for i, r in enumerate(results, 1):
                print(f"{i}. {r.title}")
                if r.url:
                    print(f"   {r.url}")
                if r.snippet:
                    print(f"   {r.snippet[:200]}")
                print()
        if not results:
            print("No results found.", file=sys.stderr)
            return 1
    finally:
        browser.close()
    return 0


def cmd_extract(args):
    """Extract page content as text."""
    cfg = BrowserConfig(headless=not args.visible)
    browser = launch(cfg)
    try:
        page = new_page(browser)
        page.goto(args.url, timeout=30000, wait_until="domcontentloaded")
        text = page.inner_text("body")
        print(text)
    finally:
        browser.close()
    return 0


def cmd_list_engines(args):
    """List all available engines."""
    available = []
    unavailable = []
    for name in sorted(set(ENGINE_REGISTRY.keys())):
        try:
            _get_engine(name)
            available.append(name)
        except (ImportError, AttributeError):
            unavailable.append(name)

    print("Available engines:")
    for name in available:
        print(f"  ✅ {name}")
    if unavailable:
        print("\nNot yet implemented:")
        for name in unavailable:
            print(f"  ⏳ {name}")


def cmd_test(args):
    """Run anti-detection tests."""
    from .stealth.enhance import check_blocked
    cfg = BrowserConfig(headless=not args.visible)
    browser = launch(cfg)
    page = new_page(browser)

    sites = args.sites or ["google", "bing", "duckduckgo"]
    results = {}

    for name in sites:
        try:
            engine_cls = _get_engine(name)
        except (ImportError, AttributeError, ValueError) as e:
            print(f"  ⏳ {name}: not available ({e})")
            continue

        engine = engine_cls(page)
        search_results = engine.search("test query", limit=3)
        blocked = check_blocked(page)
        results[name] = {
            "passed": len(search_results) > 0 and not blocked,
            "results_count": len(search_results),
            "blocked": blocked,
        }
        status = "✅" if results[name]["passed"] else "❌"
        print(f"  {status} {name}: {len(search_results)} results, blocked={blocked}")

    browser.close()
    if results:
        print(f"\nPassed: {sum(1 for r in results.values() if r['passed'])}/{len(results)}")
    return 0


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    parser = argparse.ArgumentParser(prog="cloak", description="Cloak Stealth Suite CLI")
    sub = parser.add_subparsers(dest="command")

    # search
    sp = sub.add_parser("search", help="Search with stealth browser")
    sp.add_argument("query", help="Search query")
    sp.add_argument("--engine", "-e", default="duckduckgo", help="Engine name")
    sp.add_argument("--limit", "-n", type=int, default=10)
    sp.add_argument("--json", action="store_true", help="Output as JSON")
    sp.add_argument("--visible", action="store_true", help="Run in headed mode")
    sp.add_argument("--proxy", default=None, help="Proxy URL")

    # extract
    ep = sub.add_parser("extract", help="Extract page content")
    ep.add_argument("url", help="URL to extract")
    ep.add_argument("--visible", action="store_true")

    # list-engines
    lp = sub.add_parser("list-engines", help="List available engines")

    # test
    tp = sub.add_parser("test", help="Run anti-detection tests")
    tp.add_argument("sites", nargs="*", help="Sites to test")
    tp.add_argument("--visible", action="store_true")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    if args.command == "search":
        sys.exit(cmd_search(args))
    elif args.command == "extract":
        sys.exit(cmd_extract(args))
    elif args.command == "list-engines":
        cmd_list_engines(args)
    elif args.command == "test":
        sys.exit(cmd_test(args))


if __name__ == "__main__":
    main()
