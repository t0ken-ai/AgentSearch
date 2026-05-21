<div align="center">

<img src="https://img.shields.io/badge/-🔍_AgentSearch-1f2937?style=for-the-badge" alt="AgentSearch" height="48"/>

### 给 AI Agent 用的搜索引擎

# **免费 · 本地 · 隐私 · 绕过 Cloudflare**

**一个 Python 包。80 个网站。零 API Key。零数据外泄。**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![Sites: 80](https://img.shields.io/badge/网站数-80-success.svg)]()
[![No API Key](https://img.shields.io/badge/无需-API%20Key-success.svg)]()
[![Local Only](https://img.shields.io/badge/数据-永远在本机-orange.svg)]()
[![Bypasses Cloudflare](https://img.shields.io/badge/绕过-Cloudflare%20%2F%20PerimeterX%20%2F%20Akamai-red.svg)]()

[![GitHub Stars](https://img.shields.io/github/stars/t0ken-ai/AgentSearch?style=social)](https://github.com/t0ken-ai/AgentSearch/stargazers)
[![GitHub Forks](https://img.shields.io/github/forks/t0ken-ai/AgentSearch?style=social)](https://github.com/t0ken-ai/AgentSearch/network/members)

[English](README.md) · **[中文](README_CN.md)**

</div>

---

## ⚡ 一分钟上手

```bash
pip install cloakbrowser && pip install -e .

# 任意 80 个站点搜索
agentsearch search "人工智能新闻"     --engine google --json
agentsearch search "Python 教程"      --engine bilibili --limit 10
agentsearch search "推荐笔记本 2025"  --engine reddit
agentsearch search "transformer 注意力" --engine arxiv

# 一次拿 SERP + 顶部 N 条正文
agentsearch search "MCP 网页搜索" --engine hackernews --limit 5 --depth 3 --json

# 多引擎并行扇出 + URL 去重合并
agentsearch search-many "AgentSearch" --engines google,reddit,hackernews,arxiv --merged

# 预设 bundle:一键多站融合
agentsearch jobs    "数据工程师"          # linkedin_jobs+indeed+ziprecruiter+glassdoor
agentsearch travel  "京都"                # booking + expedia
agentsearch news    "美联储利率决议"      # reuters+ap+bbc+guardian+npr
agentsearch code    "kubernetes ingress"  # github+stackoverflow+HN
agentsearch research "扩散 transformer"   # ddg+google+reddit+HN

# 登录态站点:登录一次,后续永久复用
agentsearch login twitter
agentsearch search "from:openai" --engine twitter --profile twitter --limit 20

# 抓 URL 转 Markdown(readability + 自动滚动)
agentsearch extract "https://news.ycombinator.com/item?id=43936992" --json

# 跑 MCP server 给 Cursor / Cline / Claude Desktop / OpenClaw / Continue 用
python -m agent_search.mcp_server

# 或者跑成自托管 HTTP API,云端 / Docker 里的 agent 也能用
python -m agent_search.serve --port 8088
```

80 个站点 · CLI · MCP server · HTTP API · 完全跑在你的机器上。**绕过 Cloudflare、PerimeterX、Akamai、DataDome 以及所有已知的指纹检测系统。**

---

## 🛡️ CloakBrowser 优势 — 为什么我们能跑通别人跑不通的

> 原生 Selenium / Puppeteer / Playwright 在 Cloudflare 保护的网站、亚马逊、Google CAPTCHA、Reddit、Twitter 等几乎所有"重要"网站上**会被秒封**。AgentSearch 不会，因为它建立在 **[CloakBrowser](https://github.com/CloakHQ/CloakBrowser)** 之上 —— 一个**在 C++ 源码层面打了 49 个指纹补丁**的开源 Chromium 分支。
>
> **不是 JS 注入，不是配置篡改，不是 UA 伪装。** 它是一个真正的 Chromium 二进制文件，反爬系统**无法**区分 AgentSearch 和真实人类浏览器，*因为根本就没有区别*。

### 透明绕过的反爬层

| 层级 | 系统 | 状态 |
|:------|:-------|:------:|
| 🛡️ WAF / CDN | **Cloudflare Turnstile / Bot Fight Mode** | ✅ |
| 🛡️ WAF / CDN | **PerimeterX / HUMAN Security** | ✅ |
| 🛡️ WAF / CDN | **Akamai Bot Manager** | ✅ |
| 🛡️ WAF / CDN | **DataDome** | ✅ |
| 🛡️ WAF / CDN | **Imperva / Incapsula** | ✅ |
| 🔬 指纹检测 | **bot.sannysoft.com** | ✅ 全部通过 |
| 🔬 指纹检测 | **CreepJS** (abrahamjuliot) | ✅ |
| 🔬 指纹检测 | **PixelScan** | ✅ |
| 🔬 指纹检测 | **BrowserLeaks** | ✅ |
| 🔬 指纹检测 | **fingerprint.com** | ✅ |
| 🤖 reCAPTCHA v3 | 评分 | **≥ 0.7** |

> **为什么这对 Agent 重要：** AI Agent 用一个会被 Cloudflare 30% 概率拦截的"免费搜索工具"是不可用的。AgentSearch 在 Cloudflare 保护的站点上**连续跑几百次查询都不会被封**。

---

## 💡 为什么用 AgentSearch（对比其他方案）

|                                          | AgentSearch | API 服务<br>(Tavily / Serper / Firecrawl) | 原生浏览器自动化<br>(Selenium / Puppeteer) | SearXNG |
|:-----------------------------------------|:-----------:|:---------------------------------------:|:-----------------------------------------:|:-------:|
| 💰 费用                                   | **永久免费** | 有免费额度，超出付费 | 免费 | 免费 |
| 🔑 是否需要 API Key                        | **不需要** | 必须 | 不需要 | 不需要 |
| 🌐 数据是否离开本机                       | **永远不会** | 每次请求都发到云 | 不会 | 取决于实例 |
| 🛡️ **能绕过 Cloudflare**                  | ✅ **C++ 级补丁** | 不适用（API 调用） | ❌ 秒被识别 | ❌ 仅 HTTP |
| 🔬 **能通过指纹检测**                       | ✅ 全部主流 | 不适用 | ❌ CreepJS 直接挂 | ❌ |
| 🌍 支持网站数                             | **80 个** | 1 个（Google） | 你自己写 | 聚合大概 10 个 SE |
| 🐍 JavaScript 渲染                        | ✅ 完整 Chromium | ❌ 仅 API | ✅ | ❌ 仅 HTTP |
| 🔐 需登录的网站                           | ✅ Cookie 导入 | ❌ | 有限 | ❌ |
| 🚀 部署                                   | `pip install` | 注册 + 申请 key | 从零写代码 | Docker + 配置 |
| 💾 是否需要自建服务器                       | 不需要 | 不需要 | 不需要 | 必须 |

---

## 🌍 全部 80 个站点 — 按类目

> 每个站点都是一个独立的适配器（`agent_search/engines/<name>.py`），并附带可独立运行的测试（`tests/test_<name>.py`）。

<table>
<tr>
<td valign="top" width="50%">

**🔍 搜索引擎 (11)**
Google · Bing · DuckDuckGo · Yandex · Brave · 百度 · 搜狗 · 360 搜索 · Startpage · Ecosia · Qwant

**💻 代码 & 开发 (5)**
GitHub · StackOverflow · Hacker News · NPM · dev.to

**🤖 AI & 研究 (2)**
HuggingFace · arXiv

**📚 知识库 (4)**
Wikipedia · Wikivoyage · PubMed · Wolfram Alpha

**💬 社交 & 论坛 (6)**
Reddit · Reddit Subreddit (JSON) · Twitter/X · Quora · BlackHatWorld · Instagram

**🇨🇳 中文平台 (6)**
知乎 · 微博 · 小红书 · 抖音 · 今日头条 · B站

**🌍 海外新闻 (10)**
BBC · 卫报 · 路透社 · 美联社 · CNN · NPR · 半岛电视台 · TechCrunch · The Verge · Ars Technica

**🎬 视频 & 直播 (4)**
YouTube · Twitch · Netflix · TikTok

**🎵 音乐 / 音频 / 播客 (4)**
Spotify · SoundCloud · Apple Podcasts · 小宇宙 FM

</td>
<td valign="top" width="50%">

**🎥 电影 & 图书 (2)**
IMDB · Goodreads

**📰 资讯 & 内容 (2)**
Medium · Product Hunt

**🛒 电商 & 购物 (4)**
Amazon · eBay · Icecat · Steam

**💼 招聘 & 本地 (3)**
LinkedIn Jobs · Indeed · Yelp

**📜 专利 & 安全 (2)**
Google Patents · VirusTotal

**📦 档案 & 文件 (2)**
Internet Archive · 1337x

**🖼️ 图片 (4)**
Unsplash · Pixabay · Pexels · Pinterest

</td>
</tr>
</table>

> *持续增加中 — 新的适配器不断添加。*

---

## 🚀 快速开始

### 1. 安装

```bash
pip install cloakbrowser
git clone https://github.com/t0ken-ai/AgentSearch.git
cd AgentSearch
pip install -e .
```

### 2. 命令行使用

```bash
# 通用搜索
agentsearch search "最新 AI 新闻" --engine google

# 在 StackOverflow 查找代码问题
agentsearch search "TypeError pandas groupby" --engine stackoverflow

# 最新论文
agentsearch search "transformer scaling laws" --engine arxiv

# Reddit 讨论
agentsearch search "推荐 linux 笔记本 2025" --engine reddit

# 视频教程
agentsearch search "react hooks" --engine youtube --limit 10

# 电商
agentsearch search "机械键盘" --engine amazon

# 中文平台
agentsearch search "机器学习" --engine zhihu

# JSON 输出，给其他工具消费
agentsearch search "open source" --engine github --json | jq .

# 列出所有可用引擎
agentsearch list-engines
```

### 3. Python 集成

```python
from agent_search.core import launch, BrowserConfig, new_page
from agent_search.engines.google import GoogleEngine

browser = launch(BrowserConfig(headless=True, humanize=True))
try:
    page = new_page(browser)
    results = GoogleEngine(page).search("开源 AI 模型", limit=5)
    for r in results:
        print(f"{r.title}\n  {r.url}\n  {r.snippet[:120]}\n")
finally:
    browser.close()
```

### 4. 作为 OpenClaw skill 安装

```bash
cp -r skills/agent-search ~/.openclaw/workspace/skills/
```

完成后你的 OpenClaw / Codex / Kiro Agent 就**原生知道**怎么搜 80 个站点了 — 不用写胶水代码、不用调 prompt。

---

## 🍳 Cookbook — 常见用法

<details>
<summary><b>📚 多源研究一个主题</b></summary>

```bash
# 多引擎扇出 + 合并
agentsearch search "X" --engine google     --limit 5 --json > /tmp/g.json
agentsearch search "X" --engine reddit     --limit 5 --json > /tmp/r.json
agentsearch search "X" --engine arxiv      --limit 3 --json > /tmp/a.json
agentsearch search "X" --engine hackernews --limit 5 --json > /tmp/h.json
```
</details>

<details>
<summary><b>🛒 电商比价</b></summary>

```bash
agentsearch search "AirPods Pro 2" --engine amazon --json
agentsearch search "AirPods Pro 2" --engine ebay   --json
```
</details>

<details>
<summary><b>🎬 电影 + 书 + 播客一站查全</b></summary>

```bash
agentsearch search "Dune"           --engine imdb           --json
agentsearch search "Dune"           --engine goodreads      --json
agentsearch search "Frank Herbert"  --engine apple_podcasts --json
```
</details>

<details>
<summary><b>🇨🇳 中文平台搜索（自动 Google site: 兜底）</b></summary>

```bash
# 这些适配器内置 Google → Bing → DuckDuckGo 三级 site: 兜底链路 —
# 不需要 cookies、不需要登录，开箱即用
agentsearch search "旅行攻略" --engine xiaohongshu
agentsearch search "美食"     --engine douyin
agentsearch search "科技"     --engine weibo
```
</details>

<details>
<summary><b>🤖 在 HuggingFace 找模型 / 数据集</b></summary>

```bash
agentsearch search "llama" --engine huggingface --json
# 返回 model_id / author / downloads / likes / pipeline_tag / library / tags
```
</details>

---

## 🔒 隐私 & 安全

```
┌─ 完全本地运行 ─────────────────────────────────────────────────┐
│                                                                │
│  ✅ 浏览器实例（CloakBrowser/Chromium 在你自己机器上）            │
│  ✅ 搜索查询                                                    │
│  ✅ 结果解析                                                    │
│  ✅ 所有数据处理                                                 │
│                                                                │
└────────────────────────────────────────────────────────────────┘

┌─ 唯一对外的网络流量 ──────────────────────────────────────────┐
│                                                                │
│  🔍 仅有：直接打到目标网站的请求                                  │
│     （例如 google.com/search?q=...）                            │
│  ❌ 没有中间件                                                   │
│  ❌ 没有代理                                                     │
│  ❌ 没有埋点                                                     │
│  ❌ 没有遥测                                                     │
│                                                                │
└────────────────────────────────────────────────────────────────┘

┌─ 永远不会发生 ────────────────────────────────────────────────┐
│                                                                │
│  ❌ 查询被发送到任何第三方 API                                    │
│  ❌ 使用情况埋点                                                  │
│  ❌ 搜索历史云端存储                                              │
│  ❌ 存储或传输 API Key                                            │
│  ❌ 任何形式的数据采集                                             │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

**你的搜索只属于你。没有例外。**

---

## 🏗️ 架构

```
AgentSearch/
├── agent_search/
│   ├── core.py               ← 浏览器启动 + 配置
│   ├── cli.py                ← 命令行接口
│   ├── engines/              ← 60+ 个适配器（一个站一个文件）
│   │   ├── base.py           ← BaseEngine + SearchResult dataclass
│   │   ├── google.py         ← Google（含 consent / sorry / CAPTCHA 处理）
│   │   ├── youtube.py        ← YouTube（含播放数 / 时长 / 上传日期解析）
│   │   ├── arxiv.py          ← arXiv 通过 Atom API
│   │   ├── huggingface.py    ← HuggingFace Hub 通过 REST API
│   │   ├── douyin.py         ← Walled 站点 → Google/Bing/DDG site: 兜底链
│   │   └── ...               ← 还有 55 个
│   ├── stealth/
│   │   └── enhance.py        ← 在 CloakBrowser 之上的反检测 JS 层
│   └── tests/                ← 每个引擎都有独立可跑的测试
├── skills/agent-search/
│   └── SKILL.md              ← OpenClaw / Codex / Kiro skill metadata
├── README.md  /  README_CN.md
├── LEGAL.md  /  LICENSE
└── pyproject.toml
```

### 添加一个新的网站适配器

```python
from .base import BaseEngine, SearchResult

class MySiteEngine(BaseEngine):
    name = "mysite"

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        self.page.goto(f"https://mysite.com/search?q={query}")
        # 解析 DOM，返回 list[SearchResult]
        ...
```

就这样。`BaseEngine` 已经帮你处理了重试、被封检测和拟人节流。

---

## 🙏 致谢

本项目基于 **[CloakBrowser](https://github.com/CloakHQ/CloakBrowser)** —— 一个在 **C++ 源码层面**修改指纹信号的开源隐身 Chromium（49 个补丁覆盖 V8、Blink 和 content shell）。没有他们在浏览器层面反检测的出色工作，这个项目无法实现。

> *"CloakBrowser —— 通过所有反爬检测的隐身 Chromium。不是配置补丁。不是 JS 注入。是一个在 C++ 源码层面修改了指纹的真实 Chromium 二进制文件。"*
>
> — [github.com/CloakHQ/CloakBrowser](https://github.com/CloakHQ/CloakBrowser) · MIT 协议

---

## ⚖️ 免责声明

本项目仅供**教育研究和个人使用**。作者**不对**本软件的任何滥用行为承担责任。

| | |
|---|---|
| ✅ 应当 | 遵守 `robots.txt`、速率限制和服务条款 |
| ✅ 应当 | 用于合法研究、学习、个人使用 |
| ❌ 禁止 | 违反您所在地区的法律法规 |
| ❌ 禁止 | 抓取明确禁止自动化访问的网站 |
| ❌ 禁止 | 用于未经授权的数据采集或侵犯隐私 |

使用本软件即表示您同意对自己的行为承担全部责任。详见 [LEGAL.md](LEGAL.md)。

---

## 📜 许可证

**MIT** —— 随便用。被拦了别找我们（一般不会）。

---

## 🤝 贡献

欢迎 PR，特别是：

- 🆕 新的网站适配器（参考 `agent_search/engines/` 中的示例）
- 🐛 现有适配器的 Bug 修复
- 🎯 改进的反检测技术
- 🌐 文档与翻译

PR 会及时审核。改动较大的请先开 issue 讨论。

---

<div align="center">

**一个 Skill。整个互联网。本地运行。永久免费。绕过 Cloudflare。**

[![GitHub](https://img.shields.io/badge/GitHub-t0ken--ai%2FAgentSearch-181717?logo=github)](https://github.com/t0ken-ai/AgentSearch)

</div>
