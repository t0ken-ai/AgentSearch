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

**📣 广告情报 (5) + 应用商店搜索** 🆕
Meta Ad Library · Instagram Ad Library · Google Ads Transparency · TikTok Creative Center · TikTok Ad Library · Apple App Store · Google Play

**📚 开发者文档 (142 个平台)** 🆕
Meta / Facebook · Stripe · OpenAI · Anthropic · AWS · Google Cloud · Azure · GitHub · Kubernetes · Docker · React · Vue · Next.js · Python · Node · Rust · Go · MDN · Apple · Android · Flutter · MongoDB · Redis · Postgres · HuggingFace · Datadog · Sentry · Notion · Slack · Discord · Twilio · Shopify · Vercel · Supabase · …

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

## 🔌 作为 MCP Server 使用（Cursor / Cline / Claude Desktop / Continue / Roo Code）

AgentSearch 自带 **Model Context Protocol** 服务器，任何兼容 MCP 的客户端开箱即得 `search` / `extract` / `list_engines` 三个工具 — 不用写胶水代码、不用 API Key。

### 安装

```bash
pip install -e ".[mcp]"      # 安装 mcp Python SDK
```

### 配置客户端

<details open>
<summary><b>Claude Desktop</b> · <code>~/Library/Application Support/Claude/claude_desktop_config.json</code></summary>

```json
{
  "mcpServers": {
    "agent-search": {
      "command": "/path/to/venv/bin/python",
      "args": ["-m", "agent_search.mcp_server"]
    }
  }
}
```
</details>

<details>
<summary><b>Cursor</b> · 仓库内 <code>.cursor/mcp.json</code>（或全局 <code>~/.cursor/mcp.json</code>）</summary>

```json
{
  "mcpServers": {
    "agent-search": {
      "command": "/path/to/venv/bin/python",
      "args": ["-m", "agent_search.mcp_server"]
    }
  }
}
```
</details>

<details>
<summary><b>Cline / Continue / Roo Code</b> · 在它们各自的 MCP 设置 UI 里</summary>

格式相同 — `command` 指向 venv 里的 Python，`args` 设为 `-m agent_search.mcp_server`。具体配置文件路径各家不同，参见各自文档。
</details>

<details>
<summary><b>OpenClaw</b> · 已通过自带的 <code>agent-search</code> skill 支持</summary>

```bash
cp -r skills/agent-search ~/.openclaw/workspace/skills/
```

OpenClaw 会自动加载这个 skill，agent 在需要新鲜网络数据时会自动调用 AgentSearch。
</details>

### Agent 拿到的工具

| 工具 | 作用 | 何时调用 |
|---|---|---|
| `search(query, engine, limit)` | 跑 80 个搜索引擎之一 | 任何需要新鲜网络结果时 |
| `extract(url, paginate, max_scrolls)` | 抓 URL，返回 Markdown + 元数据 | 在 `search` 返回后想读全文时 |
| `list_engines()` | 列出所有引擎 + 类目 | 不知道该用哪个引擎时 |

服务器会在多次调用之间复用同一个 Chromium，每 25 次调用回收一次（可通过 `AGENTSEARCH_RECYCLE_AFTER` 调整），所以除了第一次调用外，后续调用的开销 <100ms，而不是完整的 ~1.5s 启动。

---

## 🌐 自托管 HTTP API（`agentsearch.serve`）

当 MCP 不可用时 — 云端 worker、Docker 容器、远程脚本、纯 HTTP 框架 — 把 AgentSearch 跑成一个轻量 HTTP 服务。同一套引擎池、同样的反爬能力、简单的 JSON API。

```bash
# 仅监听 localhost（不需要鉴权）：
python -m agent_search.serve --port 8088

# 绑定网络（需要鉴权）：
AGENTSEARCH_TOKEN=mysecret python -m agent_search.serve --host 0.0.0.0 --port 8088
```

接口列表：

| 方法 · 路径 | Body / 参数 | 返回 |
|---|---|---|
| `GET  /health` | — | `{"status": "ok"}` |
| `GET  /list-engines` | — | `{count, engines[]}` |
| `POST /search` | `{query, engine?, limit?, depth?, profile?}` | 结果数组 |
| `POST /search-many` | `{query, engines[], limit?, timeout?}` | 各引擎 + 合并后的结果 |
| `POST /extract` | `{url, paginate?, max_scrolls?, links?, images?, profile?}` | 抽取后的 Markdown |

```bash
# 快速示例
curl localhost:8088/health
curl -X POST -H 'Content-Type: application/json' \
     -d '{"query":"transformer","engine":"arxiv","limit":3}' \
     localhost:8088/search
```

> **为什么是单线程？** CloakBrowser 用的是 Playwright 的 *sync* API，每个浏览器会绑定到启动它的线程上。多线程服务器会跨线程访问 Browser。自托管单用户场景本来就不需要并发。多 agent 并发 → 在不同端口跑多个实例。
>
> **网络安全**：服务器在没有 bearer token 的情况下拒绝绑定 `0.0.0.0`，避免误暴露到公网。

---

## 🧪 质量监控 — 本地 canary

每个适配器都依赖目标站的 live DOM。站点天天在变，所以我们做了 `agentsearch canary` 来自动检测回归。

**在本地跑，不要在 CI 上跑。** GitHub Actions runner 用的是 Azure 数据中心 IP，Reddit / Cloudflare / DataDome 已经预先封锁了这些 IP，CI 上跑 canary 全是噪声不是信号。推荐做法是在你自己的机器上加个每日定时的 launchd / systemd-timer / cron 任务：

```bash
agentsearch canary --gh-issue
```

Canary 做的事：

- 并发跑一个 canary 查询通过每个适配器
- 把每个引擎分类为 **PASS**（≥1 条结果）、**EMPTY**（干净跑完但 0 条 = 大概率 DOM 漂移）或 **FAIL**（异常）
- 写出 `canary_report.json` 供下游工具消费
- 当 `(EMPTY + FAIL) / total > 20%` 时，通过 `gh` CLI 自动开（或追加评论到）一个打了 `canary-regression` 标签的 GitHub issue

```bash
# 全量扫描（约 5 分钟，80 个引擎，并发 4）
agentsearch canary

# 只扫指定子集
agentsearch canary --engines duckduckgo,reddit,arxiv

# 阈值触发时自动开 GitHub issue
agentsearch canary --gh-issue

# 没装 `gh` CLI？生成 markdown 让你手动粘贴：
agentsearch canary --issue-md /tmp/canary-issue.md
```

参见 [`docs/CANARY.md`](docs/CANARY.md)，里面有现成的 `launchd` / `systemd` / `cron` 模板，以及 [`skills/agentsearch-canary/`](skills/agentsearch-canary/) 这个负责每日跑 canary 的 OpenClaw skill。

> 仓库里还留了一个手动触发的 workflow（`.github/workflows/canary-on-demand.yml`）作为"点按钮兜底确认"用 — 它**没有**定时调度，这是有意为之。

---

## 🍳 Cookbook — 常见用法

<details>
<summary><b>🔐 登录一次,后续永久复用 session</b></summary>

Twitter/X、LinkedIn、Glassdoor、Discord、Instagram 这类站点匿名访问基本拿不到任何有用内容。`agentsearch login` 一次，在弹出的 headed CloakBrowser 窗口里登录，之后每次 `search` / `extract` 都会自动带上 session — 而且**不会丢失反爬能力**（CloakBrowser 的 C++ 反检测补丁仍然生效，不像那些走 Chrome-CDP 的工具会暴露真实 Chrome 指纹）。

```bash
# 弹出 headed 窗口手动登录，回到终端按 Enter 保存：
agentsearch login twitter
agentsearch login linkedin
agentsearch login glassdoor

# 后续调用任何命令都带上 --profile：
agentsearch search "from:elonmusk AI" --engine twitter --profile twitter --limit 10
agentsearch extract "https://www.linkedin.com/in/<handle>/" --profile linkedin --json

# 自定义站点？传 --url 覆盖登录入口：
agentsearch login mysite --url https://mysite.com/auth/signin
```

Profile 存在 `~/.cache/agentsearch/profiles/<name>/`（可通过 `AGENTSEARCH_PROFILES_DIR` 修改）。`--profile <name>` 默认使用你执行 `login` 时传的站点名。Profile 完整保存 cookies、localStorage、IndexedDB、service workers — 形态跟一个真实的 Chrome profile 一样。
</details>

<details>
<summary><b>📰 抓 URL 转干净 Markdown（readability + 自动翻页）</b></summary>

```bash
# 抓标题 / 作者 / 日期 / 全文正文 — 自动翻页加载惰性内容、剥掉广告和导航，
# 返回 LLM-friendly 的 Markdown
agentsearch extract \
  "https://news.ycombinator.com/item?id=43936992" --json | jq .

# 返回:
# {
#   "url": "...",
#   "status": "ok",
#   "title": "Updated rate limits for unauthenticated requests",
#   "author": "...",
#   "date": "2025-05-09",
#   "content_markdown": "...",          # 完整楼主帖+所有评论, 约 7900 字
#   "content_text": "...",
#   "word_count": 7936,
#   "extractor": "trafilatura",
#   "scrolls": 1,
#   "load_more_clicks": 0
# }

# 静态页面跳过自动滚动加快速度
agentsearch extract "https://example.com/blog" --json --no-paginate

# 直接打印 Markdown 到 stdout（不要 JSON 包裹）
agentsearch extract "https://example.com/blog" --format markdown
```
</details>

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

<details>
<summary><b>⚡ 多引擎并行扇出 + URL 去重</b></summary>

```bash
# 并发跑 3-5 个引擎,墙上时间 ≈ 最慢的那个引擎,而不是各自相加。
# 多个引擎都返回的 URL 会被识别为「共识结果」浮到合并 feed 顶部。
agentsearch search-many "open source MCP web search" \
    --engines duckduckgo,hackernews,github --limit 5 --merged --json

# 或者直接用预设 bundle（内置快捷指令）：
agentsearch jobs    "data engineer"             # linkedin_jobs+indeed+ziprecruiter+glassdoor
agentsearch travel  "kyoto"                     # booking + expedia
agentsearch news    "fed rate decision"         # reuters+ap+bbc+guardian+npr
agentsearch code    "kubernetes ingress yaml"   # github+stackoverflow+HN
agentsearch research "diffusion transformer"    # ddg+google+reddit+HN
```
</details>

<details>
<summary><b>📰 一次拿 SERP + 正文（--depth N）</b></summary>

```bash
# 顶部 N 条结果直接附带 body_markdown / body_word_count 字段 —
# agent 不用再发起后续 extract 调用
agentsearch search "Brave Search API forbids AI" \
    --engine hackernews --limit 5 --depth 3 --json
```
</details>

<details>
<summary><b>🚦 健康感知兜底（--fallback）</b></summary>

```bash
# 试主选引擎；返回空 / 报错时,沿着按近期成功率排序的兜底链一路降级。
# 引擎健康度记录在 ~/.cache/agentsearch/health.json,跨调用累积。
agentsearch search "X" --engine google --fallback --json

# 自定义兜底链：
agentsearch search "X" --engine google \
    --fallback --fallback-chain duckduckgo,bing,startpage --json

# 查看本地健康度表（按综合分数排序）：
agentsearch status
```
</details>

<details>
<summary><b>💼 跨 LinkedIn / Indeed / ZipRecruiter / Glassdoor 比对岗位</b></summary>

```bash
agentsearch jobs "site reliability engineer in Berlin" --limit 5 --json | jq .
# 合并 feed 顶部 = 共识岗位（多个招聘板都收录的同一 URL）
```
</details>

<details>
<summary><b>🗺️ Google Maps 本地商家搜索</b></summary>

```bash
agentsearch search "ramen tokyo" --engine google_maps --limit 5 --json
# 返回 name / url / rating / review_count / address / category / phone / website
```
</details>

<details>
<summary><b>📈 Yahoo Finance 股票快速查询</b></summary>

```bash
agentsearch search "apple" --engine yahoo_finance --limit 3 --json
# 返回 symbol / name / last_price / exchange / asset_type
```
</details>

<details>
<summary><b>🌐 走 HTTP / SOCKS 代理（被限流时轮换出口 IP）</b></summary>

家里 IP 被 Instagram / YouTube / Reddit 限流时，切代理。支持
HTTP / HTTPS / SOCKS4 / SOCKS5（含账密），有轮换策略和本地缓存。

```bash
# 1. 从 GitHub 拉免费列表（proxifly / roosterkid / TheSpeedX / Zaeem20 四家）
agentsearch proxies fetch --sources socks5 --limit 200       # 整个 socks5 套餐
agentsearch proxies fetch --sources proxifly_http --limit 100  # 单个 source

# 2. 跑健康检查（HTTP/HTTPS 走 urllib，SOCKS 由浏览器使用时验证）
agentsearch proxies test --workers 50 --timeout 8 --max-test 200

# 3. 看池子状态
agentsearch proxies list --limit 30

# 4. 直接用一个静态代理
agentsearch search "限流的 query" --engine google \
  --proxy http://user:pass@1.2.3.4:8080

# 5. 从池子轮换（每次调用按池策略选一条）
agentsearch search "..." --engine instagram --proxy pool          # 任意 scheme
agentsearch search "..." --engine youtube  --proxy pool:socks5    # 仅 SOCKS5
agentsearch search "..." --engine reddit   --proxy pool:/path/list.json
agentsearch search "..." --engine google   --proxy file:/path/proxies.txt

# 6. 手动加付费住宅代理（生产推荐）
agentsearch proxies add http://user:pw@proxy.webshare.io:80 \
                       socks5://user:pw@gate.bright.com:33335
```

> **提醒：** GitHub 上的免费代理命中率非常低（绝大多数列出的 IP 几分钟内就死了）。
> 严肃用途请买 Webshare / Bright Data / Oxylabs / IPRoyal 的住宅代理 ——
> 同样的 `--proxy` / `proxies add` 接口，存活率高得多。

</details>

<details>
<summary><b>📣 广告情报 — Meta / Instagram / Google / TikTok 跨平台竞品创意分析,以及应用商店搜索 + 联系方式收集</b></summary>

五个广告库 engine + App Store 搜索把 AgentSearch 变成自托管的
BigSpy / 广大大 / SocialPeta / data.ai —— 同样的数据,免 ¥99-499/月
订阅,结果直接是标准 JSON 给 agent 消费。

#### 单引擎广告搜索

```bash
# 1. Meta Ad Library — FB + IG 关键词 / 广告主 / 主页 URL 搜索
agentsearch search "shopify" --engine meta_ads --limit 20 --json
# 每条返回: ad_archive_id, page_name, days_running, image_urls[],
#            video_urls[], body_text, cta_text, link_url,
#            collation_id, currency, reach, funding_entity,
#            disclaimer, page_like_count, age_gender_distribution,
#            region_distribution, ... (60+ 字段)

# 2. Instagram-only — 同一后端,锁 publisher_platforms=instagram
agentsearch search "sephora" --engine ig_ads --limit 5 --json

# 3. TikTok Creative Center — 19 个 mode (Top Ads / Keyword Insights /
#    Top Products / Trending Hashtags / Hashtag Analytics / Trending
#    Songs / Song Analytics / Trending Creators / Trending Videos / ...)
agentsearch search "" --engine tt_ads --limit 10 --json
# Filter: --period 7|30|180  --country_code US|GB|JP  --order_by ctr|like|cvr
#         --industry <id>   --objective <id>   --keyword <kw>

# 4. Google Ads Transparency — search/domain/advertiser_ads/creative_detail
agentsearch search "nike.com" --engine g_ads --mode domain --json
# → 直接定位到 advertiser_id,然后列出广告:
agentsearch search "AR01266454498310619137" --engine g_ads \
    --mode advertiser_ads --region anywhere --limit 20

# 5. TikTok Ad Library — 仅 EU/UK(DSA 监管)
agentsearch search "burger king" --engine tiktok_ads --region GB --limit 10
```

#### 跨平台顶层命令

```bash
# 6. `ads` — 一个 query 打四个广告库
agentsearch ads "summer skincare" --limit 10
#   meta + instagram + tiktok + google 并行,合并按时间排序,
#   归一化成统一的 AdRecord 字段。

# 6a. 边查边过滤 — 只要视频广告 + impression 上限
agentsearch ads "summer skincare" \
    -f has_video=true \
    -f min_impressions=10000 \
    -f country=US \
    -f last_seen_after=2026-04-01

# 7. `ads-by-app` — App Store URL → 该 app 在每个平台投的广告
agentsearch ads-by-app "https://apps.apple.com/us/app/instagram/id389801252" \
    --platform all --precise --limit 20
#   → 解析 app 拿到 developer name + 官网域名
#   → Google ATC: `mode=domain` (域名精确匹配)
#   → Meta/IG:    `--precise` 把 dev name 解析到 Facebook canonical
#                 page_id,然后 advertiser-mode 查询(0 关键词噪音)
#   → TikTok CC:  Top Ads 按 dev name 过滤

# 8. `ads-batch` — 周度竞品 sweep,批量跑一组 app
cat > competitors.txt <<EOF
com.shopify.mobile
https://apps.apple.com/us/app/instagram/id389801252
com.spotify.music
EOF

agentsearch ads-batch competitors.txt -o ./weekly-sweep \
    --platform all --precise --limit 20 \
    --workers 4 --proxy-pool ./fluxisp-pool.txt
#   → 4 个 app 通过 4 个不同住宅 IP 真并发(fluxisp-pool.txt 一行一个
#     代理 URL;workers/pool 不匹配会有警告)
#   → 输出: ./weekly-sweep/<bundle_slug>.json 每个 app 一份
#     + index.json 总览(per-platform 广告数、app 元数据、评分、
#     版本、上次更新日期)

# 9. `ads-download` — 把 JSON 中的所有图片/视频 URL 拉到本地
cat weekly-sweep/com_shopify_mobile.json | jq -c '.results[]' \
    | agentsearch ads-download - -o ./swipe --max-per-record 1
#   → 每个广告挑最高分辨率视频/图,按
#     {platform}_{ad_id}_{idx}_{kind}.{ext} 命名,
#     走相同代理出口
```

#### 应用商店关键词搜索 + 开发者联系方式收集

```bash
# 10. `app-search` — 跨 Apple App Store / Google Play 关键词搜索
agentsearch app-search "shopify" --store all -n 6
# →
#   [apple ] Shopify: Sell online/in person   by Shopify Inc.   id=371294472
#            web=http://www.shopify.com/mobile
#   [google] Shopify: Sell online/in person   by Shopify        id=com.shopify.mobile
#            web=http://www.shopify.com/mobile
#            email=support@shopify.com
#            privacy=http://www.shopify.com/legal/privacy

# 每个结果含 25 个字段:support_email、privacy_url、website、
# terms_url、version、last_updated、rating、rating_count、
# size_bytes、supported_devices、languages、genres、
# screenshot_urls 等。

# 10a. 只要有 contact 信息的 app(BD 拓客 / 合规审计)
agentsearch app-search "fitness tracker" --store all -n 50 \
    --with-contact --json > apps.json
```

#### 端到端 pipeline(关键词 → 找竞品 → 抓广告 → 下素材)

```bash
# 一条 shell pipeline:跨两商店搜 "fitness" → 扇出 4 个广告库 →
# 把所有创意字节拉本地。

agentsearch app-search "fitness" --store all -n 20 --json \
    | jq -r '.results[].bundle_id' \
    > /tmp/bundles.txt

agentsearch ads-batch /tmp/bundles.txt -o ./fitness-intel \
    --workers 4 --proxy-pool ./fluxisp-pool.txt --precise

for f in fitness-intel/*.json; do
  cat "$f" | jq -c '.results[]' \
    | agentsearch ads-download - -o "./swipe/$(basename $f .json)" \
        --max-per-record 1 --quiet
done
```

#### 每条广告能拿到什么

| 字段 | Meta / Instagram | TikTok CC | Google ATC |
|---|---|---|---|
| `ad_id` / `creative_id` | ✅ | ✅ | ✅ |
| `advertiser_name` / `page_name` | ✅ | ✅ | ✅ (via domain) |
| `first_seen` / `last_seen` / `days_running` | ✅ (政治/EU) | ⚠️ via period | ✅ |
| `image_urls[]` / `video_urls[]` | ✅ (含轮播) | ✅ (5 种码率) | ✅ (text/image/video) |
| 性能(CTR / likes / CVR / 6 秒完播率) | ⚠️ 政治才有 | ✅ | ❌ |
| Headline / description / 落地 URL | ✅ | ✅ | ✅ (text-ad protobuf 解码) |
| 人群分布 + 地域分布 | ✅ 仅政治 | ❌ | ❌ |
| 资助方 / 免责声明(政治) | ✅ | ❌ | ❌ |

> **为什么这能跑通** —— 每个平台现在都建了 transparency 入口
> (EU DSA + 自我合规),公开了创意 + 投放时间。**他们不公开的是
> 真实消耗 / CTR / ROAS**,所以广大大、BigSpy 卖的"消耗排行"全是
> 估算(impressions × 行业 CPM)。一个广告挂超过 **60 天的几乎一定
> 盈利** —— 这是唯一真实可见的 performance signal,比任何 spy
> tool 的估算都准。
>
> 部分 TikTok CC mode (`keyword_insights` / `creative_insights` /
> `top_products` / `trending_*` / `hashtag_analytics` /
> `song_analytics`) 需要 `ads.tiktok.com` 免费登录 — 跑一次
> `agentsearch login tiktok_business` 后 cookies 持久化即可。
> 另外 6 个 mode (`top_ads` / `top_ads_spotlight` / `ad_*`)
> 无需登录。
>
> Google ATC 在住宅代理下:cloakbrowser+stealth 路径如果撞上
> Google 的 bot 挑战,引擎会自动 fallback 到 raw HTTP transport
> (`GoogleAdTransparencyEngine.raw()`)保证全覆盖。

</details>

<details>
<summary><b>📚 开发者文档搜索 — 142 个平台 (Stripe / OpenAI / Anthropic / AWS / TikTok / WhatsApp / Telegram / Meta / …)</b></summary>

直接搜开发者文档站点很痛苦 —— developers.facebook.com、
platform.openai.com、docs.anthropic.com 等都是重型 React SPA,
内置 search 返回的是空壳(SSR HTML 0 results),且 cloakbrowser+
stealth+住宅代理在多数文档站会撞 navigation 反爬。

务实方案:用 DuckDuckGo 站内搜(`site:<host>` 前缀),然后用
`extract_page()` 把每个命中页抓成干净 Markdown。

#### `facebook_docs` —— Meta 开发者文档

```bash
# 通用搜索
agentsearch search "ad creation" -e fb_docs --json

# 限定子产品(16 个:marketing-api / graph-api / whatsapp-business /
# instagram-graph / messenger / threads / audience-network /
# app-events / ad-library / login / webhooks / business-sdk / ...)
agentsearch search "create campaign" -e fb_docs --product marketing-api

# 只看 Reference 文档(避免 tutorials 干扰)
agentsearch search "adcreative" -e fb_docs --mode reference --product marketing-api

# 锁特定 API 版本
agentsearch search "ad insights" -e fb_docs --api-version v25.0

# 一次拿搜索 + 文档 markdown 全文
agentsearch search "ad creation" -e fb_docs --depth 3 --json
# → 5 命中,前 3 个含 body_markdown(curl 示例 + 参数表 + ISO 更新日期)

# 别名: fb_docs / meta_docs / fb_dev / facebook_docs
```

每条 result 自动 tag:`doc_section` (reference / changelog /
quickstart / tutorial / use_case / webhook / guide) /
`product` (从 URL 推断) / `api_version` (`v21.0` / `v25.0`)。

#### `dev_docs` —— 通用开发者文档搜索(142 平台)

```bash
# 按预设 platform
agentsearch search "subscription webhook" -e docs --platform stripe
agentsearch search "embeddings" -e docs --platform openai
agentsearch search "tool use streaming" -e docs --platform anthropic
agentsearch search "s3 presigned url" -e docs --platform aws --product s3
agentsearch search "useEffect cleanup" -e docs --platform react

# 任意 host(预设没覆盖的)
agentsearch search "rate limit" -e docs --site api.notion.com

# 多 host preset 自动 OR-combine
agentsearch search "messages" -e docs --platform anthropic
# → site:docs.anthropic.com OR site:docs.claude.com

# 模式: search / reference / changelog / api / tutorial / examples
agentsearch search "lambda" -e docs --platform aws --mode changelog

# 一次拿搜索 + Markdown
agentsearch search "embeddings" -e docs --platform openai --depth 3 --json
```

#### 预设平台清单 (142)

| 类目 | 别名 |
|---|---|
| **云 / 基础设施** | google-cloud / gcp · aws · azure / microsoft · docker · kubernetes / k8s · hashicorp / terraform · github · gitlab · cloudflare · vercel · netlify · fly · render |
| **API / SaaS** | stripe · twilio · slack · discord · shopify · supabase · firebase · mongodb · redis · postgres / postgresql · mysql · elasticsearch |
| **社交平台** | tiktok / tiktok-business / tiktok-marketing / tiktok-login · snap / snapchat / snap-marketing · twitter / x · pinterest · reddit · linkedin · youtube |
| **即时通讯 / IM** | whatsapp / whatsapp-business / whatsapp-cloud · telegram / telegram-bot · messenger · line · viber · wechat / wechat-pay · kakao · instagram · threads |
| **Meta 系产品** | meta · facebook · instagram · messenger · threads · whatsapp · *(另见 fb_docs 引擎,16 个子产品精细过滤)* |
| **Google 系产品** | google-cloud / gcp · firebase · google-ads · google-analytics · google-maps · google-pay · youtube · google-ai / gemini |
| **移动分析 / 归因 / 广告情报** 🆕 | data.ai / appannie · sensortower · appsflyer · appsflyer-performance-index · appsflyer-benchmarks · adjust · branch · applovin / applovin-max · bigspy · similarweb · admiral / getadmiral · businessofapps · qimai / 七麦 · diandian / 点点数据 |
| **AI / ML** | openai · anthropic / claude · huggingface / hf · cohere · pinecone · google-ai / gemini · langchain · llamaindex |
| **前端 / 语言** | mdn / mozilla · react · vue · angular · svelte · nextjs / next · remix · nuxt · nodejs / node · deno · bun · python · typescript · rust · go / golang |
| **移动端** | android · apple / ios · swift · flutter · react-native · expo |
| **浏览器** | chrome · webkit |
| **DevOps / 可观测性** | datadog · grafana · prometheus · sentry · opentelemetry |
| **身份认证** | auth0 · okta · clerk |
| **协作平台** | notion · airtable · linear |
| **ML 训练基建** | wandb · mlflow · ray |

> **已知限制** —— DDG 偶尔会对 `site:` 查询从住宅代理限速。引擎
> 通过 `last_status` 暴露调试信息;真发生时,把 `dev_docs.py` 里
> DDG 换成 Brave/Searx 是 1 行改动。每个 preset 只是一个 `host`
> 列表 —— 加新厂商只要 1 行 PR。

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
