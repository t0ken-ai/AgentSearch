"""CLI tool for Cloak Stealth Suite."""

import argparse
import importlib
import inspect
import json
import logging
import pkgutil
import sys
from functools import lru_cache

from .core import launch, new_page, BrowserConfig

# Explicit short-alias map. Maps `--engine` value → `(module, class)`.
# Anything NOT in here is auto-discovered from cloak_stealth_suite/engines/.
_ALIASES: dict[str, tuple[str, str]] = {
    # Common short aliases.
    "ddg":         ("duckduckgo",        "DuckDuckGoEngine"),
    "x":           ("twitter",           "TwitterEngine"),
    "github":      ("github_search",     "GitHubSearchEngine"),
    "npm":         ("npm_search",        "NpmSearchEngine"),
    "archive":     ("archive_org",       "ArchiveOrgEngine"),
    "1337x":       ("torrent_1337x",     "Torrent1337xEngine"),
    "reddit_sub":  ("reddit_subreddit",  "RedditSubredditEngine"),
}


@lru_cache(maxsize=1)
def _engine_registry() -> dict[str, tuple[str, str]]:
    """Discover every BaseEngine subclass under cloak_stealth_suite.engines.

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
        full = f"cloak_stealth_suite.engines.{modname}"
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
        f"cloak_stealth_suite.engines.{module_basename}"
    )
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
