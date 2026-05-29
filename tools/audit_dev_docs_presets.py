"""Live audit for every dev_docs preset.

For each preset:
  • build a generic query keyed off the platform alias
  • DDG search via DevDocsEngine
  • record (preset, host_count, hits, latency, sample_url)

Output is JSON Lines so the result can be re-processed.

Run::

  ~/tools/cloakbrowser/venv/bin/python tools/audit_dev_docs_presets.py
"""
from __future__ import annotations

import json
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from agent_search.core import BrowserConfig, launch, new_page
from agent_search.engines.dev_docs import DevDocsEngine, _PRESETS

# Per-preset query — picks something that should plausibly exist.
# Default: use the preset name itself.
_QUERIES: dict[str, str] = {
    # cloud
    "google-cloud":     "compute engine",
    "gcp":              "compute engine",
    "aws":              "lambda function",
    "azure":            "function app",
    "microsoft":        "graph api",
    "docker":           "compose volume",
    "kubernetes":       "deployment yaml",
    "k8s":              "deployment yaml",
    "hashicorp":        "terraform provider",
    "terraform":        "provider aws",
    "github":           "actions workflow",
    "gitlab":           "ci yaml",
    "cloudflare":       "workers kv",
    "vercel":           "edge functions",
    "netlify":          "edge functions",
    "fly":              "deploy machines",
    "render":           "deploy api",
    # APIs
    "stripe":           "payment intent",
    "twilio":           "send sms",
    "slack":            "block kit",
    "discord":          "interactions",
    "shopify":          "graphql api",
    "supabase":         "auth signup",
    "firebase":         "firestore rules",
    "mongodb":          "aggregation pipeline",
    "redis":            "lua scripting",
    "postgres":         "create index",
    "postgresql":       "create index",
    "mysql":            "create table",
    "elasticsearch":    "query dsl",
    # social
    "tiktok":           "login kit",
    "tiktok-business":  "ad creation",
    "tiktok-marketing": "campaign create",
    "tiktok-login":     "oauth scope",
    "snap":             "login kit",
    "snapchat":         "login kit",
    "snap-marketing":   "ad accounts",
    "twitter":          "v2 endpoints",
    "x":                "v2 endpoints",
    "linkedin":         "share api",
    "pinterest":        "create pin",
    "reddit":           "submit post",
    "youtube":          "data api search",
    # messaging
    "whatsapp":         "send message template",
    "whatsapp-business":"phone number registration",
    "whatsapp-cloud":   "webhook setup",
    "telegram":         "sendMessage",
    "telegram-bot":     "inline keyboard",
    "messenger":        "send api",
    "instagram":        "graph api media",
    "threads":          "publishing api",
    "line":             "flex message",
    "viber":            "send message",
    "wechat":           "mini program login",
    "wechat-pay":       "JSAPI 支付",
    "kakao":            "login redirect",
    # google products
    "google-ads":       "campaign budget",
    "google-analytics": "measurement protocol",
    "google-maps":      "geocoding api",
    "google-pay":       "request payment",
    # AI / ML
    "openai":           "embeddings",
    "anthropic":        "tool use",
    "claude":           "tool use",
    "huggingface":      "transformers pipeline",
    "hf":               "transformers pipeline",
    "cohere":           "embed v3",
    "pinecone":         "vector upsert",
    "google-ai":        "gemini api",
    "gemini":           "gemini api",
    "langchain":        "retrieval qa",
    "llamaindex":       "vector store",
    # frontend / lang
    "mdn":              "fetch api",
    "mozilla":          "fetch api",
    "react":            "useEffect",
    "vue":              "composition api",
    "angular":          "signal",
    "svelte":           "runes",
    "nextjs":           "app router",
    "next":             "app router",
    "remix":            "loader action",
    "nuxt":             "server routes",
    "nodejs":           "fs promises",
    "node":             "fs promises",
    "deno":             "kv namespace",
    "bun":              "bun test",
    "python":           "asyncio gather",
    "typescript":       "generics",
    "rust":             "iterator trait",
    "go":               "context cancel",
    "golang":           "context cancel",
    # mobile
    "android":          "jetpack compose",
    "apple":            "swiftui state",
    "ios":              "swiftui state",
    "swift":            "concurrency",
    "flutter":          "stateful widget",
    "react-native":     "navigation",
    "expo":             "router",
    # browsers
    "chrome":           "service worker",
    "webkit":           "css feature",
    # observability
    "datadog":          "logs query",
    "grafana":          "alerting rules",
    "prometheus":       "query language",
    "sentry":           "javascript sdk",
    "opentelemetry":    "tracing python",
    # identity
    "auth0":            "actions login",
    "okta":             "oauth scopes",
    "clerk":            "user management",
    # workspace
    "notion":           "blocks api",
    "airtable":         "create record",
    "linear":           "graphql mutations",
    # ml infra
    "wandb":            "log metric",
    "mlflow":           "tracking server",
    "ray":              "ray serve",
    # meta
    "meta":             "marketing api",
    "facebook":         "marketing api",
}


def main() -> int:
    cfg = BrowserConfig(headless=True, humanize=False, proxy=None)
    b = launch(cfg)
    out_path = "/tmp/dev_docs_audit.jsonl"
    fail_path = "/tmp/dev_docs_audit_failed.txt"

    presets = sorted(_PRESETS.keys())
    print(f"[audit] {len(presets)} presets")
    print(f"[audit] writing → {out_path}")

    failed = []
    with open(out_path, "w") as f:
        try:
            page = new_page(b)
            eng = DevDocsEngine(page)
            for i, plat in enumerate(presets, 1):
                hosts = _PRESETS[plat]
                q = _QUERIES.get(plat, plat.replace("-", " "))
                t0 = time.time()
                err = ""
                hits = 0
                first_url = ""
                try:
                    rs = eng.search(q, limit=3, platform=plat)
                    hits = len(rs)
                    if rs:
                        first_url = rs[0].url
                except Exception as e:
                    err = f"{type(e).__name__}: {e}"
                elapsed = time.time() - t0
                rec = {
                    "preset":   plat,
                    "hosts":    hosts,
                    "query":    q,
                    "hits":     hits,
                    "elapsed":  round(elapsed, 1),
                    "first":    first_url[:120],
                    "err":      err,
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                mark = "✓" if hits > 0 else ("✗" if not err else "💥")
                line = f"[{i:>3}/{len(presets)}] {mark} {plat:<24} hits={hits} t={elapsed:>5.1f}s"
                if first_url:
                    line += f"  {first_url[:55]}"
                if err:
                    line += f"  ERR={err[:80]}"
                print(line)
                if hits == 0:
                    failed.append(plat)
        finally:
            b.close()

    with open(fail_path, "w") as f:
        for p in failed:
            f.write(p + "\n")
    print(f"\n[audit] {len(failed)} failed → {fail_path}")
    print("[audit] failed presets:")
    for p in failed:
        print(f"  - {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
