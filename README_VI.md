<div align="center">

<img src="https://img.shields.io/badge/-🔍_AgentSearch-1f2937?style=for-the-badge" alt="AgentSearch" height="48"/>

### Công cụ tìm kiếm dành cho AI agent.

# **Miễn phí · Cục bộ · Riêng tư · Vượt Cloudflare**

**Một gói Python. 90+ trang web. Không API key. Không rò rỉ dữ liệu.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![Sites: 90+](https://img.shields.io/badge/Sites-90%2B-success.svg)]()
[![No API Key](https://img.shields.io/badge/Không-API_Key-success.svg)]()
[![Local Only](https://img.shields.io/badge/Dữ_liệu-Ở_máy_bạn-orange.svg)]()

[English](README.md) · [中文](README_CN.md) · [Português](README_PT.md) · [한국어](README_KR.md) · [日本語](README_JA.md) · [Русский](README_RU.md) · **[Tiếng Việt](README_VI.md)** · [हिन्दी](README_HI.md) · [ไทย](README_TH.md) · [Bahasa Indonesia](README_ID.md) · [Čeština](README_CS.md)

</div>

---

## ⚡ Bắt đầu trong 30 giây

```bash
pip install cloakbrowser && pip install -e .

# Tìm kiếm trên 90+ công cụ
agentsearch search "giá xăng hôm nay"        --engine coccoc --limit 5
agentsearch search "VN-Index dự báo"          --engine coccoc --limit 5
agentsearch search "react hooks hướng dẫn"    --engine google --limit 5

# SERP + nội dung top-3 trong một lần gọi
agentsearch search "kinh tế Việt Nam 2026" --engine coccoc --limit 5 --depth 3 --json
```

90 trang stealth · CLI · MCP server · HTTP API · chạy hoàn toàn trên máy bạn. **Vượt Cloudflare, PerimeterX, Akamai, DataDome.**

---

## 🇻🇳 Công cụ tìm kiếm Việt Nam (ưu tiên nội địa)

AgentSearch hỗ trợ **Cốc Cốc** — công cụ tìm kiếm Việt Nam giữ thị phần lớn nhất (~50%) cho nội dung tiếng Việt. Khi người dùng hỏi bằng tiếng Việt hoặc về chủ đề Việt Nam (chính trị, kinh tế, giải trí, du lịch), hãy dùng `coccoc` trước, sau đó mới đến `google`.

| Công cụ | Nguồn | Thế mạnh | Khi dùng |
|---|---|---|---|
| **`coccoc`** | [coccoc.com/search](https://coccoc.com/search) — thị phần ~50% nội địa | Khả năng thu hồi nội dung tiếng Việt vượt trội, hiểu dấu/không dấu, ưu tiên báo Việt Nam | **Lựa chọn đầu tiên cho truy vấn tiếng Việt** |

### Ví dụ sao chép–dán

```bash
# Tin tức / chính trị
agentsearch search "Quốc hội kỳ họp"           -e coccoc --limit 10
agentsearch search "Thủ tướng phát biểu"        -e coccoc --limit 10

# Kinh tế / chứng khoán
agentsearch search "VN-Index hôm nay"           -e coccoc --limit 5
agentsearch search "tỷ giá USD VND"             -e coccoc --limit 5

# Thể thao
agentsearch search "đội tuyển Việt Nam AFF Cup" -e coccoc --limit 5
agentsearch search "Hà Nội FC V-League"         -e coccoc --limit 5

# Giải trí / du lịch
agentsearch search "phim Việt mới ra rạp"       -e coccoc --limit 5
agentsearch search "du lịch Đà Nẵng tháng 12"   -e coccoc --limit 5

# Đời sống
agentsearch search "Sơn Tùng MTP album mới"     -e coccoc --limit 5
```

### Gọi từ MCP (Cursor / Claude Desktop / Cline / Continue / Kiro)

```jsonc
search(query="kinh tế Việt Nam 2026", engine="coccoc", limit=5, depth=3)
search(query="VN-Index dự báo",        engine="coccoc", limit=10)
```

---

## 🌐 Các công cụ hữu ích khác cho người dùng Việt

| Mục đích | Công cụ |
|---|---|
| Tìm kiếm chung toàn cầu | `google` · `duckduckgo` · `bing` |
| Thảo luận / "mọi người nghĩ gì" | `reddit` (tiếng Anh) |
| Video / hướng dẫn | `youtube` |
| Bài báo khoa học | `arxiv` (CS/ML) · `pubmed` (y học) |
| Code / lập trình | `github` · `stackoverflow` |
| Mua sắm | `amazon` · `ebay` |
| Tài liệu cho dev | `dev_docs` (Stripe / OpenAI / AWS / 142 nền tảng) |
| Thư viện quảng cáo | `meta_ad_library` · `tiktok_creative_center` · `google_ad_transparency` |
| App Store + Google Play | `search_app` / `lookup_app` |

Danh sách đầy đủ: chạy `agentsearch list-engines` hoặc xem [README tiếng Anh](README.md).

---

## ⚙️ Cài đặt

```bash
git clone https://github.com/t0ken-ai/AgentSearch.git
cd AgentSearch
pip install cloakbrowser
pip install -e .
```

Yêu cầu Python 3.9+. Lần chạy đầu tiên CloakBrowser (Chromium ẩn danh) sẽ tự tải về.

---

## 🔌 Cấu hình MCP server

Thêm vào `~/.kiro/settings/mcp.json` (hoặc tệp tương đương ở Cursor / Claude Desktop / Cline / Continue):

```json
{
  "mcpServers": {
    "agent-search": {
      "command": "/đường/dẫn/venv/bin/python",
      "args": ["-m", "agent_search.mcp_server"],
      "env": { "AGENTSEARCH_HEADLESS": "1" }
    }
  }
}
```

Các công cụ được phơi ra: `search`, `extract`, `extract_many`, `list_engines`, `list_dev_docs_platforms`, `search_app`, `lookup_app`, `find_competitor_ads`, `download_ad_media`.

---

## 📚 Tài liệu đầy đủ

Mọi thứ **không** có ở đây (bảng tham chiếu đầy đủ về các công cụ, tùy chọn lọc, thư viện quảng cáo, quy trình nghiên cứu đối thủ, danh sách 142 nền tảng dev_docs, …) đều nằm trong [**README tiếng Anh**](README.md) và [SKILL.md](skills/agent-search/SKILL.md).

---

*Một gói Python · 90+ công cụ tìm kiếm · 142 nền tảng tài liệu dev · 5 thư viện quảng cáo · App Store · 9 công cụ MCP — tất cả cục bộ, không cần API key.*
