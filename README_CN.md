# 🔍 AgentSearch

**AI Agent 的搜索引擎。免费、本地、隐私。**

一个为 AI Agent 设计的隐身网页搜索工具包，绕过 60+ 个网站的反爬检测——完全在你的机器上运行，零 API Key、零云依赖、零数据泄露。

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![No API Key Required](https://img.shields.io/badge/No%20API%20Key-Required-green.svg)]()
[![Local Only](https://img.shields.io/badge/Data-Never%20Leaves%20Your%20Machine-orange.svg)]()

[English](README.md) | **[中文](README_CN.md)**

---

## 为什么做这个

大多数"免费"搜索工具并不真正免费：

- **API 服务**（Tavily、Serper、Firecrawl）把你的查询发到他们的服务器。免费额度用完就得付钱。你的数据就是他们的数据。
- **浏览器自动化**（Selenium、Puppeteer）会被 Google、Cloudflare 和现代反爬系统立刻封杀。
- **元搜索引擎**（SearXNG）很好，但需要自己搭建服务器。

**AgentSearch** 不一样：

- 🔒 **100% 本地运行** — 所有搜索都在你的机器上执行。数据永远不离开你的网络。
- 🆓 **100% 免费** — 没有 API Key、没有速率限制、没有订阅。永远免费。
- 🕵️ **内置反检测** — 基于 [CloakBrowser](https://github.com/CloakHQ/CloakBrowser)，一个在 C++ 源码层面修改了 49 个指纹特征的 Chromium 分支。
- 🤖 **OpenClaw 原生** — 作为 skill 安装，立刻可用。一条命令搜索任何网站。

---

## 特点

- **60+ 网站适配器** — Google、Bing、DuckDuckGo、YouTube、Reddit、GitHub、StackOverflow、arXiv、HuggingFace、IMDB、Goodreads、Amazon、eBay、Pinterest、SoundCloud、Apple Podcasts、B站、知乎、小红书、抖音 等等
- **隐身无头浏览器** — CloakBrowser 在 C++ 源码层面修改 Chromium。不是 JS 注入。不是配置补丁。反爬系统看到的是真实浏览器，因为它*就是*真实浏览器。
- **零配置** — 无需 API Key、无需注册、无需账号。安装即用。
- **隐私优先** — 所有处理在本地完成。你的搜索查询永远不会触碰任何云服务。
- **CLI + Python API** — 命令行使用或作为库导入
- **自动重试与降级** — 如果一个引擎被封，自动重试或切换到备选方案
- **可扩展** — 用一个简单的 Python 类就能添加新的网站适配器

---

## 支持的网站

61 个适配器，分布在 15 个类目。每个网站都有独立的引擎模块（`cloak_stealth_suite/engines/`）和可独立运行的测试（`tests/test_<name>.py`）。

**搜索引擎 (11)：** Google、Bing、DuckDuckGo、Yandex、Brave、百度、搜狗、360 搜索、Startpage、Ecosia、Qwant

**代码 & 开发 (5)：** GitHub、StackOverflow、Hacker News、NPM、dev.to

**AI & 研究 (2)：** HuggingFace、arXiv

**知识库 (4)：** Wikipedia、Wikivoyage、PubMed、Wolfram Alpha

**社交 & 论坛 (6)：** Reddit、Reddit Subreddit (JSON API)、Twitter/X、Quora、BlackHatWorld、Instagram

**中文平台 (6)：** 知乎、微博、小红书、抖音、今日头条、B站

**视频 & 直播 (4)：** YouTube、Twitch、Netflix、TikTok

**音乐 / 音频 / 播客 (4)：** Spotify、SoundCloud、Apple Podcasts、小宇宙 FM

**电影 & 图书 (2)：** IMDB、Goodreads

**资讯 & 内容 (2)：** Medium、Product Hunt

**电商 & 购物 (4)：** Amazon、eBay、Icecat、Steam

**招聘 & 本地 (3)：** LinkedIn Jobs、Indeed、Yelp

**专利 & 安全 (2)：** Google Patents、VirusTotal

**档案 & 文件 (2)：** Internet Archive、1337x

**图片 (4)：** Unsplash、Pixabay、Pexels、Pinterest

*持续增加中 — 新的适配器不断添加。*

---

## 快速开始

### 前提条件

- Python 3.9+
- [OpenClaw](https://github.com/openclaw/openclaw)（可选，用于 skill 集成）

### 安装

```bash
# 1. 安装 CloakBrowser
pip install cloakbrowser

# 2. 克隆仓库
git clone https://github.com/t0ken-ai/AgentSearch.git
cd AgentSearch

# 3. 安装为可编辑包
pip install -e .
```

### 使用

**命令行：**
```bash
# 搜索 Google
python -m cloak_stealth_suite.cli search "最新AI新闻" --engine google

# 搜索 DuckDuckGo
python -m cloak_stealth_suite.cli search "Rust 教程" --engine duckduckgo

# 搜索 GitHub 仓库
python -m cloak_stealth_suite.cli search "headless browser" --engine github

# 列出所有可用引擎
python -m cloak_stealth_suite.cli list-engines

# JSON 格式输出
python -m cloak_stealth_suite.cli search "Python async" --engine stackoverflow --json
```

**Python API：**
```python
from cloak_stealth_suite.core import launch, BrowserConfig, new_page
from cloak_stealth_suite.engines.google import GoogleEngine

browser = launch(BrowserConfig(headless=True, humanize=True))
page = new_page(browser)
engine = GoogleEngine(page)
results = engine.search("开源AI模型", limit=5)

for r in results:
    print(f"[{r.title}]({r.url})")
    print(f"  {r.snippet[:120]}")

browser.close()
```

**OpenClaw Skill：**

将 `skills/agent-search/` 文件夹复制到你的 OpenClaw skills 目录：

```bash
cp -r skills/agent-search ~/.openclaw/workspace/skills/
```

然后你的 OpenClaw agent 就可以原生搜索网页了。

---

## 隐私 & 安全

### 本地运行的内容
- ✅ 浏览器实例（CloakBrowser/Chromium）
- ✅ 搜索查询
- ✅ 结果解析
- ✅ 所有数据处理

### 会发送到互联网的内容
- 🔍 仅发送到目标网站的 HTTP 请求（例如 `google.com/search?q=...`）
- 仅此而已。没有中间件、没有代理、没有分析、没有遥测。

### 永远不会发生的事
- ❌ 你的查询永远不会发送到任何第三方 API
- ❌ 没有使用跟踪或分析
- ❌ 没有搜索历史的云存储
- ❌ 没有存储或传输的 API Key
- ❌ 没有任何形式的数据收集

**你的搜索只属于你。没有例外。**

---

## 架构

```
AgentSearch/
├── cloak_stealth_suite/
│   ├── core.py              # 浏览器启动和配置
│   ├── cli.py               # 命令行接口
│   ├── engines/             # 网站适配器（每个网站一个文件）
│   │   ├── base.py          # BaseEngine + SearchResult
│   │   ├── google.py
│   │   ├── duckduckgo.py
│   │   └── ...              # 60+ 个适配器
│   ├── stealth/
│   │   └── enhance.py       # 反检测 JS 注入
│   └── tests/               # 测试套件
├── skills/
│   └── agent-search/
│       └── SKILL.md          # OpenClaw skill 定义
├── LEGAL.md
├── LICENSE
├── README.md                 # 英文文档
├── README_CN.md              # 中文文档
└── pyproject.toml
```

每个网站适配器是一个继承自 `BaseEngine` 的 Python 文件。添加新网站只需：

```python
from .base import BaseEngine, SearchResult

class MySiteEngine(BaseEngine):
    name = "mysite"

    def _do_search(self, query: str, limit: int) -> list[SearchResult]:
        # 导航到搜索页
        self.page.goto(f"https://mysite.com/search?q={query}")
        # 解析结果
        # 返回 SearchResult 列表
        ...
```

---

## 对比

| | AgentSearch | SearXNG | Tavily/Serper | free-search |
|--|------------|---------|---------------|-------------|
| **费用** | 永久免费 | 免费（需自建） | 免费额度，之后付费 | 免费 |
| **隐私** | 100% 本地 | 取决于实例 | 查询发送到云端 | 查询发送到云端 |
| **API Key** | 不需要 | 不需要 | 必须 | 不需要 |
| **反检测** | C++ 层面补丁 | UA 伪装 | 不适用（API 访问） | 基础 Puppeteer |
| **安装** | pip install | Docker + 配置 | 注册 + API key | npm install |
| **网站数** | 60+ 适配器 | 聚合现有搜索引擎 | 仅 Google | 15 个引擎 |
| **JS 渲染** | ✅ 完整浏览器 | ❌ 仅 HTTP | ❌ 仅 API | ✅ Puppeteer |
| **需登录网站** | ✅ Cookie 导入 | ❌ | ❌ | ❌ |

---

## 致谢

本项目基于 [**CloakBrowser**](https://github.com/CloakHQ/CloakBrowser) 构建——一个开源的隐身 Chromium，在 C++ 源码层面修改指纹信号。没有 CloakBrowser 在浏览器层面反检测的出色工作，这个项目不可能实现。

> CloakBrowser — 通过所有反爬检测的隐身 Chromium。不是配置补丁。不是 JS 注入。是一个真正在 C++ 源码层面修改了指纹的 Chromium 二进制文件。
> — [github.com/CloakHQ/CloakBrowser](https://github.com/CloakHQ/CloakBrowser)（MIT 协议）

---

## 免责声明

本项目仅供**娱乐和学习研究目的**。作者和贡献者不对本软件的任何滥用行为承担责任。

- ❌ 禁止将本工具用于违反您所在地区法律法规的活动。
- ❌ 禁止将本工具用于爬取网站服务条款中明确禁止自动化访问的网站。
- ❌ 禁止将本工具用于未经授权的数据采集、侵犯隐私或任何违法活动。
- ✅ 请遵守 robots.txt、速率限制和网站服务条款。
- ✅ 请将本工具用于合法的研究、学习和个人使用。

使用本软件即表示您同意对自己的行为承担全部责任。详见 [LEGAL.md](LEGAL.md)。

---

## 许可证

MIT License — 随便用。被拦了别找我们（一般不会）。

---

## 贡献

欢迎贡献！特别是：

- 新的网站适配器（参考 `engines/` 目录中的示例）
- 现有适配器的 Bug 修复
- 改进的反检测技术
- 文档和翻译

PR 会及时审核。
