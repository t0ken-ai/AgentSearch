<div align="center">

<img src="https://img.shields.io/badge/-🔍_AgentSearch-1f2937?style=for-the-badge" alt="AgentSearch" height="48"/>

### เครื่องมือค้นหาสำหรับ AI agent

# **ฟรี · ทำงานในเครื่อง · เป็นส่วนตัว · ผ่าน Cloudflare ได้**

**Python แพ็กเกจเดียว. 90+ เว็บไซต์. ไม่ต้องใช้ API key. ไม่มีข้อมูลรั่วไหล.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![Sites: 90+](https://img.shields.io/badge/Sites-90%2B-success.svg)]()
[![No API Key](https://img.shields.io/badge/ไม่ต้องใช้-API_Key-success.svg)]()
[![Local Only](https://img.shields.io/badge/ข้อมูล-อยู่ในเครื่องคุณ-orange.svg)]()

[English](README.md) · [中文](README_CN.md) · [Português](README_PT.md) · [한국어](README_KR.md) · [日本語](README_JA.md) · [Русский](README_RU.md) · [Tiếng Việt](README_VI.md) · [हिन्दी](README_HI.md) · **[ไทย](README_TH.md)** · [Bahasa Indonesia](README_ID.md) · [Čeština](README_CS.md)

</div>

---

## ⚡ เริ่มต้นใน 30 วินาที

```bash
pip install cloakbrowser && pip install -e .

# ค้นหาผ่าน engine ใดก็ได้จาก 90+ ตัว
agentsearch search "ราคาทองวันนี้"      --engine thairath --limit 5
agentsearch search "ร้านอาหารกรุงเทพ"   --engine pantip   --limit 5
agentsearch search "react hooks"         --engine google   --limit 5

# SERP + เนื้อหา top-3 ในคำสั่งเดียว
agentsearch search "เลือกตั้ง 2569" --engine thairath --limit 5 --depth 3 --json
```

90 เว็บไซต์ stealth · CLI · MCP server · HTTP API · ทำงานบนเครื่องคุณทั้งหมด **ผ่าน Cloudflare, PerimeterX, Akamai, DataDome ได้**

---

## 🇹🇭 Search engine สำหรับเมืองไทย (ลำดับความสำคัญในประเทศ)

AgentSearch รองรับ engine ไทยสองตัวที่ครอบคลุมพฤติกรรมหลักของคนไทย: **Pantip** (เว็บบอร์ดเบอร์หนึ่ง คล้าย Reddit ของไทย) และ **Thairath** (หนังสือพิมพ์ใหญ่ที่สุดของประเทศ) เมื่อผู้ใช้พิมพ์ภาษาไทยหรือถามเรื่องไทย ให้ลองสองตัวนี้ก่อน `google` เสมอ

| Engine | แหล่งที่มา | จุดเด่น | ใช้ตอนไหน |
|---|---|---|---|
| **`pantip`** | [search.pantip.com](https://search.pantip.com/ss) — Pantip Smart Search | ค้นกระทู้ Pantip ได้ครบ ทั้งห้องสินธร / ไกลบ้าน / สวนลุมพินี / ครัว / ฯลฯ | **ความเห็นจริงของคนไทย, รีวิว, ดราม่า** |
| **`thairath`** | [thairath.co.th](https://www.thairath.co.th/search) — ไทยรัฐออนไลน์ | ข่าวการเมือง / เศรษฐกิจ / กีฬา / บันเทิงไทยล่าสุด | **ข่าวประจำวันของไทย** |

### ตัวอย่างที่ใช้ได้ทันที

```bash
# ข่าว / การเมือง
agentsearch search "นายกรัฐมนตรีแถลง"      -e thairath --limit 10
agentsearch search "เลือกตั้งผู้ว่า กทม"     -e thairath --limit 10

# เศรษฐกิจ / การเงิน
agentsearch search "ราคาทองคำวันนี้"          -e thairath --limit 5
agentsearch search "ค่าเงินบาท"               -e thairath --limit 5

# กีฬา
agentsearch search "ทีมชาติไทยซีเกมส์"        -e thairath --limit 5

# ไลฟ์สไตล์ / ดราม่า / รีวิว (Pantip)
agentsearch search "ร้านอาหารกรุงเทพแนะนำ"  -e pantip   --limit 5
agentsearch search "รีวิวซีรีส์เกาหลี"          -e pantip   --limit 5
agentsearch search "ห้องสินธร หุ้นไทย"        -e pantip   --limit 5
agentsearch search "ที่เที่ยวเชียงใหม่"          -e pantip   --limit 5
```

### เรียกผ่าน MCP (Cursor / Claude Desktop / Cline / Continue / Kiro)

```jsonc
search(query="ราคาทองวันนี้",      engine="thairath", limit=5, depth=3)
search(query="ร้านอาหารกรุงเทพ",  engine="pantip",   limit=10)
```

---

## 🌐 Engine อื่น ๆ ที่คนไทยใช้ได้

| ใช้สำหรับ | Engine |
|---|---|
| ค้นหาทั่วไประดับโลก | `google` · `duckduckgo` · `bing` |
| ความเห็น / "คนอื่นคิดยังไง" | `reddit` (อังกฤษ) |
| วิดีโอ / สอนทำ | `youtube` |
| งานวิจัย / paper | `arxiv` (CS/ML) · `pubmed` (การแพทย์) |
| โค้ด / dev | `github` · `stackoverflow` |
| ช้อปปิ้ง | `amazon` · `ebay` |
| เอกสาร dev | `dev_docs` (Stripe / OpenAI / AWS / 142 platform) |
| คลังโฆษณา | `meta_ad_library` · `tiktok_creative_center` · `google_ad_transparency` |
| App Store + Google Play | `search_app` / `lookup_app` |

ดูทั้งหมด: รัน `agentsearch list-engines` หรืออ่าน [README ภาษาอังกฤษ](README.md)

---

## ⚙️ การติดตั้ง

```bash
git clone https://github.com/t0ken-ai/AgentSearch.git
cd AgentSearch
pip install cloakbrowser
pip install -e .
```

ต้องใช้ Python 3.9+ การรันครั้งแรก CloakBrowser (Chromium stealth) จะดาวน์โหลดเองอัตโนมัติ

---

## 🔌 ตั้งค่า MCP server

เพิ่มลงใน `~/.kiro/settings/mcp.json` (หรือไฟล์เทียบเท่าใน Cursor / Claude Desktop / Cline / Continue):

```json
{
  "mcpServers": {
    "agent-search": {
      "command": "/path/to/venv/bin/python",
      "args": ["-m", "agent_search.mcp_server"],
      "env": { "AGENTSEARCH_HEADLESS": "1" }
    }
  }
}
```

เครื่องมือที่เปิดให้ใช้: `search`, `extract`, `extract_many`, `list_engines`, `list_dev_docs_platforms`, `search_app`, `lookup_app`, `find_competitor_ads`, `download_ad_media`

---

## 📚 เอกสารทั้งหมด

ทุกอย่างที่ **ไม่มี** ในไฟล์นี้ (ตารางตัวเลือกของแต่ละ engine, คลังโฆษณา, การวิเคราะห์คู่แข่ง, รายชื่อ 142 platform ของ dev_docs ฯลฯ) อยู่ใน [**README ภาษาอังกฤษ**](README.md) และ [SKILL.md](skills/agent-search/SKILL.md)

---

*Python แพ็กเกจเดียว · 90+ search engines · 142 dev-docs platforms · 5 ad libraries · App Store · 9 MCP tools — ทำงานในเครื่องทั้งหมด ไม่ต้องใช้ API key*
