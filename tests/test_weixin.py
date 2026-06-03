"""WeChat official-account (微信公众号) search adapter test.

What it checks:
1. Run WeixinEngine.search("老刘投放笔记", author="老刘投放笔记", mode="article")
   with up to 3 attempts. Sogou article search (type=2) mixes many
   accounts, so the ``author`` filter auto-paginates and keeps only the
   target 公众号's articles.
2. After every attempt, print the page title / URL, engine.last_status
   (mode / pages / raw_seen / count / author / block_reason).
3. PASS if at least one article is returned AND every returned result
   belongs to the requested author.
4. Print the top results with title / account / publish date, and show
   that resolve_urls produced a real ``mp.weixin.qq.com`` link.

Browser is launched with locale=zh-CN and timezone=Asia/Shanghai so
Sogou serves its standard Chinese desktop layout.

Run:
    source ~/tools/cloakbrowser/venv/bin/activate
    cd /Users/gao/projects/AgentSearch
    python tests/test_weixin.py
"""

from __future__ import annotations

import logging
import os
import sys
import time
import traceback

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from agent_search import core
from agent_search.engines.weixin import WeixinEngine
from agent_search.stealth.enhance import check_blocked


QUERY = "老刘投放笔记"
AUTHOR = "老刘投放笔记"
LIMIT = 5
MAX_ATTEMPTS = 3


def _attempt(engine: WeixinEngine, attempt: int) -> list:
    print(f"\n--- attempt {attempt}/{MAX_ATTEMPTS} ---")
    # Resolve the first page of articles + real URLs (no body fetch to keep
    # the test fast). search() drives its own retry, so call once.
    results = engine.search(
        QUERY, limit=LIMIT, mode="article", author=AUTHOR,
        resolve_urls=True, fetch_content=False,
    )

    page = engine.page
    try:
        title = page.title()
    except Exception as e:
        title = f"<title err: {e}>"
    try:
        url = page.url
    except Exception as e:
        url = f"<url err: {e}>"

    print(f"  page title : {title!r}")
    print(f"  page url   : {url}")

    if engine.last_status:
        ls = engine.last_status
        print(f"  mode       : {ls.get('mode')}")
        print(f"  author     : {ls.get('author')}")
        print(f"  pages      : {ls.get('pages')}")
        print(f"  raw_seen   : {ls.get('raw_seen')}")
        print(f"  parsed     : {ls.get('count')}")
        if ls.get("block_reason"):
            print(f"  block_reason: {ls.get('block_reason')!r}")
        if ls.get("body_len") is not None:
            print(f"  body length: {ls.get('body_len')} chars")

    blocked_reason = check_blocked(page)
    if blocked_reason:
        print(f"  check_blocked: {blocked_reason}")

    print(f"  results    : {len(results)}")
    return results


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    print("=== WeChat (微信公众号) search adapter test ===")
    print(f"Query: {QUERY!r} | Limit: {LIMIT} | Max attempts: {MAX_ATTEMPTS}")

    cfg = core.BrowserConfig(
        headless=True,
        humanize=True,
        locale="zh-CN",
        timezone="Asia/Shanghai",
    )
    browser = core.launch(cfg)
    try:
        page = core.new_page(browser)
        engine = WeixinEngine(page)

        results: list = []
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                results = _attempt(engine, attempt)
            except Exception:
                print(f"  attempt {attempt} raised:")
                traceback.print_exc()
                results = []
            if results:
                break
            if attempt < MAX_ATTEMPTS:
                wait = 5 + attempt * 2
                print(f"  no results -- sleeping {wait}s before retry")
                time.sleep(wait)

        if not results:
            print("\n=== FAIL === no results after all attempts", file=sys.stderr)
            return 1

        assert len(results) > 0, "expected at least one WeChat article"
        # Every result must belong to the requested author.
        bad = [getattr(r, "account", "") for r in results
               if AUTHOR not in (getattr(r, "account", "") or "")]
        assert not bad, f"author filter leaked other accounts: {bad}"

        print(f"\nReturned {len(results)} results")
        print("\n--- Top results ---")
        for i, r in enumerate(results[:LIMIT], start=1):
            account = getattr(r, "account", "") or ""
            real_url = getattr(r, "real_url", "") or ""
            print(f"\n[{i}] {r.title}")
            if account:
                print(f"    Account : {account}")
            if r.published_date:
                print(f"    Date    : {r.published_date}")
            print(f"    URL     : {r.url}")
            if real_url:
                print(f"    Real URL: {real_url}")

        print("\n=== PASS ===")
        return 0
    except AssertionError as e:
        print(f"\n=== FAIL === assertion: {e}", file=sys.stderr)
        return 1
    except Exception:
        print("\n=== FAIL === unexpected exception:", file=sys.stderr)
        traceback.print_exc()
        return 1
    finally:
        try:
            browser.close()
        except Exception as e:
            print(f"warning: browser.close() raised: {e}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
