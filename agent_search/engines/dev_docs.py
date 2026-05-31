"""Generic developer-documentation search engine.

Same trick as :mod:`agent_search.engines.facebook_docs` (drive an
external web-search engine with ``site:<host>`` prepended), but the
host is user-configurable so callers can search any well-known
developer portal:

    Cloud / Infra:  cloud.google.com  docs.aws.amazon.com
                    learn.microsoft.com  docs.docker.com
                    kubernetes.io  hashicorp.com  docs.github.com
                    docs.gitlab.com

    APIs / SaaS:    stripe.com  twilio.com  api.slack.com
                    shopify.dev  vercel.com  supabase.com
                    mongodb.com  redis.io  postgresql.org

    AI / ML:        platform.openai.com  docs.anthropic.com
                    docs.claude.com  huggingface.co
                    cohere.com  pinecone.io  ai.google.dev

    Frontend:       developer.mozilla.org  react.dev  vuejs.org
                    angular.dev  svelte.dev  nextjs.org
                    nodejs.org  python.org

    Mobile:         developer.android.com  developer.apple.com
                    flutter.dev  reactnative.dev

    Browsers:       webkit.org  developer.chrome.com

The full list (40+ presets) is in :data:`_PRESETS`. Pass
``platform="<key>"`` to use one, or ``site="<host>"`` for an arbitrary
domain.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from .base import BaseEngine, SearchResult
from .duckduckgo import DuckDuckGoEngine

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Preset platforms — short alias → (primary host, list of additional hosts)
# ---------------------------------------------------------------------------
#
# Many ecosystems split docs across multiple hosts (e.g. AWS keeps
# CLI / SDK / service docs on different subdomains). When a preset has
# secondary hosts, the engine OR-combines them so a single query
# spans the whole ecosystem.

_PRESETS: dict[str, list[str]] = {
    # ── Cloud / Infra ──
    "google-cloud":    ["cloud.google.com"],
    "gcp":             ["cloud.google.com"],
    "aws":             ["docs.aws.amazon.com"],
    "azure":           ["learn.microsoft.com", "docs.microsoft.com"],
    "microsoft":       ["learn.microsoft.com", "docs.microsoft.com"],
    "docker":          ["docs.docker.com"],
    "kubernetes":      ["kubernetes.io"],
    "k8s":             ["kubernetes.io"],
    "hashicorp":       ["developer.hashicorp.com"],
    "terraform":       ["developer.hashicorp.com"],
    "github":          ["docs.github.com"],
    "gitlab":          ["docs.gitlab.com"],
    "cloudflare":      ["developers.cloudflare.com"],
    "fly":             ["fly.io"],
    "render":          ["render.com"],
    "vercel":          ["vercel.com"],
    "netlify":         ["docs.netlify.com"],

    # ── APIs / SaaS ──
    "stripe":          ["stripe.com"],
    "twilio":          ["www.twilio.com"],
    "slack":           ["api.slack.com", "docs.slack.dev"],
    "discord":         ["discord.com"],
    "shopify":         ["shopify.dev"],
    "supabase":        ["supabase.com"],
    "firebase":        ["firebase.google.com"],
    "mongodb":         ["www.mongodb.com", "docs.mongodb.com"],
    "redis":           ["redis.io"],
    "postgres":        ["www.postgresql.org"],
    "postgresql":      ["www.postgresql.org"],
    "mysql":           ["dev.mysql.com"],
    "elasticsearch":   ["www.elastic.co"],

    # ── Social platforms — developer / business / marketing portals ──
    # Each social platform exposes docs across several subdomains. The
    # multi-host preset fans out to all of them. Entries with a path
    # suffix (e.g. "developers.facebook.com/docs/whatsapp") narrow to
    # one product within a megasite.
    "tiktok":              ["developers.tiktok.com",
                            "business-api.tiktok.com",
                            "ads.tiktok.com"],
    "tiktok-business":     ["business-api.tiktok.com",
                            "ads.tiktok.com"],
    "tiktok-marketing":    ["business-api.tiktok.com",
                            "ads.tiktok.com"],
    "tiktok-login":        ["developers.tiktok.com"],

    # Snap, X (Twitter) and others' developer portals — same pattern.
    "snap":                ["developers.snap.com"],
    "snapchat":            ["developers.snap.com",
                            "businesshelp.snapchat.com"],
    "snap-marketing":      ["marketingapi.snapchat.com",
                            "businesshelp.snapchat.com"],
    "twitter":             ["developer.x.com", "developer.twitter.com"],
    "x":                   ["developer.x.com", "developer.twitter.com"],
    "linkedin":            ["learn.microsoft.com/en-us/linkedin"],
    "pinterest":           ["developers.pinterest.com"],
    "reddit":              ["developers.reddit.com"],
    "youtube":             ["developers.google.com/youtube"],

    # WhatsApp Business Platform — docs live under
    # developers.facebook.com (the path under that host has been
    # rotated several times: /docs/whatsapp/ → /documentation/
    # business-messaging/whatsapp/). We narrow with the bare
    # "whatsapp" keyword in the URL — survives any path rotation.
    "whatsapp":            ["developers.facebook.com/whatsapp",
                            "business.whatsapp.com",
                            "faq.whatsapp.com"],
    "whatsapp-business":   ["developers.facebook.com/whatsapp",
                            "business.whatsapp.com"],
    "whatsapp-cloud":      ["developers.facebook.com/whatsapp"],

    # Telegram — Bot API + MTProto client API + TDLib.
    "telegram":            ["core.telegram.org"],
    "telegram-bot":        ["core.telegram.org/bots"],

    # Meta megasite — covers everything fb_docs handles, available
    # via the generic dev_docs interface. (fb_docs remains the more
    # ergonomic entry point with 16 product slugs.)
    "meta":                ["developers.facebook.com"],
    "facebook":            ["developers.facebook.com"],
    "instagram":           ["developers.facebook.com/instagram"],
    "messenger":           ["developers.facebook.com/messenger"],
    "threads":             ["developers.facebook.com/threads"],

    # Google product-narrowed — the bare developers.google.com host
    # bundles ~30 products, so each preset adds an inurl:<keyword>
    # narrow.
    "google-ads":          ["developers.google.com/google-ads"],
    "google-analytics":    ["developers.google.com/analytics"],
    "google-maps":         ["developers.google.com/maps"],
    "google-pay":          ["developers.google.com/pay"],

    # Other messaging / social platforms
    "line":                ["developers.line.biz"],
    "viber":               ["developers.viber.com"],
    "wechat":              ["developers.weixin.qq.com",
                            "wechatwiki.com"],
    "wechat-pay":          ["pay.weixin.qq.com"],
    "kakao":               ["developers.kakao.com"],

    # ── Mobile analytics / attribution / ad-intelligence ──
    # The four pillars of paid-acquisition tooling: market
    # intelligence (data.ai, Sensor Tower, 七麦 / 点点数据), MMP /
    # attribution (AppsFlyer, Adjust, Branch), ad-network /
    # mediation (AppLovin), and ad-creative spy (BigSpy).
    #
    # Many vendors split docs across "developer hub" and "help
    # center" subdomains — both are included so a single search
    # spans tutorials + API reference.
    "data.ai":             ["helpcenter.data.ai"],
    "data-ai":             ["helpcenter.data.ai"],
    "appannie":            ["helpcenter.data.ai"],
    "sensortower":         ["sensortower.com",
                            "app.sensortower.com"],
    "appsflyer":           ["dev.appsflyer.com",
                            "support.appsflyer.com"],
    "appsflyer-performance-index": [
                            "www.appsflyer.com/performance-index"],
    "appsflyer-pi":        ["www.appsflyer.com/performance-index"],
    "adjust":              ["dev.adjust.com", "help.adjust.com"],
    "branch":              ["help.branch.io"],
    "branch.io":           ["help.branch.io"],
    "applovin":            ["developers.applovin.com",
                            "dash.applovin.com/documentation"],
    "applovin-max":        ["developers.applovin.com/max",
                            "dash.applovin.com/documentation/mediation"],
    "bigspy":              ["bigspy.com",
                            "dev.bigspy.com",
                            "bigspy.crisp.help"],

    # Industry data / news / benchmarks:
    "businessofapps":      ["www.businessofapps.com"],
    "boa":                 ["www.businessofapps.com"],
    "appsflyer-benchmarks":["www.appsflyer.com/benchmarks"],
    "appsflyer-bench":     ["www.appsflyer.com/benchmarks"],

    # Industry research / market-intelligence portals (often cited in
    # quantitative industry write-ups):
    "statista":            ["www.statista.com"],
    "emarketer":           ["www.emarketer.com",
                            "www.insiderintelligence.com"],
    "insiderintelligence": ["www.insiderintelligence.com",
                            "www.emarketer.com"],

    # Ad-creative spy (西方 + 国内出海) :
    "socialpeta":          ["socialpeta.com"],
    "广大大":              ["socialpeta.com"],
    "guangdada":           ["socialpeta.com"],

    # Performance-marketing / ad-tech editorial sources frequently
    # used as secondary references for MMP and benchmark reports:
    "ppc.land":            ["ppc.land"],
    "ppcland":             ["ppc.land"],

    # Newsletter platform — many performance-marketing experts publish
    # on Substack rather than a personal blog. Site search covers all
    # *.substack.com subdomains.
    "substack":            ["substack.com"],

    # Web traffic intelligence (used to size up competitor websites):
    "similarweb":          ["docs.similarweb.com",
                            "developers.similarweb.com"],

    # Visitor-relationship management / adblock recovery:
    "admiral":             ["docs.getadmiral.com",
                            "learn.getadmiral.com"],
    "getadmiral":          ["docs.getadmiral.com",
                            "learn.getadmiral.com"],
    # Chinese ASO / app-store intelligence portals. 七麦 = qimai.cn,
    # 点点数据 also ASO-focused — these aliases all map to the
    # broadest host so a Chinese-language query lands on the right
    # platform regardless of which brand the user typed.
    "qimai":               ["www.qimai.cn"],
    "qimai.cn":            ["www.qimai.cn"],
    "七麦":                ["www.qimai.cn"],
    "qimai-international": ["www.qimai.com"],
    "diandian":            ["www.qimai.cn"],   # 点点数据 — same family
    "点点数据":            ["www.qimai.cn"],
    "dianshu":             ["www.qimai.cn"],

    # ── AI / ML ──
    "openai":          ["platform.openai.com"],
    "anthropic":       ["docs.anthropic.com", "docs.claude.com"],
    "claude":          ["docs.anthropic.com", "docs.claude.com"],
    "huggingface":     ["huggingface.co"],
    "hf":              ["huggingface.co"],
    "cohere":          ["docs.cohere.com"],
    "pinecone":        ["docs.pinecone.io"],
    "google-ai":       ["ai.google.dev"],
    "gemini":          ["ai.google.dev"],
    "langchain":       ["python.langchain.com", "js.langchain.com"],
    "llamaindex":      ["docs.llamaindex.ai"],

    # ── Frontend / Languages ──
    "mdn":             ["developer.mozilla.org"],
    "mozilla":         ["developer.mozilla.org"],
    "react":           ["react.dev"],
    "vue":             ["vuejs.org"],
    "angular":         ["angular.dev"],
    "svelte":          ["svelte.dev"],
    "nextjs":          ["nextjs.org"],
    "next":            ["nextjs.org"],
    "remix":           ["remix.run"],
    "nuxt":            ["nuxt.com"],
    "nodejs":          ["nodejs.org"],
    "node":            ["nodejs.org"],
    "deno":            ["docs.deno.com"],
    "bun":             ["bun.com"],
    "python":          ["docs.python.org"],
    "typescript":      ["www.typescriptlang.org"],
    "rust":            ["doc.rust-lang.org", "docs.rs"],
    "go":              ["go.dev"],
    "golang":          ["go.dev"],

    # ── Mobile ──
    "android":         ["developer.android.com"],
    "apple":           ["developer.apple.com"],
    "ios":             ["developer.apple.com"],
    "swift":           ["developer.apple.com", "swift.org"],
    "flutter":         ["docs.flutter.dev"],
    "react-native":    ["reactnative.dev"],
    "expo":            ["docs.expo.dev"],

    # ── Browsers ──
    "chrome":          ["developer.chrome.com"],
    "webkit":          ["webkit.org"],

    # ── DevOps / Observability ──
    "datadog":         ["docs.datadoghq.com"],
    "grafana":         ["grafana.com"],
    "prometheus":      ["prometheus.io"],
    "sentry":          ["docs.sentry.io"],
    "opentelemetry":   ["opentelemetry.io"],

    # ── Payments / Identity ──
    "auth0":           ["auth0.com"],
    "okta":            ["developer.okta.com"],
    "clerk":           ["clerk.com"],

    # ── Workspace ──
    "notion":          ["developers.notion.com"],
    "airtable":        ["airtable.com"],
    "linear":          ["linear.app"],

    # ── ML training infra ──
    "wandb":           ["docs.wandb.ai"],
    "mlflow":          ["mlflow.org"],
    "ray":             ["docs.ray.io"],
}


def list_platforms() -> list[str]:
    """Return all preset platform aliases (sorted)."""
    return sorted(_PRESETS.keys())


def resolve_platform(name: str) -> list[str]:
    """Return the host list for a preset, empty when unknown.

    Entries may be either bare hosts (``"docs.stripe.com"``) or
    ``host/path-prefix`` pairs (``"developers.facebook.com/docs/whatsapp"``)
    — the path narrows the site search via ``inurl:<path>``.
    """
    return list(_PRESETS.get((name or "").lower(), []))


def _split_host_path(spec: str) -> tuple[str, str]:
    """Split ``"host/path"`` → ``(host, path)``.  Path may be ``""``."""
    spec = spec.strip()
    for prefix in ("https://", "http://"):
        if spec.startswith(prefix):
            spec = spec[len(prefix):]
            break
    if "/" in spec:
        host, path = spec.split("/", 1)
        return host, path.strip("/")
    return spec, ""


def list_platforms() -> list[str]:
    """Return all preset platform aliases (sorted)."""
    return sorted(_PRESETS.keys())


# ---------------------------------------------------------------------------
# Categorisation — exposed so the MCP tool ``list_dev_docs_platforms``
# (and any future reporting tool) can group presets without hardcoding
# its own copy. Adding a new preset:
#   1. add a ``"<alias>": [<hosts>]`` row to _PRESETS above
#   2. add ``"<alias>"`` to one of the lists below (or leave it
#      uncategorised — list_dev_docs_platforms will still find it
#      via its substring filter, just without a category tag).
# ---------------------------------------------------------------------------

_CATEGORIES: dict[str, list[str]] = {
    "cloud_infra": [
        "google-cloud", "gcp", "aws", "azure", "microsoft", "docker",
        "kubernetes", "k8s", "hashicorp", "terraform", "github",
        "gitlab", "cloudflare", "vercel", "netlify", "fly", "render",
    ],
    "apis_saas": [
        "stripe", "twilio", "slack", "discord", "shopify", "supabase",
        "firebase", "mongodb", "redis", "postgres", "postgresql",
        "mysql", "elasticsearch",
    ],
    "social": [
        "tiktok", "tiktok-business", "tiktok-marketing", "tiktok-login",
        "snap", "snapchat", "snap-marketing", "twitter", "x",
        "pinterest", "reddit", "linkedin", "youtube",
    ],
    "messaging": [
        "whatsapp", "whatsapp-business", "whatsapp-cloud",
        "telegram", "telegram-bot", "messenger", "line", "viber",
        "wechat", "wechat-pay", "kakao", "instagram", "threads",
    ],
    "meta_megasite": [
        "meta", "facebook", "instagram", "messenger", "threads",
        "whatsapp",
    ],
    "google_products": [
        "google-cloud", "gcp", "firebase", "google-ads",
        "google-analytics", "google-maps", "google-pay", "youtube",
        "google-ai", "gemini",
    ],
    "mobile_ad_intel": [
        "data.ai", "data-ai", "appannie", "sensortower", "appsflyer",
        "appsflyer-performance-index", "appsflyer-pi",
        "appsflyer-benchmarks", "appsflyer-bench",
        "adjust", "branch", "branch.io",
        "applovin", "applovin-max", "bigspy",
        "similarweb", "admiral", "getadmiral", "businessofapps", "boa",
        "qimai", "qimai.cn", "七麦", "qimai-international",
        "diandian", "点点数据", "dianshu",
        "statista", "emarketer", "insiderintelligence",
        "socialpeta", "广大大", "guangdada",
        "ppc.land", "ppcland", "substack",
    ],
    "ai_ml": [
        "openai", "anthropic", "claude", "huggingface", "hf",
        "cohere", "pinecone", "google-ai", "gemini", "langchain",
        "llamaindex",
    ],
    "frontend": [
        "mdn", "mozilla", "react", "vue", "angular", "svelte",
        "nextjs", "next", "remix", "nuxt", "nodejs", "node", "deno",
        "bun", "python", "typescript", "rust", "go", "golang",
    ],
    "mobile_dev": [
        "android", "apple", "ios", "swift", "flutter",
        "react-native", "expo",
    ],
    "browsers": ["chrome", "webkit"],
    "observability": [
        "datadog", "grafana", "prometheus", "sentry", "opentelemetry",
    ],
    "identity": ["auth0", "okta", "clerk"],
    "workspace": ["notion", "airtable", "linear"],
    "ml_training": ["wandb", "mlflow", "ray"],
}


def list_categories() -> list[str]:
    """Return the high-level category names."""
    return list(_CATEGORIES.keys())


def categories_for(alias: str) -> list[str]:
    """Return every category that contains a preset alias."""
    a = (alias or "").lower()
    return [cat for cat, lst in _CATEGORIES.items() if a in lst]


def uncategorised_presets() -> list[str]:
    """Presets that exist in _PRESETS but aren't tagged in _CATEGORIES.

    Useful as a maintenance check — surfaces gaps where a new preset
    was added without updating the category map.
    """
    in_cats = {p for lst in _CATEGORIES.values() for p in lst}
    return sorted(p for p in _PRESETS if p not in in_cats)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class DevDocsEngine(BaseEngine):
    """Search any developer documentation site by name or host.

    Drives :class:`DuckDuckGoEngine` with ``site:<host>`` prepended.
    Accepts a preset platform (``platform="aws"``) or an arbitrary
    host (``site="docs.example.com"``).

    Optional refiners — applied as DDG modifiers:

    * ``mode="reference"``    add ``inurl:reference``
    * ``mode="changelog"``    add ``(inurl:changelog OR inurl:release)``
    * ``mode="api"``          add ``inurl:api``
    * ``product="<slug>"``    add ``inurl:<slug>`` (free-form)
    * ``api_version="v3"``    quote the version literal in the query
    """

    name = "dev_docs"
    max_retries = 1

    _MODES = {
        "search":    [],
        "reference": ["inurl:reference"],
        "changelog": ["(inurl:changelog OR inurl:release-notes OR inurl:release_notes)"],
        "api":       ["inurl:api"],
        "tutorial":  ["(inurl:tutorial OR inurl:guide)"],
        "examples":  ["(inurl:example OR inurl:examples)"],
    }

    def __init__(self, page):
        super().__init__(page)
        self.last_status: dict = {}
        self._ddg = DuckDuckGoEngine(page)
        # Lazy fallback engines — only instantiated when DDG drops to
        # zero results (which happens when the residential / datacenter
        # IP gets throttled).
        self._brave = None
        self._bing = None
        self._page = page

    def _get_brave(self):
        if self._brave is None:
            from .brave import BraveEngine
            self._brave = BraveEngine(self._page)
        return self._brave

    def _get_bing(self):
        if self._bing is None:
            from .bing import BingEngine
            self._bing = BingEngine(self._page)
        return self._bing

    def _search_with_fallback(self, ddg_query: str, limit: int) -> tuple[list, str]:
        """Run a DDG search; on zero results fall back to Brave then Bing.

        Returns ``(results, backend_used)``.
        """
        rs = self._ddg.search(ddg_query, limit=limit) or []
        if rs:
            return rs, "ddg"
        # DDG threw 0 — try Brave (different infra, usually unaffected by
        # DDG residential-proxy throttling).
        try:
            rs = self._get_brave().search(ddg_query, limit=limit) or []
            if rs:
                return rs, "brave"
        except Exception as e:  # pragma: no cover
            log.debug("brave fallback failed: %s", e)
        # Last resort — Bing.
        try:
            rs = self._get_bing().search(ddg_query, limit=limit) or []
            if rs:
                return rs, "bing"
        except Exception as e:  # pragma: no cover
            log.debug("bing fallback failed: %s", e)
        return [], "all-failed"

    def search(  # type: ignore[override]
        self,
        query: str,
        limit: int = 10,
        *,
        platform: Optional[str] = None,
        site: Optional[str] = None,
        mode: str = "search",
        product: Optional[str] = None,
        api_version: Optional[str] = None,
    ) -> list[SearchResult]:
        """Search a developer-docs site.

        Either ``platform`` or ``site`` is required (``platform`` wins
        when both are given). When ``platform`` resolves to multiple
        hosts the query OR-combines them.

        Args:
            query:       Free-text search terms.
            limit:       Max results to return.
            platform:    One of :func:`list_platforms` (e.g. "stripe",
                         "openai", "aws", "react").
            site:        Arbitrary host (e.g. "docs.example.com").
                         Use this to search a portal not in the preset
                         list.
            mode:        "search" / "reference" / "changelog" / "api" /
                         "tutorial" / "examples".
            product:     Add an extra ``inurl:<product>`` filter.
            api_version: Quote a version literal in the query.
        """
        m = (mode or "search").lower()
        if m not in self._MODES:
            raise ValueError(
                f"unknown mode {m!r}; choose from "
                f"{sorted(self._MODES.keys())}"
            )

        # Resolve hosts
        hosts: list[str] = []
        resolved_platform = ""
        if platform:
            hosts = resolve_platform(platform)
            if not hosts:
                raise ValueError(
                    f"unknown platform {platform!r}. "
                    f"Use list_platforms() or pass site=<host> directly."
                )
            resolved_platform = platform.lower()
        if site:
            # _split_host_path normalises the prefix and (host, path)
            # split. We want the raw spec (incl. path) preserved here
            # so it flows into hosts as one entry.
            normalised = site.strip()
            for prefix in ("https://", "http://"):
                if normalised.startswith(prefix):
                    normalised = normalised[len(prefix):]
                    break
            if normalised not in hosts:
                hosts.append(normalised)
        if not hosts:
            raise ValueError(
                "dev_docs requires either platform=<preset> or site=<host>. "
                "See list_platforms() for known presets."
            )

        ddg_query_full = self._build_ddg_query(
            query, hosts, m, product, api_version,
        )
        log.info("[dev_docs/%s] %s",
                 resolved_platform or hosts[0], ddg_query_full)

        # DDG handles "(site:a OR site:b)" poorly — it often returns
        # zero results when the OR-combined site list spans different
        # subdomains of the same brand (e.g.
        # developers.tiktok.com OR business-api.tiktok.com). Fan out
        # to one query per host and merge instead. Keeps the single-
        # host fast path as is.
        if len(hosts) == 1:
            results, backend = self._search_with_fallback(
                ddg_query_full, limit=max(limit * 2, 20))
        else:
            results = []
            backend = "ddg"
            seen_urls: set[str] = set()
            for host in hosts:
                sub_q = self._build_ddg_query(
                    query, [host], m, product, api_version,
                )
                sub_rs, sub_backend = self._search_with_fallback(
                    sub_q, limit=limit * 2)
                if sub_backend != "ddg":
                    backend = sub_backend
                for r in sub_rs:
                    if r.url and r.url not in seen_urls:
                        seen_urls.add(r.url)
                        results.append(r)
                if len(results) >= limit * 2:
                    break

        kept: list[SearchResult] = []
        seen: set[str] = set()
        host_specs = [_split_host_path(h) for h in hosts]
        for r in results:
            url = (r.url or "").lower()
            # Match (host, path?) — both must appear in URL.
            if not any(
                host in url and (not path or path in url)
                for host, path in host_specs
            ):
                continue
            if "translate.goog" in url or "/cache/" in url:
                continue
            if r.url in seen:
                continue
            seen.add(r.url)
            r.__dict__.update({
                "doc_site": self._matching_host(url, host_specs),
                "doc_section": self._infer_section(url),
                "platform": resolved_platform,
                "product": product or "",
                "api_version": api_version or self._infer_version(url),
            })
            kept.append(r)
            if len(kept) >= limit:
                break

        self.last_status = {
            "platform": resolved_platform,
            "hosts": hosts,
            "mode": m,
            "product": product,
            "api_version": api_version,
            "ddg_query": ddg_query_full,
            "backend": backend,
            "raw_results": len(results),
            "kept": len(kept),
        }
        return kept

    # ── helpers ──────────────────────────────────────────────────────

    @classmethod
    def _build_ddg_query(cls, query: str, hosts: list[str], mode: str,
                         product: Optional[str],
                         api_version: Optional[str]) -> str:
        parts: list[str] = []
        # Each entry can be either "host" or "host/path-prefix". Path
        # narrows via inurl: so a single preset can cover one product
        # inside a megasite (e.g. WhatsApp docs live under
        # developers.facebook.com/docs/whatsapp).
        if len(hosts) == 1:
            host, path = _split_host_path(hosts[0])
            parts.append(f"site:{host}")
            if path:
                parts.append(f"inurl:{path}")
        else:
            site_atoms = []
            for h in hosts:
                host, path = _split_host_path(h)
                if path:
                    site_atoms.append(f"(site:{host} inurl:{path})")
                else:
                    site_atoms.append(f"site:{host}")
            parts.append("(" + " OR ".join(site_atoms) + ")")
        parts.extend(cls._MODES.get(mode, []))
        if product:
            parts.append(f"inurl:{product}")
        if api_version:
            parts.append(f'"{api_version}"')
        if query:
            parts.append(query)
        return " ".join(parts)

    @staticmethod
    def _matching_host(url: str, host_specs: list[tuple[str, str]]) -> str:
        url_low = url.lower()
        for host, path in host_specs:
            if host in url_low and (not path or path in url_low):
                return f"{host}/{path}" if path else host
        return ""

    @staticmethod
    def _infer_section(url: str) -> str:
        u = url.lower()
        if "/reference/" in u or "/api/reference/" in u or "/api-reference/" in u:
            return "reference"
        if "changelog" in u or "release-notes" in u or "release_notes" in u:
            return "changelog"
        if "/get-started" in u or "/quickstart" in u or "/getting-started" in u:
            return "quickstart"
        # /tutorials/ /tutorial/ /tutorial-foo (URL-slug variant) /learn/
        # (React/Svelte/Astro all use /learn/ for tutorials).
        if (
            "/tutorials/" in u or "/tutorial/" in u
            or "/tutorial-" in u or "/learn/" in u
            or "/guides/" in u
        ):
            return "tutorial"
        if "/examples/" in u or "/example/" in u or "/cookbook/" in u:
            return "example"
        if "/blog/" in u:
            return "blog"
        if "/faq" in u:
            return "faq"
        return "guide"

    @staticmethod
    def _infer_version(url: str) -> str:
        m = re.search(r"/v(\d+(?:\.\d+){0,2})/", url)
        return f"v{m.group(1)}" if m else ""

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        return self.search(query, limit)
