<div align="center">

<img src="https://img.shields.io/badge/-🔍_AgentSearch-1f2937?style=for-the-badge" alt="AgentSearch" height="48"/>

### Vyhledávač pro AI agenty.

# **Zdarma · Lokálně · Soukromě · Obejde Cloudflare**

**Jeden Python balíček. 90+ webů. Žádné API klíče. Žádný únik dat.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![Sites: 90+](https://img.shields.io/badge/Sites-90%2B-success.svg)]()
[![No API Key](https://img.shields.io/badge/Bez-API_kl%C3%AD%C4%8De-success.svg)]()
[![Local Only](https://img.shields.io/badge/Data-Z%C5%AFstanou_u_v%C3%A1s-orange.svg)]()

[English](README.md) · [中文](README_CN.md) · [Português](README_PT.md) · [한국어](README_KR.md) · [日本語](README_JA.md) · [Русский](README_RU.md) · [Tiếng Việt](README_VI.md) · [हिन्दी](README_HI.md) · [ไทย](README_TH.md) · [Bahasa Indonesia](README_ID.md) · **[Čeština](README_CS.md)**

</div>

---

## ⚡ Začněte za 30 sekund

```bash
pip install cloakbrowser && pip install -e .

# Vyhledejte přes kterýkoli z 90+ enginů
agentsearch search "počasí Praha"             --engine seznam --limit 5
agentsearch search "volby 2025 výsledky"      --engine seznam --limit 5
agentsearch search "react hooks tutoriál"     --engine google --limit 5

# SERP + texty top 3 v jednom volání
agentsearch search "ČNB úroková sazba" --engine seznam --limit 5 --depth 3 --json
```

90 stealth webů · CLI · MCP server · HTTP API · vše běží přímo na vašem počítači. **Obejde Cloudflare, PerimeterX, Akamai, DataDome.**

---

## 🇨🇿 Český vyhledávač (lokální priorita)

AgentSearch nativně podporuje **Seznam.cz** — jeden z mála evropských vyhledávačů, který si i v roce 2026 udržuje kolem 30% trhu proti Googlu. Když uživatel píše česky nebo se ptá na české reálie (politika, sport, kultura, počasí, mapy), vyzkoušejte nejprve `seznam`, teprve potom `google`.

| Engine | Zdroj | Silné stránky | Kdy použít |
|---|---|---|---|
| **`seznam`** | [search.seznam.cz](https://search.seznam.cz/) — ~30 % českého trhu | Lepší recall českých webů, .cz domén, českých zpravodajských serverů | **První volba pro česky psané dotazy** |

### Hotové příklady

```bash
# Zprávy / politika
agentsearch search "Babiš ANO volby"               -e seznam --limit 10
agentsearch search "vláda Fiala koalice SPOLU"      -e seznam --limit 10

# Ekonomika / finance
agentsearch search "ČNB sazba zasedání"             -e seznam --limit 5
agentsearch search "kurz koruny dolar"              -e seznam --limit 5

# Sport
agentsearch search "Slavia Sparta derby"            -e seznam --limit 5
agentsearch search "Češi hokej MS"                  -e seznam --limit 5

# Životní styl / kultura
agentsearch search "Karlovy Vary festival 2026"     -e seznam --limit 5
agentsearch search "Karel Gott vzpomínka"           -e seznam --limit 5

# Lokální / praktické
agentsearch search "počasí Praha víkend"            -e seznam --limit 5
agentsearch search "Mapy.cz turistická trasa"       -e seznam --limit 5
```

### Z MCP (Cursor / Claude Desktop / Cline / Continue / Kiro)

```jsonc
search(query="ČNB sazba",      engine="seznam", limit=5, depth=3)
search(query="volby 2025",     engine="seznam", limit=10)
```

---

## 🌐 Další užitečné enginy pro české uživatele

| K čemu | Engine |
|---|---|
| Globální obecné vyhledávání | `google` · `duckduckgo` · `bing` |
| Diskuze / "co lidi říkají" | `reddit` (anglicky) |
| Video / návody | `youtube` |
| Akademické články | `arxiv` (CS/ML) · `pubmed` (medicína) |
| Kód / dev | `github` · `stackoverflow` |
| Nákupy | `amazon` · `ebay` |
| Vývojářská dokumentace | `dev_docs` (Stripe / OpenAI / AWS / 142 platforem) |
| Reklamní knihovny | `meta_ad_library` · `tiktok_creative_center` · `google_ad_transparency` |
| App Store + Google Play | `search_app` / `lookup_app` |

Kompletní seznam: spusťte `agentsearch list-engines` nebo [README v angličtině](README.md).

---

## ⚙️ Instalace

```bash
git clone https://github.com/t0ken-ai/AgentSearch.git
cd AgentSearch
pip install cloakbrowser
pip install -e .
```

Vyžaduje Python 3.9+. Při prvním spuštění se CloakBrowser (stealth Chromium) stáhne automaticky.

---

## 🔌 Nastavení MCP serveru

Přidejte do `~/.kiro/settings/mcp.json` (nebo ekvivalentního souboru v Cursor / Claude Desktop / Cline / Continue):

```json
{
  "mcpServers": {
    "agent-search": {
      "command": "/cesta/k/venv/bin/python",
      "args": ["-m", "agent_search.mcp_server"],
      "env": { "AGENTSEARCH_HEADLESS": "1" }
    }
  }
}
```

Vystavené nástroje: `search`, `extract`, `extract_many`, `list_engines`, `list_dev_docs_platforms`, `search_app`, `lookup_app`, `find_competitor_ads`, `download_ad_media`.

---

## 📚 Kompletní dokumentace

Vše, co tu **není** (kompletní reference enginů, filtrovací volby, reklamní knihovny, workflow konkurenční analýzy, seznam 142 dev_docs platforem atd.) najdete v [**anglickém README**](README.md) a [SKILL.md](skills/agent-search/SKILL.md).

---

*Jeden Python balíček · 90+ vyhledávačů · 142 vývojářských dokumentací · 5 reklamních knihoven · App Store · 9 MCP nástrojů — vše lokálně, bez API klíčů.*
