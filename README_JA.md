<div align="center">

<img src="https://img.shields.io/badge/-🔍_AgentSearch-1f2937?style=for-the-badge" alt="AgentSearch" height="48"/>

### AI エージェントのための検索エンジン。

# **無料 · ローカル · プライベート · Cloudflare 突破**

**Python パッケージひとつ。90+ サイト。API キー 0。データ漏洩 0。**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![Sites: 90+](https://img.shields.io/badge/Sites-90%2B-success.svg)]()
[![No API Key](https://img.shields.io/badge/API_Key-不要-success.svg)]()
[![Local Only](https://img.shields.io/badge/データ-自分のPC内-orange.svg)]()

[English](README.md) · [中文](README_CN.md) · [Português](README_PT.md) · [한국어](README_KR.md) · **[日本語](README_JA.md)** · [Русский](README_RU.md) · [Tiếng Việt](README_VI.md) · [हिन्दी](README_HI.md) · [ไทย](README_TH.md) · [Bahasa Indonesia](README_ID.md) · [Čeština](README_CS.md)

</div>

---

## ⚡ 30秒スタート

```bash
pip install cloakbrowser && pip install -e .

# 90+ サイトを直接検索
agentsearch search "生成AI 業界動向"   --engine yahoo_japan --limit 5
agentsearch search "東京オリンピック"  --engine yahoo_japan --limit 5
agentsearch search "react hooks 入門"  --engine google      --limit 5

# 検索結果 + 上位3件の本文を一括取得
agentsearch search "半導体 国策"  --engine yahoo_japan --limit 5 --depth 3 --json
```

90 ステルスサイト · CLI · MCP サーバー · HTTP API · すべてローカル実行。**Cloudflare / PerimeterX / Akamai / DataDome を突破。**

---

## 🇯🇵 日本市場向け検索エンジン(ローカル優先)

AgentSearch は日本検索市場のシェア No.2 である **Yahoo! JAPAN(`yahoo_japan`)** をネイティブに対応しています。日本語クエリや日本国内ニュース・芸能・経済では、まず Yahoo! JAPAN を試してから `google` にフォールバックしてください。

| エンジン | ソース | 強み | 使いどころ |
|---|---|---|---|
| **`yahoo_japan`** | [search.yahoo.co.jp](https://search.yahoo.co.jp) — 日本シェア ~25% | 国内ニュース・知恵袋・YJショッピングが上位に出やすく、日本語ロングテールに強い | **日本語クエリの第一候補** |

### コピペで使えるサンプル

```bash
# ニュース / 時事
agentsearch search "首相 所信表明演説"        -e yahoo_japan --limit 10
agentsearch search "日銀 金融政策決定会合"     -e yahoo_japan --limit 10

# ビジネス / 株式
agentsearch search "トヨタ 決算発表"           -e yahoo_japan --limit 5
agentsearch search "ソフトバンク AI 投資"      -e yahoo_japan --limit 5

# エンタメ / 芸能
agentsearch search "紅白歌合戦 出場者"          -e yahoo_japan --limit 5
agentsearch search "ジャニーズ 改名"            -e yahoo_japan --limit 5

# ライフスタイル
agentsearch search "京都 紅葉 おすすめ"          -e yahoo_japan --limit 5
agentsearch search "東京 ラーメン 名店"          -e yahoo_japan --limit 5
```

### MCP から呼ぶ(Cursor / Claude Desktop / Cline / Continue / Kiro)

```jsonc
search(query="生成AI 業界",  engine="yahoo_japan", limit=5, depth=3)
search(query="日銀 利上げ",  engine="yahoo_japan", limit=10)
```

日本語の画像検索は `yahoo_japan_images` で利用できます。

---

## 🌐 日本のユーザーが使う他のエンジン

| 用途 | エンジン |
|---|---|
| グローバル一般検索 | `google` · `duckduckgo` · `bing` |
| 議論 / "みんなの意見" | `reddit` (英語) |
| 動画 / 解説 | `youtube` |
| 論文・学術 | `arxiv` (CS/ML) · `pubmed` (医学) |
| コード / 開発 | `github` · `stackoverflow` |
| EC / ショッピング | `amazon` · `ebay` |
| 開発者ドキュメント | `dev_docs` (Stripe / OpenAI / AWS / 142 種) |
| 広告ライブラリ | `meta_ad_library` · `tiktok_creative_center` · `google_ad_transparency` |
| App Store + Google Play | `search_app` / `lookup_app` |

完全なリスト: `agentsearch list-engines` または [英語 README](README.md) を参照。

---

## ⚙️ インストール

```bash
git clone https://github.com/t0ken-ai/AgentSearch.git
cd AgentSearch
pip install cloakbrowser
pip install -e .
```

Python 3.9+ 必須。初回実行時に CloakBrowser(ステルス Chromium)が自動ダウンロードされます。

---

## 🔌 MCP サーバー設定

`~/.kiro/settings/mcp.json`(または Cursor / Claude Desktop / Cline / Continue の同等ファイル)に追記:

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

公開ツール: `search`, `extract`, `extract_many`, `list_engines`, `list_dev_docs_platforms`, `search_app`, `lookup_app`, `find_competitor_ads`, `download_ad_media`.

---

## 📚 詳細ドキュメント

ここに **載っていない** 内容(エンジン別オプション一覧、広告ライブラリ、競合分析ワークフロー、142 の dev_docs プラットフォーム一覧など)は [**英語 README**](README.md) と [SKILL.md](skills/agent-search/SKILL.md) に集約されています。

---

*Python パッケージ 1 つ · 90+ 検索エンジン · 142 開発者ドキュメント · 5 広告ライブラリ · App Store · 9 MCP ツール — すべてローカル、API キー不要。*
