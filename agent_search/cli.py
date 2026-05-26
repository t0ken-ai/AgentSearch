"""CLI tool for AgentSearch."""

import argparse
import importlib
import inspect
import json
import logging
import os
import pkgutil
import sys
import time
from functools import lru_cache

from .core import launch, new_page, BrowserConfig

# Explicit short-alias map. Maps `--engine` value → `(module, class)`.
# Anything NOT in here is auto-discovered from agent_search/engines/.
_ALIASES: dict[str, tuple[str, str]] = {
    # Common short aliases.
    "ddg":         ("duckduckgo",        "DuckDuckGoEngine"),
    "x":           ("twitter",           "TwitterEngine"),
    "github":      ("github_search",     "GitHubSearchEngine"),
    "npm":         ("npm_search",        "NpmSearchEngine"),
    "archive":     ("archive_org",       "ArchiveOrgEngine"),
    "1337x":       ("torrent_1337x",     "Torrent1337xEngine"),
    "reddit_sub":  ("reddit_subreddit",  "RedditSubredditEngine"),
    # Ad intelligence.
    "tt_ads":      ("tiktok_creative_center", "TikTokCreativeCenterEngine"),
    "ttcc":        ("tiktok_creative_center", "TikTokCreativeCenterEngine"),
    "fb_ads":      ("meta_ad_library",   "MetaAdLibraryEngine"),
    "meta_ads":    ("meta_ad_library",   "MetaAdLibraryEngine"),
    "g_ads":       ("google_ad_transparency", "GoogleAdTransparencyEngine"),
    "tiktok_ads":  ("tiktok_ad_library", "TikTokAdLibraryEngine"),
}


@lru_cache(maxsize=1)
def _engine_registry() -> dict[str, tuple[str, str]]:
    """Discover every BaseEngine subclass under agent_search.engines.

    Returns a mapping from canonical short name (module basename) to a
    ``(module_path, class_name)`` tuple. The discovery is cached after the
    first call so subsequent ``--engine`` lookups are O(1).
    """
    from .engines.base import BaseEngine
    from . import engines as engines_pkg

    registry: dict[str, tuple[str, str]] = {}

    # 1) Auto-discover.
    for finder, modname, ispkg in pkgutil.iter_modules(engines_pkg.__path__):
        if modname.startswith("_") or modname == "base":
            continue
        full = f"agent_search.engines.{modname}"
        try:
            mod = importlib.import_module(full)
        except Exception as e:
            logging.warning("[cli] failed to import %s: %s", full, e)
            continue
        for cls_name, cls in inspect.getmembers(mod, inspect.isclass):
            if cls is BaseEngine:
                continue
            if not issubclass(cls, BaseEngine):
                continue
            if cls.__module__ != full:
                continue  # Only register classes defined in their own module.
            # Use the engine's `name` attr if defined, else the module basename.
            handle = getattr(cls, "name", None) or modname
            registry.setdefault(handle, (modname, cls_name))
            registry.setdefault(modname, (modname, cls_name))

    # 2) Apply explicit aliases on top so they always win.
    for alias, target in _ALIASES.items():
        registry[alias] = target

    return registry


def _get_engine(name: str):
    reg = _engine_registry()
    spec = reg.get(name)
    if spec is None:
        # Try a relaxed match — strip non-alphanumeric, lowercase.
        norm = "".join(ch for ch in name.lower() if ch.isalnum())
        for k, v in reg.items():
            if "".join(ch for ch in k.lower() if ch.isalnum()) == norm:
                spec = v
                break
    if spec is None:
        available = ", ".join(sorted(reg.keys()))
        raise ValueError(f"Unknown engine: {name!r}. Available: {available}")
    module_basename, class_name = spec
    module = importlib.import_module(
        f"agent_search.engines.{module_basename}"
    )
    return getattr(module, class_name)


def _resolve_profile_dir(profile: str | None) -> str | None:
    """Translate a --profile <name> CLI value into a concrete user_data_dir.

    Returns None when no profile was requested, so callers fall back to
    anonymous BrowserConfig. Logs a hint when the profile dir doesn't
    exist yet (likely user forgot to run `agentsearch login` first).
    """
    if not profile:
        return None
    from .core import profile_path

    try:
        path = profile_path(profile)
    except ValueError as e:
        logging.warning("[cli] %s", e)
        return None
    if not path.exists():
        logging.warning(
            "[cli] profile %r does not exist yet (%s) — running anonymously. "
            "Run `agentsearch login %s` first to create it.",
            profile,
            path,
            profile,
        )
        return None
    return str(path)


def cmd_login(args):
    """Open a headed CloakBrowser, let the user log into a site, persist cookies.

    The browser stays open until the user closes the window OR presses Enter
    in this terminal. Cookies / localStorage / IndexedDB are persisted to
    ``~/.cache/agentsearch/profiles/<name>/`` and automatically picked up by
    later ``search`` / ``extract`` / ``search-many`` calls that pass
    ``--profile <name>``.
    """
    from .core import profile_path

    # Default URL map for known login-walled sites. Add more here as we
    # support more login flows; users can always pass --url to override.
    DEFAULT_LOGIN_URLS = {
        "twitter":     "https://x.com/login",
        "x":           "https://x.com/login",
        "linkedin":    "https://www.linkedin.com/login",
        "instagram":   "https://www.instagram.com/accounts/login/",
        "facebook":    "https://www.facebook.com/login/",
        "reddit":      "https://www.reddit.com/login",
        "glassdoor":   "https://www.glassdoor.com/profile/login_input.htm",
        "discord":     "https://discord.com/login",
        "github":      "https://github.com/login",
        "medium":      "https://medium.com/m/signin",
        "quora":       "https://www.quora.com/",
        "weibo":       "https://passport.weibo.com/sso/signin",
        "zhihu":       "https://www.zhihu.com/signin",
        "bilibili":    "https://passport.bilibili.com/login",
        "xiaohongshu": "https://www.xiaohongshu.com/explore",
        "douyin":      "https://www.douyin.com/",
    }

    profile_name = args.profile or args.site
    try:
        prof_dir = profile_path(profile_name)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    fresh = not prof_dir.exists()

    url = args.url or DEFAULT_LOGIN_URLS.get(args.site.lower())
    if not url:
        print(
            f"Error: no default login URL for site {args.site!r}. "
            f"Pass --url <login_url>, e.g. --url https://example.com/login",
            file=sys.stderr,
        )
        return 2

    print(f"📂 Profile: {prof_dir}{' (new)' if fresh else ' (existing — re-using)'}")
    print(f"🌐 Opening: {url}")
    print(f"🪟 Window will stay open. Log in, then come back here and press Enter to save and close.")

    cfg = BrowserConfig(headless=False, user_data_dir=str(prof_dir))
    browser = launch(cfg)
    try:
        page = new_page(browser)
        try:
            page.goto(url, timeout=60000, wait_until="domcontentloaded")
        except Exception as e:
            log.warning("[login] navigation warning: %s", e)
        try:
            input(f"\nPress Enter when you've finished logging into {args.site}... ")
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled — profile may be empty.", file=sys.stderr)
            return 1
    finally:
        try:
            browser.close()
        except Exception:
            pass

    print(f"\n✅ Profile saved to {prof_dir}")
    print(f"   Use it next time:  agentsearch search ... --profile {profile_name}")
    return 0


def cmd_search(args):
    # When --fallback is set, run through the health-aware fallback chain:
    # try the requested engine first, then bubble down to other healthy
    # general-search engines if it returns nothing / errors.
    if getattr(args, "fallback", False):
        from .health import search_with_fallback, DEFAULT_CHAIN

        chain = (
            [e.strip() for e in args.fallback_chain.split(",") if e.strip()]
            if getattr(args, "fallback_chain", None)
            else list(DEFAULT_CHAIN)
        )
        out = search_with_fallback(
            args.query,
            primary=args.engine,
            limit=args.limit,
            chain=chain,
            headless=not args.visible,
        )
        if args.json:
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return 0 if out.get("results") else 1
        if not out.get("results"):
            attempted = ", ".join(a["engine"] for a in out["attempts"])
            print(f"No results found (tried: {attempted}).", file=sys.stderr)
            return 1
        print(
            f"engine={out['engine']} (fallback={out['fallback']}) attempts={len(out['attempts'])}\n",
            file=sys.stderr,
        )
        for i, r in enumerate(out["results"], 1):
            print(f"{i}. {r.get('title', '')}")
            if r.get("url"):
                print(f"   {r['url']}")
            if r.get("snippet"):
                print(f"   {r['snippet'][:200]}")
            print()
        return 0

    # Standard single-engine path (also records to the health log so the
    # fallback path has signal to work with on later calls).
    from .health import HealthLog

    health = HealthLog()
    cfg = BrowserConfig(
        headless=not args.visible,
        user_data_dir=_resolve_profile_dir(getattr(args, "profile", None)),
    )
    if getattr(args, "proxy", None):
        from .proxy import apply_proxy_spec_to_config
        apply_proxy_spec_to_config(cfg, args.proxy)
    browser = launch(cfg)
    started = time.time()
    results = []
    ok = False
    try:
        page = new_page(browser)
        engine_cls = _get_engine(args.engine)
        engine = engine_cls(page)
        results = engine.search(args.query, limit=args.limit)
        ok = bool(results)

        # Optional deep-fetch: pull the markdown body for the top N URLs
        # so the agent doesn't need a follow-up `extract` round-trip.
        depth = getattr(args, "depth", 0) or 0
        if results and depth > 0:
            from .extract import extract_page

            top = [r for r in results[:depth] if r.url]
            for r in top:
                ep = None
                try:
                    ep = new_page(browser)
                    rec = extract_page(
                        ep,
                        url=r.url,
                        paginate=True,
                        max_scrolls=2,
                        include_links=False,
                        include_images=False,
                    )
                    # Stamp the body fields onto the SearchResult's __dict__
                    # so the JSON path picks them up.
                    r.__dict__["body_markdown"] = rec.get("content_markdown") or ""
                    r.__dict__["body_text"] = rec.get("content_text") or ""
                    r.__dict__["body_word_count"] = rec.get("word_count") or 0
                    if rec.get("date") and not getattr(r, "date", None):
                        r.__dict__["date"] = rec["date"]
                    if rec.get("author") and not getattr(r, "author", None):
                        r.__dict__["author"] = rec["author"]
                except Exception as e:
                    logging.warning("[search] deep-fetch failed for %s: %s", r.url, e)
                    r.__dict__["body_error"] = f"{type(e).__name__}: {e}"
                finally:
                    if ep is not None:
                        try:
                            ep.close()
                        except Exception:
                            pass

        if args.json:
            print(json.dumps([r.__dict__ for r in results], ensure_ascii=False, indent=2))
        else:
            for i, r in enumerate(results, 1):
                print(f"{i}. {r.title}")
                if r.url:
                    print(f"   {r.url}")
                if r.snippet:
                    print(f"   {r.snippet[:200]}")
                wc = r.__dict__.get("body_word_count")
                if wc:
                    print(f"   📰 body: {wc} words")
                print()
        if not results:
            print("No results found.", file=sys.stderr)
            return 1
    finally:
        try:
            health.record(
                args.engine,
                ok=ok,
                count=len(results) if results else 0,
                ms=int((time.time() - started) * 1000),
            )
        except Exception:
            pass
        browser.close()
    return 0


def cmd_extract(args):
    """Extract main content from a URL with readability + auto-pagination."""
    from .extract import extract_page

    cfg = BrowserConfig(
        headless=not args.visible,
        user_data_dir=_resolve_profile_dir(getattr(args, "profile", None)),
    )
    if getattr(args, "proxy", None):
        from .proxy import apply_proxy_spec_to_config
        apply_proxy_spec_to_config(cfg, args.proxy)
    browser = launch(cfg)
    try:
        page = new_page(browser)
        result = extract_page(
            page,
            url=args.url,
            paginate=not args.no_paginate,
            max_scrolls=args.max_scrolls,
            max_load_more_clicks=args.max_load_more,
            include_links=not args.no_links,
            include_images=not args.no_images,
        )
    finally:
        browser.close()

    if args.json:
        # Drop heavy fields when the user asked for a leaner payload.
        if args.no_links:
            result.pop("links", None)
        if args.no_images:
            result.pop("images", None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("status") == "ok" else 1

    # Human-readable output.
    fmt = args.format
    if fmt == "markdown":
        body = result.get("content_markdown") or result.get("content_text") or ""
    else:
        body = result.get("content_text") or result.get("content_markdown") or ""

    if result.get("title"):
        print(f"# {result['title']}")
    meta_bits = []
    if result.get("author"):
        meta_bits.append(f"by {result['author']}")
    if result.get("date"):
        meta_bits.append(result["date"])
    if result.get("word_count"):
        meta_bits.append(f"{result['word_count']} words")
    if meta_bits:
        print(" · ".join(meta_bits))
    if result.get("url"):
        print(result["url"])
    print()
    print(body)
    return 0 if result.get("status") == "ok" else 1


def cmd_list_engines(args):
    """List all available engines."""
    reg = _engine_registry()
    print(f"Available engines ({len(reg)} aliases / {len(set(reg.values()))} unique):")
    for name in sorted(reg.keys()):
        try:
            _get_engine(name)
            print(f"  ✅ {name}")
        except (ImportError, AttributeError) as e:
            print(f"  ⏳ {name}  ({e})")


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


def cmd_search_many(args):
    """Run multiple engines in parallel and merge their results."""
    from .multi import search_many

    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    if not engines:
        print("No engines provided. Use --engines google,reddit,arxiv (comma-separated).", file=sys.stderr)
        return 2

    out = search_many(
        args.query,
        engines,
        limit=args.limit,
        headless=not args.visible,
        timeout_s=args.timeout,
    )

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out["successful"] > 0 else 1

    # Human-readable summary.
    print(
        f"query={args.query!r} engines={out['engines']} "
        f"successful={out['successful']}/{len(out['engines'])} "
        f"elapsed={out['elapsed_s']}s"
    )
    print()
    if args.merged:
        # Show only the merged list.
        for i, r in enumerate(out["merged"][: args.limit * 2], 1):
            tag = ",".join(r.get("engines") or [])
            print(f"{i}. [{tag}] {r.get('title', '')}")
            if r.get("url"):
                print(f"   {r['url']}")
            if r.get("snippet"):
                print(f"   {r['snippet'][:200]}")
            print()
    else:
        for engine_name, payload in out["per_engine"].items():
            status = "✅" if payload.get("ok") and payload.get("count") else "❌"
            err = f" — {payload.get('error', '')}" if not payload.get("ok") else ""
            print(f"{status} {engine_name}  ({payload.get('count', 0)} hits, {payload.get('elapsed_s', '?')}s){err}")
            for i, r in enumerate(payload.get("results", []), 1):
                print(f"   {i}. {r.get('title', '')}")
                if r.get("url"):
                    print(f"      {r['url']}")
            print()
    return 0 if out["successful"] > 0 else 1


# Preset engine bundles for common multi-site agent tasks. Each bundle is a
# list of engine handles; `agentsearch <bundle> <query>` fans out across the
# bundle and merges by URL with consensus signal.
_BUNDLES: dict[str, list[str]] = {
    # JobSpy-style jobs aggregator: fan out across the four major boards.
    "jobs": ["linkedin_jobs", "indeed", "ziprecruiter", "glassdoor"],
    # Generic research bundle: web + opinion + news + papers.
    "research": ["duckduckgo", "google", "reddit", "hackernews"],
    # News from credible Western outlets only.
    "news": ["reuters", "apnews", "bbc", "guardian", "npr"],
    # Code / dev research.
    "code": ["github_search", "stackoverflow", "hackernews"],
    # Travel / hotel search across major aggregators (DataDome heavy —
    # CloakBrowser is one of few stacks that bypasses both reliably).
    # Skyscanner left out for now: free-text queries don't map cleanly
    # to its structured origin/destination/dates input.
    "travel": ["booking", "expedia"],
    # Ad intelligence — competitive creative research across the three
    # major public ad libraries. Returns image / video URLs + first/last
    # seen so a marketing agent can build evergreen swipe files.
    "ads": ["meta_ad_library", "google_ad_transparency", "tiktok_creative_center"],
}


def cmd_bundle(args):
    """Run a preset multi-engine bundle (jobs, research, news, code, ...)."""
    from .multi import search_many

    bundle_name = args.command  # 'jobs' / 'research' / 'news' / 'code'
    engines = _BUNDLES[bundle_name]
    out = search_many(
        args.query,
        engines,
        limit=args.limit,
        headless=not args.visible,
        timeout_s=args.timeout,
    )
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0 if out["successful"] > 0 else 1
    print(
        f"[{bundle_name}] query={args.query!r} "
        f"successful={out['successful']}/{len(out['engines'])} "
        f"elapsed={out['elapsed_s']}s"
    )
    print()
    for i, r in enumerate(out["merged"][: args.limit * 2], 1):
        tag = ",".join(r.get("engines") or [])
        print(f"{i}. [{tag}] {r.get('title', '')}")
        if r.get("url"):
            print(f"   {r['url']}")
        if r.get("snippet"):
            print(f"   {r['snippet'][:200]}")
        print()
    return 0 if out["successful"] > 0 else 1


def cmd_status(args):
    """Show per-engine health from the local sliding-window log."""
    from .health import HealthLog, DEFAULT_HEALTH_PATH

    health = HealthLog()
    rows = health.all_stats()

    if args.json:
        print(json.dumps({
            "path": str(DEFAULT_HEALTH_PATH),
            "engines": rows,
        }, ensure_ascii=False, indent=2))
        return 0

    if not rows:
        print(f"No health data yet. Run a few searches first.")
        print(f"(Log path: {DEFAULT_HEALTH_PATH})")
        return 0

    # Sort by score descending so healthiest engines surface first.
    rows.sort(key=lambda s: health.score(s["engine"]), reverse=True)

    print(f"Health log: {DEFAULT_HEALTH_PATH}")
    print()
    print(f"{'engine':<22} {'score':>6} {'attempts':>8} {'success':>8} {'avg_hits':>8} {'avg_ms':>7}  last")
    print("-" * 80)
    for s in rows:
        score = health.score(s["engine"])
        sr = s["success_rate"]
        sr_str = f"{sr*100:>6.1f}%" if sr is not None else "    —  "
        avg_hits = f"{s['avg_results']:>6.2f}" if s["avg_results"] is not None else "    —"
        avg_ms = f"{s['avg_ms']:>5}" if s["avg_ms"] is not None else "    —"
        last_ok = "✅" if s["last_ok"] else "❌"
        last_ago = ""
        if s["last_attempt"]:
            ago = int(time.time()) - int(s["last_attempt"])
            if ago < 60:
                last_ago = f"{ago}s ago"
            elif ago < 3600:
                last_ago = f"{ago // 60}m ago"
            elif ago < 86400:
                last_ago = f"{ago // 3600}h ago"
            else:
                last_ago = f"{ago // 86400}d ago"
        print(f"{s['engine']:<22} {score:>6.2f} {s['attempts']:>8d} {sr_str:>8} {avg_hits:>8} {avg_ms:>7}ms  {last_ok} {last_ago}")
    return 0


def cmd_proxies(args):
    """Manage the local proxy pool cached at ~/.cache/agentsearch/proxies.json.

    Subactions: fetch / test / list / add / clear.
    """
    from .proxy import (
        DEFAULT_CACHE_FILE,
        GITHUB_SOURCES,
        SOURCE_BUNDLES,
        Proxy,
        ProxyPool,
    )

    action = getattr(args, "proxies_action", None)
    if action is None:
        # Default: same as `proxies list`.
        action = "list"

    cache_path = getattr(args, "cache", None) or str(DEFAULT_CACHE_FILE)

    if action == "fetch":
        sources = args.sources or "all"
        pool = ProxyPool.load_from_cache(cache_path)
        before = len(pool)
        for s in [x.strip() for x in sources.split(",") if x.strip()]:
            n = pool.fetch_from_github(s, limit=args.limit)
            print(f"  {s}: +{n}")
        path = pool.save(cache_path)
        print(f"\nTotal: {len(pool)} (was {before}). Saved to {path}.")
        print(f"Available sources: {', '.join(GITHUB_SOURCES)}")
        print(f"Available bundles: {', '.join(SOURCE_BUNDLES)}")
        return 0

    if action == "test":
        pool = ProxyPool.load_from_cache(cache_path)
        if len(pool) == 0:
            print(f"Pool empty. Run `agentsearch proxies fetch` first.", file=sys.stderr)
            return 1
        scheme_filter = args.scheme or None
        target = args.target or "https://api.ipify.org?format=text"
        max_test = args.max_test
        print(
            f"Testing {min(max_test or len(pool), len(pool))} proxies "
            f"(scheme={scheme_filter or 'any'}, target={target}, "
            f"workers={args.workers}, timeout={args.timeout}s) ..."
        )
        t0 = time.time()
        res = pool.test_all(
            max_workers=args.workers,
            target_url=target,
            timeout=args.timeout,
            scheme_filter=scheme_filter,
            max_test=max_test,
        )
        print(
            f"  done in {time.time()-t0:.1f}s — "
            f"ok={res['ok']} fail={res['fail']} skipped={res['skipped']}"
        )
        path = pool.save(cache_path)
        print(f"  saved to {path}")
        return 0

    if action == "list":
        pool = ProxyPool.load_from_cache(cache_path)
        stats = pool.stats()
        if args.json:
            print(json.dumps({
                "cache": cache_path,
                "stats": stats,
                "proxies": [p.to_json() for p in pool.all],
            }, ensure_ascii=False, indent=2))
            return 0
        print(f"Cache: {cache_path}")
        print(
            f"Total: {stats['total']}  healthy: {stats['healthy']}  "
            f"by_scheme: {stats['by_scheme']}"
        )
        if not pool.all:
            return 0
        # Sort by health score descending; show top N (default 30).
        n = args.limit or 30
        rows = sorted(
            pool.all,
            key=lambda p: (p.health_score(), -1 * (p.fail_count + p.success_count)),
            reverse=True,
        )[:n]
        print()
        print(f"{'proxy':<48} {'src':<22} {'ok':>4} {'fail':>5} {'ms':>6}  last")
        print("-" * 100)
        for p in rows:
            ms = f"{p.latency_ms:.0f}" if p.latency_ms is not None else "—"
            last = ""
            if p.last_ok_at:
                ago = int(time.time() - p.last_ok_at)
                if ago < 60:
                    last = f"{ago}s ago"
                elif ago < 3600:
                    last = f"{ago // 60}m ago"
                else:
                    last = f"{ago // 3600}h ago"
            elif p.last_err:
                last = "err: " + p.last_err[:30]
            url = p.server  # show without auth in the listing
            if p.username:
                url = f"{p.scheme}://<auth>@{p.host}:{p.port}"
            print(f"{url:<48} {p.source[:22]:<22} {p.success_count:>4} {p.fail_count:>5} {ms:>6}  {last}")
        return 0

    if action == "add":
        if not args.url:
            print("Provide one or more proxy URLs / 'host:port' lines.", file=sys.stderr)
            return 2
        pool = ProxyPool.load_from_cache(cache_path)
        added = 0
        for raw in args.url:
            p = Proxy.from_url(raw, source="user")
            if p is None:
                print(f"  rejected: {raw!r}", file=sys.stderr)
                continue
            before = len(pool)
            pool.add(p)
            if len(pool) > before:
                added += 1
                print(f"  added: {p.server}")
            else:
                print(f"  duplicate: {p.server}")
        if added:
            path = pool.save(cache_path)
            print(f"\nSaved to {path}.")
        return 0

    if action == "clear":
        # Drop the on-disk cache (after a confirmation if --yes wasn't given).
        if not args.yes:
            print(f"Will delete {cache_path}. Re-run with --yes to confirm.", file=sys.stderr)
            return 1
        try:
            os.unlink(cache_path)
            print(f"Removed {cache_path}.")
        except FileNotFoundError:
            print(f"No cache to remove ({cache_path}).")
        return 0

    print(f"Unknown action: {action}", file=sys.stderr)
    return 2


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    # Special-case `canary`: it has its own argparse parser inside
    # `agent_search.canary` with a long flag list (--engines, --gh-issue,
    # --report, --issue-md, --fail-threshold). Nested REMAINDER handling
    # in argparse is fragile, so we just hand off the argv tail directly.
    if len(sys.argv) >= 2 and sys.argv[1] == "canary":
        from .canary import main as canary_main
        sys.argv = [sys.argv[0]] + sys.argv[2:]
        sys.exit(canary_main())

    parser = argparse.ArgumentParser(prog="agentsearch", description="AgentSearch — local stealth-browser web search across 71+ sites for AI agents")
    sub = parser.add_subparsers(dest="command")

    # search
    sp = sub.add_parser("search", help="Search with stealth browser")
    sp.add_argument("query", help="Search query")
    sp.add_argument("--engine", "-e", default="duckduckgo", help="Engine name")
    sp.add_argument("--limit", "-n", type=int, default=10)
    sp.add_argument("--json", action="store_true", help="Output as JSON")
    sp.add_argument("--visible", action="store_true", help="Run in headed mode")
    sp.add_argument(
        "--proxy",
        default=None,
        help="Proxy spec. Forms: 'http://1.2.3.4:8080', 'socks5://u:p@host:1080', "
             "'pool' (rotate from ~/.cache/agentsearch/proxies.json), "
             "'pool:socks5' (filter by scheme), 'pool:/path/to/cache.json', "
             "or 'file:/path/to/list.txt'. See `agentsearch proxies --help`.",
    )
    sp.add_argument(
        "--fallback",
        action="store_true",
        help="If the chosen engine fails, walk down a health-ranked fallback chain",
    )
    sp.add_argument(
        "--fallback-chain",
        default=None,
        help="Comma-separated override of the default fallback chain "
             "(default: duckduckgo,google,bing,brave,startpage,qwant,ecosia)",
    )
    sp.add_argument(
        "--depth",
        "-d",
        type=int,
        default=0,
        help="Deep-fetch the top N results (extract markdown body inline). "
             "0 = SERP only (default).",
    )
    sp.add_argument(
        "--profile",
        default=None,
        help="Use a persistent CloakBrowser profile by name (login state). "
             "Run `agentsearch login <site>` first to populate it.",
    )

    # extract
    ep = sub.add_parser("extract", help="Extract page content (readability + markdown)")
    ep.add_argument("url", help="URL to extract")
    ep.add_argument("--json", action="store_true", help="Output as JSON")
    ep.add_argument(
        "--format",
        "-f",
        choices=["markdown", "text"],
        default="markdown",
        help="Output format for non-JSON mode (default: markdown)",
    )
    ep.add_argument(
        "--no-paginate",
        action="store_true",
        help="Don't auto-scroll / click 'Load more' buttons",
    )
    ep.add_argument(
        "--max-scrolls",
        type=int,
        default=3,
        help="Max auto-scrolls for lazy content (default: 3)",
    )
    ep.add_argument(
        "--max-load-more",
        type=int,
        default=3,
        help="Max 'Load more' button clicks (default: 3)",
    )
    ep.add_argument("--no-links", action="store_true", help="Skip <a> link collection")
    ep.add_argument("--no-images", action="store_true", help="Skip <img> collection")
    ep.add_argument("--visible", action="store_true")
    ep.add_argument(
        "--profile",
        default=None,
        help="Use a persistent CloakBrowser profile by name (login state).",
    )
    ep.add_argument(
        "--proxy",
        default=None,
        help="Proxy spec (see `agentsearch search --help`).",
    )

    # list-engines
    lp = sub.add_parser("list-engines", help="List available engines")

    # search-many
    smp = sub.add_parser(
        "search-many",
        help="Run multiple engines in parallel and merge results",
    )
    smp.add_argument("query", help="Search query")
    smp.add_argument(
        "--engines",
        "-e",
        default="duckduckgo,google,reddit",
        help="Comma-separated engine list (default: duckduckgo,google,reddit)",
    )
    smp.add_argument("--limit", "-n", type=int, default=5, help="Limit per engine")
    smp.add_argument("--timeout", type=int, default=90, help="Total wall-clock timeout (s)")
    smp.add_argument(
        "--merged",
        action="store_true",
        help="Show only the URL-deduped merged list (text mode)",
    )
    smp.add_argument("--json", action="store_true", help="Output as JSON")
    smp.add_argument("--visible", action="store_true")

    # test
    tp = sub.add_parser("test", help="Run anti-detection tests")
    tp.add_argument("sites", nargs="*", help="Sites to test")
    tp.add_argument("--visible", action="store_true")

    # status
    stp = sub.add_parser("status", help="Show engine health stats from the local log")
    stp.add_argument("--json", action="store_true", help="Output as JSON")

    # login
    lgp = sub.add_parser(
        "login",
        help="Open a headed CloakBrowser to log into a site; cookies persist for later use",
    )
    lgp.add_argument(
        "site",
        help="Site name (e.g. twitter, linkedin, glassdoor, instagram, discord, github, ...)",
    )
    lgp.add_argument(
        "--profile",
        default=None,
        help="Profile name (defaults to the site name)",
    )
    lgp.add_argument(
        "--url",
        default=None,
        help="Override the login URL (default: a known login URL for common sites)",
    )

    # canary — runs locally on the user's residential IP. See docs/CANARY.md.
    # NOTE: Implemented as an early-dispatch before argparse runs (in main())
    # because the canary takes its own flag set. Listed here only so it shows
    # up in the global --help banner.
    sub.add_parser("canary", help="Health check across all engines (local; auto-files GitHub issues — see docs/CANARY.md)")

    # proxies — manage the local proxy pool used by --proxy pool[:scheme].
    pp = sub.add_parser(
        "proxies",
        help="Manage the local proxy pool (~/.cache/agentsearch/proxies.json)",
    )
    pp.add_argument(
        "--cache",
        default=None,
        help="Override the cache file path (default: ~/.cache/agentsearch/proxies.json)",
    )
    pp_sub = pp.add_subparsers(dest="proxies_action")

    pp_fetch = pp_sub.add_parser(
        "fetch",
        help="Pull proxies from GitHub free-lists (proxifly / roosterkid / TheSpeedX / Zaeem20)",
    )
    pp_fetch.add_argument(
        "--sources",
        default="all",
        help="Comma-separated source / bundle names. "
             "Bundles: all / http / socks / socks4 / socks5. "
             "Individual: proxifly_http, proxifly_socks5, roosterkid_https, "
             "speedx_socks5, zaeem_http, etc. Default: all.",
    )
    pp_fetch.add_argument(
        "--limit", type=int, default=None,
        help="Cap each source at this many lines (default: no cap)",
    )

    pp_test = pp_sub.add_parser(
        "test",
        help="Test cached HTTP/HTTPS proxies against a live target and update health scores. "
             "(SOCKS proxies are skipped here — they're verified inside the browser at use time.)",
    )
    pp_test.add_argument("--workers", type=int, default=30, help="Concurrent connections (default: 30)")
    pp_test.add_argument("--timeout", type=float, default=8.0, help="Per-proxy timeout seconds (default: 8)")
    pp_test.add_argument(
        "--scheme",
        choices=["http", "https", "socks4", "socks5"],
        default=None,
        help="Only test proxies of this scheme",
    )
    pp_test.add_argument(
        "--target",
        default=None,
        help="URL to fetch through each proxy (default: https://api.ipify.org?format=text)",
    )
    pp_test.add_argument(
        "--max-test", type=int, default=None,
        help="Cap the number of proxies tested (handy when the pool is huge)",
    )

    pp_list = pp_sub.add_parser("list", help="Show cached proxies sorted by health score")
    pp_list.add_argument("--limit", type=int, default=30, help="Max rows (default: 30)")
    pp_list.add_argument("--json", action="store_true", help="Output as JSON")

    pp_add = pp_sub.add_parser("add", help="Add a proxy (or several) to the pool")
    pp_add.add_argument("url", nargs="+",
                        help="Proxy URL(s): 'http://user:pass@1.2.3.4:8080' or 'host:port'")

    pp_clear = pp_sub.add_parser("clear", help="Delete the proxy cache")
    pp_clear.add_argument("--yes", action="store_true", help="Confirm")

    # bundle subcommands (jobs / research / news / code)
    for bundle_name, engines in _BUNDLES.items():
        bp = sub.add_parser(
            bundle_name,
            help=f"Multi-engine bundle: fan out across {', '.join(engines)} and merge",
        )
        bp.add_argument("query", help="Search query")
        bp.add_argument("--limit", "-n", type=int, default=5, help="Limit per engine")
        bp.add_argument("--timeout", type=int, default=120, help="Total wall-clock timeout (s)")
        bp.add_argument("--json", action="store_true", help="Output as JSON")
        bp.add_argument("--visible", action="store_true")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    if args.command == "search":
        sys.exit(cmd_search(args))
    elif args.command == "search-many":
        sys.exit(cmd_search_many(args))
    elif args.command == "extract":
        sys.exit(cmd_extract(args))
    elif args.command == "list-engines":
        cmd_list_engines(args)
    elif args.command == "status":
        sys.exit(cmd_status(args))
    elif args.command == "login":
        sys.exit(cmd_login(args))
    elif args.command in _BUNDLES:
        sys.exit(cmd_bundle(args))
    elif args.command == "test":
        sys.exit(cmd_test(args))
    elif args.command == "proxies":
        sys.exit(cmd_proxies(args))


if __name__ == "__main__":
    main()
