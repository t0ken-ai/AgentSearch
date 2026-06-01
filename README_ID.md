<div align="center">

<img src="https://img.shields.io/badge/-🔍_AgentSearch-1f2937?style=for-the-badge" alt="AgentSearch" height="48"/>

### Mesin pencari untuk AI agent.

# **Gratis · Lokal · Privat · Menembus Cloudflare**

**Satu paket Python. 90+ situs. Tanpa API key. Tanpa kebocoran data.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![Sites: 90+](https://img.shields.io/badge/Sites-90%2B-success.svg)]()
[![No API Key](https://img.shields.io/badge/Tanpa-API_Key-success.svg)]()
[![Local Only](https://img.shields.io/badge/Data-Tetap_di_komputermu-orange.svg)]()

[English](README.md) · [中文](README_CN.md) · [Português](README_PT.md) · [한국어](README_KR.md) · [日本語](README_JA.md) · [Русский](README_RU.md) · [Tiếng Việt](README_VI.md) · [हिन्दी](README_HI.md) · [ไทย](README_TH.md) · **[Bahasa Indonesia](README_ID.md)** · [Čeština](README_CS.md)

</div>

---

## ⚡ Mulai dalam 30 detik

```bash
pip install cloakbrowser && pip install -e .

# Cari di salah satu dari 90+ engine
agentsearch search "banjir Jakarta"           --engine detik  --limit 5
agentsearch search "harga BBM Pertamina"      --engine kompas --limit 5
agentsearch search "react hooks tutorial"     --engine google --limit 5

# SERP + isi top-3 dalam satu panggilan
agentsearch search "kenaikan PPN" --engine kompas --limit 5 --depth 3 --json
```

90 situs stealth · CLI · MCP server · HTTP API · semua berjalan di komputer kamu. **Menembus Cloudflare, PerimeterX, Akamai, DataDome.**

---

## 🇮🇩 Mesin pencari Indonesia (prioritas lokal)

AgentSearch mendukung dua portal berita terbesar di Indonesia: **Detik** (portal berita digital terbesar) dan **Kompas** (koran nasional terbesar). Saat pengguna menulis dalam Bahasa Indonesia atau bertanya soal Indonesia (politik, ekonomi, sepak bola, budaya), gunakan keduanya sebelum jatuh ke `google`.

| Engine | Sumber | Kelebihan | Kapan dipakai |
|---|---|---|---|
| **`detik`** | [detik.com](https://www.detik.com/search/searchall) — detiknews / detikfinance / detiktravel / detikSport / 20detik | Berita real-time, vertikal lengkap (otomotif, sepak bola, hiburan) | **Berita harian, breaking news** |
| **`kompas`** | [search.kompas.com](https://search.kompas.com/search/) — Kompas Gramedia | Reportase mendalam, tulisan editorial, ekonomi makro | **Analisis, ekonomi, kebijakan publik** |

### Contoh siap pakai

```bash
# Politik / berita
agentsearch search "Jokowi pidato kenegaraan"       -e detik   --limit 10
agentsearch search "Presiden Prabowo kebijakan"      -e kompas  --limit 10

# Ekonomi / keuangan
agentsearch search "BI rate suku bunga"              -e kompas  --limit 5
agentsearch search "kurs rupiah hari ini"            -e detik   --limit 5

# Olahraga
agentsearch search "Timnas Indonesia kualifikasi"    -e detik   --limit 5
agentsearch search "Persib Persija Liga 1"           -e detik   --limit 5

# Lifestyle / kuliner / wisata
agentsearch search "kuliner Jakarta hits"            -e detik   --limit 5
agentsearch search "wisata Bali murah"               -e kompas  --limit 5

# Lalu lintas / cuaca / kota
agentsearch search "banjir Jakarta hari ini"         -e detik   --limit 5
agentsearch search "macet Jabodetabek"               -e detik   --limit 5
```

### Dari MCP (Cursor / Claude Desktop / Cline / Continue / Kiro)

```jsonc
search(query="banjir Jakarta",   engine="detik",  limit=5, depth=3)
search(query="kenaikan PPN",     engine="kompas", limit=10)
```

---

## 🌐 Engine lain yang berguna untuk pengguna Indonesia

| Untuk… | Engine |
|---|---|
| Pencarian umum global | `google` · `duckduckgo` · `bing` |
| Diskusi / "apa kata orang" | `reddit` (bahasa Inggris) |
| Video / tutorial | `youtube` |
| Riset akademik | `arxiv` (CS/ML) · `pubmed` (medis) |
| Kode / dev | `github` · `stackoverflow` |
| Belanja | `amazon` · `ebay` |
| Dokumentasi developer | `dev_docs` (Stripe / OpenAI / AWS / 142 platform) |
| Pustaka iklan | `meta_ad_library` · `tiktok_creative_center` · `google_ad_transparency` |
| App Store + Google Play | `search_app` / `lookup_app` |

Daftar lengkap: jalankan `agentsearch list-engines` atau lihat [README bahasa Inggris](README.md).

---

## ⚙️ Instalasi

```bash
git clone https://github.com/t0ken-ai/AgentSearch.git
cd AgentSearch
pip install cloakbrowser
pip install -e .
```

Butuh Python 3.9+. Saat dijalankan pertama kali, CloakBrowser (Chromium stealth) akan terunduh otomatis.

---

## 🔌 Konfigurasi MCP server

Tambahkan ke `~/.kiro/settings/mcp.json` (atau berkas setara di Cursor / Claude Desktop / Cline / Continue):

```json
{
  "mcpServers": {
    "agent-search": {
      "command": "/path/ke/venv/bin/python",
      "args": ["-m", "agent_search.mcp_server"],
      "env": { "AGENTSEARCH_HEADLESS": "1" }
    }
  }
}
```

Tools yang dibuka: `search`, `extract`, `extract_many`, `list_engines`, `list_dev_docs_platforms`, `search_app`, `lookup_app`, `find_competitor_ads`, `download_ad_media`.

---

## 📚 Dokumentasi lengkap

Semua yang **tidak** ada di sini (referensi penuh setiap engine, opsi filter, pustaka iklan, alur riset kompetitor, daftar 142 platform dev_docs, dll.) ada di [**README bahasa Inggris**](README.md) dan [SKILL.md](skills/agent-search/SKILL.md).

---

*Satu paket Python · 90+ mesin pencari · 142 platform dokumentasi developer · 5 pustaka iklan · App Store · 9 tools MCP — semuanya lokal, tanpa API key.*
