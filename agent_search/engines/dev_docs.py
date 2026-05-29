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
    spec = spec.strip().lstrip("https://").lstrip("http://")
    if "/" in spec:
        host, path = spec.split("/", 1)
        return host, path.strip("/")
    return spec, ""


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
            host = site.strip().lstrip("https://").lstrip("http://")
            host = host.split("/", 1)[0]
            if host not in hosts:
                hosts.append(host)
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
            results = self._ddg.search(
                ddg_query_full, limit=max(limit * 2, 20)) or []
        else:
            results = []
            seen_urls: set[str] = set()
            for host in hosts:
                sub_q = self._build_ddg_query(
                    query, [host], m, product, api_version,
                )
                for r in (self._ddg.search(sub_q, limit=limit * 2) or []):
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
