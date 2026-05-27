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
from typing import Optional

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
    "ig_ads":      ("instagram_ad_library", "InstagramAdLibraryEngine"),
    "instagram_ads": ("instagram_ad_library", "InstagramAdLibraryEngine"),
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


def cmd_ads_download(args):
    """Download every image / video URL from an ad-engine JSONL file.

    Each line of the input must be a JSON object shaped like a
    ``SearchResult.__dict__`` from one of the ad-library engines (i.e.
    what ``agentsearch search ... --json`` writes for the ``results``
    field, plus a few extra fields like ``ad_archive_id``,
    ``video_url``, etc.).

    Examples::

        # Pull and save in two steps
        agentsearch search shopify -e meta_ad_library --json > shopify.json
        # Re-shape the results array into JSONL, then download:
        jq -c '.results[]' shopify.json > shopify.jsonl
        agentsearch ads-download shopify.jsonl -o ./swipe

        # Or stream from stdin
        jq -c '.results[]' shopify.json | agentsearch ads-download - -o ./swipe
    """
    import json

    in_path = args.input
    if in_path == "-":
        records = [json.loads(line) for line in sys.stdin if line.strip()]
    else:
        # Two accepted shapes — JSONL (one record per line) and a single
        # JSON document. We try whole-document first because a single-
        # line JSON dump would otherwise be mis-parsed as one JSONL row.
        with open(in_path, "r", encoding="utf-8") as f:
            raw = f.read()
        records: list = []
        try:
            blob = json.loads(raw)
            if isinstance(blob, dict) and isinstance(blob.get("results"), list):
                records = blob["results"]
            elif isinstance(blob, list):
                records = blob
            elif isinstance(blob, dict):
                # Single-record JSON.
                records = [blob]
        except json.JSONDecodeError:
            # Fall back to JSONL.
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as e:
                    logging.warning(
                        "[ads-download] skipping malformed JSONL line: %s", e,
                    )

    if not records:
        print(f"No records loaded from {in_path}", file=sys.stderr)
        return 1

    from .engines._ad_media import AdMediaDownloader
    proxy_url = _resolve_proxy_url(args.proxy) if args.proxy else None

    dl = AdMediaDownloader(
        args.output,
        proxy_url=proxy_url,
        timeout=args.timeout,
        max_retries=args.max_retries,
    )
    print(f"Downloading {len(records)} records → {args.output}", file=sys.stderr)
    if args.workers > 1:
        results = dl.download_many(
            records,
            max_per_record=args.max_per_record,
            max_workers=args.workers,
        )
    else:
        results = []
        for rec in records:
            results.extend(dl.download_record(
                rec, max_per_record=args.max_per_record,
            ))

    ok = sum(1 for r in results if r.success)
    bytes_total = sum(r.file_size or 0 for r in results if r.success)
    if args.json:
        print(json.dumps([r.to_dict() for r in results], indent=2))
    elif not args.quiet:
        for r in results:
            tag = "OK " if r.success else "ERR"
            sz = f"{(r.file_size or 0) / 1024:.1f}K" if r.success else (r.error or "")
            print(f"{tag} {sz:>10}  {r.local_path or r.url}")
    print(
        f"\n  {ok}/{len(results)} files, {bytes_total / 1024 / 1024:.1f} MB total",
        file=sys.stderr,
    )
    return 0 if ok == len(results) else 2


def cmd_ads(args):
    """Cross-platform ad-creative search.

    Fans out across one or more ad-library engines, normalizes every
    output through :func:`_ad_base.to_ad_record`, and returns one
    uniform stream of records ranked by recency / engagement.

    Compared to ``agentsearch search -e meta_ad_library`` this command:
      * does multi-platform dispatch (``--platform all`` runs all four).
      * automatically picks the right transport for Google ATC under
        a proxy (raw HTTP) — the per-engine ``search`` command would
        run into the cloakbrowser+stealth+proxy block on ATC.
      * normalizes the output schema across platforms so downstream
        scripts don't have to know whether they're consuming Meta,
        TikTok, or Google.
    """
    import json
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from .core import BrowserConfig, launch, new_page
    from .engines._ad_base import to_ad_record

    proxy_url = _resolve_proxy_url(args.proxy) if args.proxy else None
    platforms = _expand_platforms(args.platform)

    plans = []  # (label, runner_fn)
    if "meta" in platforms:
        plans.append(("meta", lambda: _run_meta_like(
            "meta_ad_library", args, proxy_url)))
    if "instagram" in platforms:
        plans.append(("instagram", lambda: _run_meta_like(
            "instagram_ad_library", args, proxy_url)))
    if "tiktok" in platforms:
        plans.append(("tiktok", lambda: _run_tiktok(args, proxy_url)))
    if "google" in platforms:
        plans.append(("google", lambda: _run_google(args, proxy_url)))

    print(
        f"Running {len(plans)} ad engine(s): {[p[0] for p in plans]} "
        f"(proxy={'on' if proxy_url else 'off'})",
        file=sys.stderr,
    )

    # Concurrency strategy: launching multiple cloakbrowser instances in
    # parallel through the same proxy causes resource contention (we've
    # seen 12s solo runs balloon to 38s with 0 results when 3 browsers
    # share a Chromium pool + a residential proxy egress). So:
    #   * Google with a proxy goes through raw HTTP — no browser, fast,
    #     never contends — so it can run on its own thread.
    #   * Meta / Instagram / TikTok all need a browser; we serialize
    #     those by default (workers=1 effectively). Users with a
    #     beefier setup can override via --workers.
    google_raw_first = (
        proxy_url is not None
        and any(lbl == "google" for lbl, _ in plans)
        and args.workers > 1
    )

    results_by_platform: dict[str, dict] = {}

    if google_raw_first:
        # Kick off Google in the background while the browser engines run.
        google_plan = next((fn for lbl, fn in plans if lbl == "google"), None)
        other_plans = [(lbl, fn) for lbl, fn in plans if lbl != "google"]

        with ThreadPoolExecutor(max_workers=2) as ex:
            google_fut = ex.submit(google_plan) if google_plan else None
            for lbl, fn in other_plans:
                try:
                    results_by_platform[lbl] = fn()
                except Exception as e:
                    results_by_platform[lbl] = {
                        "ok": False,
                        "error": f"{type(e).__name__}: {e}",
                        "results": [],
                    }
            if google_fut is not None:
                try:
                    results_by_platform["google"] = google_fut.result()
                except Exception as e:
                    results_by_platform["google"] = {
                        "ok": False,
                        "error": f"{type(e).__name__}: {e}",
                        "results": [],
                    }
    elif args.workers > 1 and len(plans) > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            future_to_label = {ex.submit(fn): lbl for lbl, fn in plans}
            for fut in as_completed(future_to_label):
                lbl = future_to_label[fut]
                try:
                    results_by_platform[lbl] = fut.result()
                except Exception as e:
                    results_by_platform[lbl] = {
                        "ok": False,
                        "error": f"{type(e).__name__}: {e}",
                        "results": [],
                    }
    else:
        for lbl, fn in plans:
            try:
                results_by_platform[lbl] = fn()
            except Exception as e:
                results_by_platform[lbl] = {
                    "ok": False,
                    "error": f"{type(e).__name__}: {e}",
                    "results": [],
                }

    # Flatten + normalize.
    flat: list[dict] = []
    for lbl, payload in results_by_platform.items():
        for r in payload.get("results") or []:
            try:
                rec = to_ad_record(r)
                d = rec.to_dict()
                d["_platform_label"] = lbl
                flat.append(d)
            except Exception as e:
                logging.warning("[ads] normalize %s record failed: %s", lbl, e)

    # Apply post-collection filters (--filter key=val).
    if args.filter:
        try:
            preds = _parse_ad_filters(args.filter)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        before = len(flat)
        flat = [d for d in flat if all(p(d) for p in preds)]
        print(
            f"  --filter applied: {before} → {len(flat)} records "
            f"({len(args.filter)} predicate{'s' if len(args.filter) != 1 else ''})",
            file=sys.stderr,
        )

    # Sort by last_seen_iso desc, then days_running desc — recent +
    # long-running ads bubble up first, which tends to be what an
    # ad-creative researcher wants.
    def _sort_key(d):
        return (d.get("last_seen_iso") or "", d.get("days_running") or 0)
    flat.sort(key=_sort_key, reverse=True)

    if args.limit and len(flat) > args.limit:
        flat = flat[:args.limit]

    if args.json:
        out = {
            "query": args.query,
            "platforms": list(results_by_platform.keys()),
            "by_platform": {
                lbl: {
                    "ok": payload.get("ok", True),
                    "error": payload.get("error"),
                    "count": len(payload.get("results") or []),
                    "elapsed_s": payload.get("elapsed_s"),
                }
                for lbl, payload in results_by_platform.items()
            },
            "count": len(flat),
            "results": flat,
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
    else:
        for lbl, payload in results_by_platform.items():
            status = "OK" if payload.get("ok", True) else "FAIL"
            n = len(payload.get("results") or [])
            err = payload.get("error", "")
            elap = payload.get("elapsed_s", 0)
            line = f"  [{lbl:>9}] {status:4}  {n:3} results  {elap:5.1f}s"
            if err:
                line += f"  err={err[:80]}"
            print(line, file=sys.stderr)
        print(file=sys.stderr)
        for d in flat[:args.limit or len(flat)]:
            preview = (d.get("copy_text") or "")[:80].replace("\n", " ")
            print(
                f"  {d['platform']:<12} {d['ad_id']:<20} "
                f"{(d.get('advertiser_name') or '')[:25]:<25} "
                f"{d.get('first_seen_iso') or '':<10} → "
                f"{d.get('last_seen_iso') or '':<10}  "
                f"{preview}"
            )

    return 0 if any(p.get("ok", True) for p in results_by_platform.values()) else 1


_PLATFORM_ALIASES = {
    "meta":      ["meta"],
    "fb":        ["meta"],
    "facebook":  ["meta"],
    "ig":        ["instagram"],
    "instagram": ["instagram"],
    "tt":        ["tiktok"],
    "tiktok":    ["tiktok"],
    "google":    ["google"],
    "g":         ["google"],
    "all":       ["meta", "instagram", "tiktok", "google"],
}


def _expand_platforms(spec: str) -> list[str]:
    parts = [p.strip().lower() for p in (spec or "all").split(",") if p.strip()]
    out: list[str] = []
    for p in parts:
        for x in _PLATFORM_ALIASES.get(p, [p]):
            if x not in out:
                out.append(x)
    return out


def _ad_browser_cfg(proxy_url):
    from .core import BrowserConfig
    return BrowserConfig(headless=True, humanize=False, proxy=proxy_url)


def _run_meta_like(engine_name: str, args, proxy_url) -> dict:
    """Run meta_ad_library or instagram_ad_library."""
    import time as _t
    from .core import launch, new_page
    started = _t.time()
    browser = None
    try:
        engine_cls = _get_engine(engine_name)
        browser = launch(_ad_browser_cfg(proxy_url))
        page = new_page(browser)
        eng = engine_cls(page)
        results = eng.search(
            args.query, limit=args.limit or 10,
            country=args.country or "US",
            status="active",
        )
        return {
            "ok": True,
            "results": [r.__dict__ for r in (results or [])],
            "elapsed_s": round(_t.time() - started, 1),
        }
    except Exception as e:
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "results": [],
            "elapsed_s": round(_t.time() - started, 1),
        }
    finally:
        if browser is not None:
            try: browser.close()
            except Exception: pass


def _run_tiktok(args, proxy_url) -> dict:
    """Run tiktok_creative_center.top_ads (the only mode that works
    without TikTok-for-Business login)."""
    import time as _t
    from .core import launch, new_page
    started = _t.time()
    browser = None
    try:
        engine_cls = _get_engine("tiktok_creative_center")
        browser = launch(_ad_browser_cfg(proxy_url))
        page = new_page(browser)
        eng = engine_cls(page)
        results = eng.search(
            args.query or "", limit=args.limit or 10,
            mode="top_ads", period=7,
            country_code=args.country or "US",
        )
        return {
            "ok": True,
            "results": [r.__dict__ for r in (results or [])],
            "elapsed_s": round(_t.time() - started, 1),
        }
    except Exception as e:
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "results": [],
            "elapsed_s": round(_t.time() - started, 1),
        }
    finally:
        if browser is not None:
            try: browser.close()
            except Exception: pass


def _run_google(args, proxy_url) -> dict:
    """Run google_ad_transparency.

    When a proxy is in play, switch to the raw HTTP transport because
    cloakbrowser + residential proxy + Google's stealth checks combine
    to block navigation. Without a proxy, the browser path works.
    """
    import time as _t
    from .core import launch, new_page
    started = _t.time()
    browser = None
    try:
        from .engines.google_ad_transparency import GoogleAdTransparencyEngine

        if proxy_url:
            eng = GoogleAdTransparencyEngine.raw(
                proxy_url=proxy_url, timeout=20,
            )
            results = eng.search(
                args.query, limit=args.limit or 10,
                mode="search_advertisers",
                region=args.country or "anywhere",
            )
        else:
            browser = launch(_ad_browser_cfg(None))
            page = new_page(browser)
            eng = GoogleAdTransparencyEngine(page)
            results = eng.search(
                args.query, limit=args.limit or 10,
                mode="search_advertisers",
                region=args.country or "anywhere",
            )
        return {
            "ok": True,
            "results": [r.__dict__ for r in (results or [])],
            "elapsed_s": round(_t.time() - started, 1),
        }
    except Exception as e:
        return {
            "ok": False,
            "error": f"{type(e).__name__}: {e}",
            "results": [],
            "elapsed_s": round(_t.time() - started, 1),
        }
    finally:
        if browser is not None:
            try: browser.close()
            except Exception: pass


def _parse_ad_filters(specs: list[str]):
    """Compile a list of ``key=val`` filter specs into AdRecord predicates.

    Supported keys (operate on the flattened :class:`AdRecord` dict):

      Numeric ranges:
        * ``min_impressions`` / ``max_impressions`` (vs ``impressions_lower``
          and ``impressions_upper`` respectively, conservative — pass when
          unknown)
        * ``min_spend`` / ``max_spend``
        * ``min_days_running`` / ``max_days_running``
        * ``min_score`` / ``max_score`` (engagement signal: CTR % or
          equivalent depending on engine)

      Bool flags:
        * ``is_active=true|false``
        * ``has_video=true|false``  (any media_url ends in mp4/webm/etc.)
        * ``has_image=true|false``  (any media_url ends in jpg/png/etc.)
        * ``has_landing=true|false`` (landing_url non-empty)

      Date strings (YYYY-MM-DD, vs first/last_seen_iso):
        * ``last_seen_after`` / ``last_seen_before``
        * ``first_seen_after`` / ``first_seen_before``

      Match:
        * ``country=US`` (case-insensitive equality)
        * ``platform=meta|instagram|tiktok_cc|tiktok_lib|google_atc``
        * ``advertiser_contains=Nike`` (case-insensitive substring on
          ``advertiser_name``)

    Multiple ``--filter`` flags AND together. Returns a list of
    callables ``(record_dict) → bool``.

    Raises ``ValueError`` on unknown keys / malformed specs so the
    caller can surface a clear error to the user.
    """
    image_exts = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")
    video_exts = (".mp4", ".webm", ".mov", ".m4v", ".mkv", ".avi")

    def _has_kind(d, exts):
        for u in d.get("media_urls") or []:
            if isinstance(u, str) and any(e in u.lower() for e in exts):
                return True
        return False

    def _to_bool(s):
        if isinstance(s, bool):
            return s
        return s.strip().lower() in ("1", "true", "yes", "y", "on")

    preds = []
    for spec in specs:
        if "=" not in spec:
            raise ValueError(
                f"--filter expects key=val, got {spec!r}. "
                f"Example: --filter has_video=true --filter min_impressions=10000"
            )
        key, val = spec.split("=", 1)
        key = key.strip().lower()
        val = val.strip()

        # Numeric ranges (impressions / spend use the upper/lower bounds).
        if key == "min_impressions":
            n = int(val)
            preds.append(lambda d, n=n: (
                d.get("impressions_upper") is None
                or d["impressions_upper"] >= n
            ))
        elif key == "max_impressions":
            n = int(val)
            preds.append(lambda d, n=n: (
                d.get("impressions_lower") is None
                or d["impressions_lower"] <= n
            ))
        elif key == "min_spend":
            n = int(val)
            preds.append(lambda d, n=n: (
                d.get("spend_upper") is None or d["spend_upper"] >= n
            ))
        elif key == "max_spend":
            n = int(val)
            preds.append(lambda d, n=n: (
                d.get("spend_lower") is None or d["spend_lower"] <= n
            ))
        elif key == "min_days_running":
            n = int(val)
            preds.append(lambda d, n=n: (d.get("days_running") or 0) >= n)
        elif key == "max_days_running":
            n = int(val)
            preds.append(lambda d, n=n: (d.get("days_running") or 0) <= n)
        elif key == "min_score":
            n = float(val)
            preds.append(lambda d, n=n: (
                d.get("score") is None or d["score"] >= n
            ))
        elif key == "max_score":
            n = float(val)
            preds.append(lambda d, n=n: (
                d.get("score") is None or d["score"] <= n
            ))
        # Bool flags
        elif key == "is_active":
            b = _to_bool(val)
            preds.append(lambda d, b=b: d.get("is_active") is b)
        elif key == "has_video":
            b = _to_bool(val)
            preds.append(lambda d, b=b: _has_kind(d, video_exts) is b)
        elif key == "has_image":
            b = _to_bool(val)
            preds.append(lambda d, b=b: _has_kind(d, image_exts) is b)
        elif key == "has_landing":
            b = _to_bool(val)
            preds.append(lambda d, b=b: bool(d.get("landing_url")) is b)
        # Dates (string ≤/≥ comparison works on YYYY-MM-DD)
        elif key == "last_seen_after":
            preds.append(lambda d, v=val: (d.get("last_seen_iso") or "") >= v)
        elif key == "last_seen_before":
            preds.append(lambda d, v=val: (d.get("last_seen_iso") or "9999-12-31") <= v)
        elif key == "first_seen_after":
            preds.append(lambda d, v=val: (d.get("first_seen_iso") or "") >= v)
        elif key == "first_seen_before":
            preds.append(lambda d, v=val: (d.get("first_seen_iso") or "9999-12-31") <= v)
        # Match
        elif key == "country":
            v = val.upper()
            preds.append(lambda d, v=v: (d.get("country") or "").upper() == v)
        elif key == "platform":
            v = val.lower()
            preds.append(lambda d, v=v: (d.get("platform") or "").lower() == v)
        elif key == "advertiser_contains":
            v = val.lower()
            preds.append(lambda d, v=v: (
                v in (d.get("advertiser_name") or "").lower()
            ))
        else:
            raise ValueError(
                f"unknown filter key {key!r}. Supported: "
                f"min/max_impressions, min/max_spend, min/max_days_running, "
                f"min/max_score, is_active, has_video, has_image, has_landing, "
                f"first/last_seen_after, first/last_seen_before, country, "
                f"platform, advertiser_contains"
            )
    return preds


def _resolve_proxy_url(spec: Optional[str]) -> Optional[str]:
    """Resolve a CLI proxy spec into a usable URL.

    Accepts:
      * Bare URLs (``http://...``, ``socks5://user:pass@host:port``)
      * ``env`` (or ``env:NAME``) — read from FLUXISP_PROXY (or NAME)
      * ``pool[:scheme]`` — pick from the rotation pool

    Returns ``None`` when nothing is set so the downloader runs direct.
    """
    if not spec:
        return None
    if spec.startswith(("http://", "https://", "socks4://", "socks5://")):
        return spec
    if spec == "env" or spec.startswith("env:"):
        var = spec.split(":", 1)[1] if ":" in spec else "FLUXISP_PROXY"
        return os.environ.get(var)
    if spec.startswith("pool"):
        from .proxy import ProxyPool
        cache = (
            spec.split(":", 1)[1] if ":" in spec else None
        ) or None
        pool = ProxyPool.load_from_cache(path=cache) if cache else ProxyPool.load_from_cache()
        p = pool.next()
        return p.url if p else None
    return spec  # last-resort: treat as a URL the user typed in


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
    # Ad intelligence — competitive creative research across the four
    # major public ad libraries. Returns image / video URLs + first/last
    # seen so a marketing agent can build evergreen swipe files.
    # NOTE: ``agentsearch ads`` (the dedicated subcommand) is more powerful
    # — it handles Google ATC's raw HTTP transport, normalizes results
    # into AdRecord schema, and surfaces per-platform stats. This bundle
    # is kept as a thin search-many wrapper for users who want the raw
    # SearchResult dicts.
    "ads-fanout": ["meta_ad_library", "instagram_ad_library",
                   "google_ad_transparency", "tiktok_creative_center"],
    # Social-only ad creatives (skip Google text/shopping which has
    # very different format expectations).
    "social_ads": ["meta_ad_library", "instagram_ad_library",
                   "tiktok_creative_center"],
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

    # ads — top-level cross-platform ad-creative search (fan-out + normalize)
    asp = sub.add_parser(
        "ads",
        help="Cross-platform ad-creative search across Meta/Instagram/TikTok/Google",
    )
    asp.add_argument("query", help="Keyword (e.g. brand name, product, theme)")
    asp.add_argument(
        "--platform", "-p",
        default="all",
        help="Comma-separated platforms: meta / ig / tt / google / all "
             "(default: all). Aliases: fb=meta, facebook=meta, instagram=ig, "
             "tiktok=tt, g=google.",
    )
    asp.add_argument(
        "--country", "-c",
        default="US",
        help="Country (ISO-3166 alpha-2 like US/GB/JP, or 'anywhere' for "
             "Google). Default: US.",
    )
    asp.add_argument(
        "--limit", "-n", type=int, default=10,
        help="Max results across all platforms after merge (default: 10)",
    )
    asp.add_argument(
        "--proxy",
        default=os.environ.get("FLUXISP_PROXY"),
        help="Proxy URL or 'env[:VAR]' or 'pool[:scheme]'. "
             "Defaults to $FLUXISP_PROXY when set.",
    )
    asp.add_argument(
        "--workers", type=int, default=1,
        help="Parallel platform workers (default: 1 — safer when "
             "multiple cloakbrowsers share one proxy egress; bump to 2-4 "
             "if you have a beefy local + clean IPs)",
    )
    asp.add_argument("--json", action="store_true",
                     help="Output as JSON")
    asp.add_argument(
        "--filter", "-f",
        action="append",
        default=[],
        help="Post-collection filter on AdRecord fields. Repeat for AND. "
             "Examples: -f has_video=true -f min_impressions=10000 "
             "-f country=US -f advertiser_contains=Nike -f "
             "last_seen_after=2026-04-01. See cli.py:_parse_ad_filters "
             "for the full key list.",
    )

    # ads-download — download every image / video URL from an ad-engine JSONL
    adp = sub.add_parser(
        "ads-download",
        help="Download media (images/videos) from an ad-engine JSONL/JSON file",
    )
    adp.add_argument(
        "input",
        help="JSONL file (one record per line), JSON dump with .results, "
             "or '-' to read from stdin",
    )
    adp.add_argument(
        "--output", "-o",
        default="./ad_media",
        help="Output directory (default: ./ad_media)",
    )
    adp.add_argument(
        "--proxy",
        default=os.environ.get("FLUXISP_PROXY"),
        help="Proxy URL or 'env[:VAR]' or 'pool[:scheme]'. "
             "Defaults to $FLUXISP_PROXY when set.",
    )
    adp.add_argument(
        "--max-per-record",
        type=int,
        default=None,
        help="Cap downloads per ad (e.g. 1 = pick only the highest-res). "
             "Default: download all URLs found.",
    )
    adp.add_argument(
        "--workers", type=int, default=4,
        help="Concurrent downloads (default: 4; use 1 to serialize)",
    )
    adp.add_argument(
        "--timeout", type=int, default=30,
        help="Per-download timeout in seconds (default: 30)",
    )
    adp.add_argument(
        "--max-retries", type=int, default=2,
        help="Retries per URL on transport errors (default: 2)",
    )
    adp.add_argument("--json", action="store_true",
                     help="Print results as a JSON array instead of one line per file")
    adp.add_argument("--quiet", "-q", action="store_true",
                     help="Suppress per-file lines; print only the summary")

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
    elif args.command == "ads":
        sys.exit(cmd_ads(args))
    elif args.command == "ads-download":
        sys.exit(cmd_ads_download(args))
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
