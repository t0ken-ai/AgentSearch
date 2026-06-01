<div align="center">

<img src="https://img.shields.io/badge/-🔍_AgentSearch-1f2937?style=for-the-badge" alt="AgentSearch" height="48"/>

### O motor de busca para agentes de IA.

# **Grátis · Local · Privado · Burla Cloudflare**

**Um pacote Python. 90+ sites. Zero chaves de API. Zero vazamento de dados.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![Sites: 90+](https://img.shields.io/badge/Sites-90%2B-success.svg)]()
[![Sem API Key](https://img.shields.io/badge/Sem-API%20Key-success.svg)]()
[![Local](https://img.shields.io/badge/Dados-Ficam%20na%20sua%20m%C3%A1quina-orange.svg)]()

[English](README.md) · [中文](README_CN.md) · **[Português](README_PT.md)** · [한국어](README_KR.md) · [日本語](README_JA.md) · [Русский](README_RU.md) · [Tiếng Việt](README_VI.md) · [हिन्दी](README_HI.md) · [ไทย](README_TH.md) · [Bahasa Indonesia](README_ID.md) · [Čeština](README_CS.md)

</div>

---

## ⚡ Em 30 segundos

```bash
pip install cloakbrowser && pip install -e .

# Buscar em qualquer um dos 90+ motores
agentsearch search "eleições 2026"           --engine g1     --limit 5
agentsearch search "reforma tributária"      --engine terra  --limit 5
agentsearch search "Copa do Mundo 2026"      --engine google --limit 5

# SERP + corpo dos top-3 em uma chamada
agentsearch search "Lula reforma" --engine g1 --limit 5 --depth 3 --json
```

90 sites stealth · CLI · servidor MCP · API HTTP · roda 100% na sua máquina. **Burla Cloudflare, PerimeterX, Akamai, DataDome.**

---

## 🇧🇷 Motores de busca brasileiros (foco local)

O AgentSearch traz dois motores nativos do Brasil. Use-os antes do Google sempre que o usuário falar de **política**, **economia**, **esportes brasileiros**, **celebridades** ou qualquer assunto que tenha melhor cobertura em português.

| Motor | Fonte | Cobertura | Quando usar |
|---|---|---|---|
| **`g1`** | [g1.globo.com](https://g1.globo.com) — rede Globo | Notícias hard (política, economia, polícia, internacional, regional, esportes da Globo Esporte) | Primeira escolha para **notícias do Brasil**. |
| **`terra`** | [terra.com.br](https://www.terra.com.br) | Cobertura geral (notícias + entretenimento + lifestyle + esportes), via Google CSE interno | Complementa o G1 com matérias de portais parceiros (UOL, BBC, ESPN, …). |

### Exemplos prontos para colar

```bash
# Política / eleições
agentsearch search "eleições presidenciais 2026"  -e g1     --limit 10
agentsearch search "PEC reforma tributária"        -e terra  --limit 10

# Economia
agentsearch search "Selic decisão Copom"           -e g1     --limit 5
agentsearch search "dólar cotação hoje"            -e terra  --limit 5

# Esportes
agentsearch search "Flamengo Libertadores"          -e g1     --limit 5
agentsearch search "Neymar volta seleção"           -e terra  --limit 5

# Cultura / entretenimento
agentsearch search "BBB 2026 paredão"               -e terra  --limit 5
```

### Pelo MCP (Cursor / Claude Desktop / Cline / Continue / Kiro)

```jsonc
search(query="reforma tributária", engine="g1",    limit=5, depth=3)
search(query="Copa do Mundo 2026", engine="terra", limit=10)
```

---

## 🌐 Outros motores úteis para o público brasileiro

| Para… | Use o motor |
|---|---|
| Discussões / "o que acham" | `reddit` (em inglês) |
| Vídeos / tutoriais | `youtube` |
| Pesquisa acadêmica | `arxiv` (CS/ML) · `pubmed` (médico) |
| Código | `github` · `stackoverflow` |
| Compras | `amazon` · `ebay` |
| Documentação dev | `dev_docs` (142 plataformas: Stripe, OpenAI, AWS, …) |
| Anúncios de concorrentes | `meta_ad_library` · `tiktok_creative_center` · `google_ad_transparency` |
| App Store + Google Play | `search_app` / `lookup_app` |

Lista completa: rode `agentsearch list-engines` ou consulte o [README em inglês](README.md).

---

## ⚙️ Instalação

```bash
git clone https://github.com/t0ken-ai/AgentSearch.git
cd AgentSearch
pip install cloakbrowser
pip install -e .
```

Requer Python 3.9+. Na primeira execução o CloakBrowser (Chromium stealth) baixa automaticamente.

---

## 🔌 Servidor MCP

Adicione ao `~/.kiro/settings/mcp.json` (ou ao equivalente no Cursor / Claude Desktop / Cline / Continue):

```json
{
  "mcpServers": {
    "agent-search": {
      "command": "/caminho/para/venv/bin/python",
      "args": ["-m", "agent_search.mcp_server"],
      "env": { "AGENTSEARCH_HEADLESS": "1" }
    }
  }
}
```

Ferramentas expostas: `search`, `extract`, `extract_many`, `list_engines`, `list_dev_docs_platforms`, `search_app`, `lookup_app`, `find_competitor_ads`, `download_ad_media`.

---

## 📚 Documentação completa

Tudo o que **não** está aqui (referência completa de motores, opções de filtro, biblioteca de anúncios, fluxo de pesquisa de concorrentes, lista de 142 plataformas de docs dev, etc.) está no [**README em inglês**](README.md) e no [SKILL.md](skills/agent-search/SKILL.md).

---

*Um pacote Python · 90+ motores de busca · 142 plataformas de docs · 5 bibliotecas de anúncios · App Store · 9 ferramentas MCP — tudo local, sem chaves de API.*
