#!/usr/bin/env python3
"""
Kiro AgentSearch - 增量监督控制器 v2
每轮只给一个小任务，避免超时
"""

import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.expanduser("~/.openclaw/workspace/skills/kiro-acp"))
from acp_client import ACPClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(os.path.expanduser("~/projects/AgentSearch/supervisor2.log")),
    ],
)
log = logging.getLogger("supervisor")

PROJECT_DIR = os.path.expanduser("~/projects/AgentSearch")
KIRO_CLI = "/Users/gao/.local/bin/kiro-cli"
CLOAK_SRC = os.path.expanduser("~/projects/cloakbrowser-src")
CLOAK_VENV = os.path.expanduser("~/tools/cloakbrowser/venv")

# 全局源码修改提示 — 追加到每个任务的 prompt
CLOAK_SOURCE_NOTE = """

## 🔧 CloakBrowser 源码修改权限

你可以直接修改 CloakBrowser 源码来解决兼容性问题！

- **源码位置**: ~/projects/cloakbrowser-src/
- **虚拟环境**: source ~/tools/cloakbrowser/venv/bin/activate
- **当前安装方式**: pip install -e (editable mode)

### 如何修改并编译
```bash
# 1. 修改源码
cd ~/projects/cloakbrowser-src
# 编辑 cloakbrowser/ 下的文件

# 2. 以 editable 模式安装（即时生效，无需重编译）
source ~/tools/cloakbrowser/venv/bin/activate
pip install -e ~/projects/cloakbrowser-src

# 3. 如果改了 C++ 补丁或二进制相关：
#    需要重新 build chromium，这比较重，除非必要不做
#    但 Python 层的修改（browser.py, config.py 等）用 pip install -e 即时生效
```

### 常见需要改的地方
- `cloakbrowser/browser.py` — 启动参数、CDP 行为
- `cloakbrowser/config.py` — 配置选项
- `cloakbrowser/download.py` — 二进制下载逻辑
- `cloakbrowser/human/` — 拟人化行为

### 什么时候改源码
- 如果 CloakBrowser API 不支持某个功能（如 proxy auth、特定 stealth 参数）
- 如果某个网站能检测到自动化特征
- 如果需要自定义 browser launch 参数
- 优先在 agent_search 层面解决，如果不行再改 CloakBrowser 源码
"""

# 增量任务列表 — 每轮一个
TASKS = [
    # Round 2: 基于 AnySearch 垂直领域补充网站适配器

    # 1. Wikivoyage (travel)
    {
        "name": "test_wikivoyage",
        "prompt": """在 {dir} 目录，为 Wikivoyage 创建搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/wikivoyage.py：
1. 使用 Wikivoyage 搜索: en.wikivoyage.org/w/index.php?search=xxx 或用 MediaWiki API
2. 解析旅行指南: title, url, snippet
3. 优先用 API (和 wikipedia 类似): en.wikivoyage.org/w/api.php?action=query&list=search&srsearch=xxx&format=json
4. 返回 SearchResult 列表

写测试 tests/test_wikivoyage.py：
- 搜索 "Tokyo"
- 断言结果 > 0
- 打印前5条

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 2. PubMed (academic/health)
    {
        "name": "test_pubmed",
        "prompt": """在 {dir} 目录，为 PubMed 创建搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/pubmed.py：
1. 使用 PubMed 搜索: pubmed.ncbi.nlm.nih.gov/?term=xxx
2. 或用 PubMed API: eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi + efetch
3. 解析论文: title, url (https://pubmed.ncbi.nlm.nih.gov/PMID/), authors, abstract snippet
4. 优先用 NCBI E-utilities API (JSON format)
5. 返回 SearchResult 列表

写测试 tests/test_pubmed.py：
- 搜索 "machine learning healthcare"
- 断言结果 > 0
- 打印前5条（含 PMID 和标题）

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 3. Steam Store (gaming)
    {
        "name": "test_steam",
        "prompt": """在 {dir} 目录，为 Steam Store 创建搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/steam.py：
1. 使用 Steam Store 搜索: store.steampowered.com/search/?term=xxx
2. 解析游戏: name, url, price, rating
3. 处理年龄验证弹窗
4. 返回 SearchResult 列表（带 price 扩展字段）

写测试 tests/test_steam.py：
- 搜索 "Cyberpunk"
- 断言结果 > 0
- 打印前5条（含价格）

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 4. 1337x Torrent (film)
    {
        "name": "test_1337x",
        "prompt": """在 {dir} 目录，为 1337x 创建搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/torrent_1337x.py：
1. 使用 1337x 搜索: 1337x.to/search/xxx/1/
2. 解析种子: name, url, seeders, leechers, size
3. 返回 SearchResult 列表（带 seeders/size 扩展字段）
4. 注意: 可能需要处理 Cloudflare 验证

写测试 tests/test_1337x.py：
- 搜索 "ubuntu"
- 断言结果 > 0
- 打印前5条（含 seeders 和 size）

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 5. Google Patents (ip/patent)
    {
        "name": "test_google_patents",
        "prompt": """在 {dir} 目录，为 Google Patents 创建搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/google_patents.py：
1. 使用 Google Patents: patents.google.com/?q=xxx
2. 解析专利: title, patent_id, url, assignee, abstract snippet, filing_date
3. 处理可能的 consent 弹窗（复用 google.py 的经验）
4. 返回 SearchResult 列表

写测试 tests/test_google_patents.py：
- 搜索 "self driving car"
- 断言结果 > 0
- 打印前5条（含 patent_id 和 assignee）

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 6. LinkedIn Jobs (business/jobs)
    {
        "name": "test_linkedin_jobs",
        "prompt": """在 {dir} 目录，为 LinkedIn Jobs 创建搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/linkedin_jobs.py：
1. 使用 LinkedIn Jobs: linkedin.com/jobs/search/?keywords=xxx
2. 解析职位: title, company, location, url, date_posted
3. LinkedIn 可能需要处理登录弹窗
4. 如果被拦截，尝试用 Google site:linkedin.com/jobs 搜索作为备选
5. 返回 SearchResult 列表

写测试 tests/test_linkedin_jobs.py：
- 搜索 "software engineer"
- 断言结果 > 0
- 打印前5条（含 company 和 location）

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 7. Indeed (business/jobs)
    {
        "name": "test_indeed",
        "prompt": """在 {dir} 目录，为 Indeed 创建搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/indeed.py：
1. 使用 Indeed 搜索: indeed.com/jobs?q=xxx
2. 解析职位: title, company, location, url, salary, snippet
3. Indeed 有反爬但相对温和
4. 返回 SearchResult 列表

写测试 tests/test_indeed.py：
- 搜索 "python developer"
- 断言结果 > 0
- 打印前5条（含 company 和 salary）

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 8. VirusTotal (security)
    {
        "name": "test_virustotal",
        "prompt": """在 {dir} 目录，为 VirusTotal 创建搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/virustotal.py：
1. 使用 VirusTotal: virustotal.com/gui/search/xxx
2. VirusTotal 需要 API key 才能获取结构化数据
3. 但页面搜索结果可以解析: detection ratio, file name, hash, community score
4. 如果页面渲染困难，使用 VirusTotal API (需要免费 API key)
5. 返回 SearchResult 列表

写测试 tests/test_virustotal.py：
- 搜索一个已知测试 hash: "44d88612fea8a8f36de82e1278abb02f" (EICAR test file MD5)
- 检查是否能获取到结果或至少页面加载成功
- 打印检测结果

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 9. Icecat (ecommerce)
    {
        "name": "test_icecat",
        "prompt": """在 {dir} 目录，为 Icecat 产品目录创建搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/icecat.py：
1. 使用 Icecat 搜索: icecat.biz/en/search?q=xxx 或 icecat.nl/search
2. 解析产品: name, brand, category, url, image, specs
3. Icecat 有开放 API (https://api.icecat.biz/) 但需要注册
4. 优先用页面搜索解析
5. 返回 SearchResult 列表

写测试 tests/test_icecat.py：
- 搜索 "iPhone 16"
- 断言结果 > 0
- 打印前5条（含 brand 和 category）

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 10. Amazon (ecommerce)
    {
        "name": "test_amazon",
        "prompt": """在 {dir} 目录，为 Amazon 创建搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/amazon.py：
1. 使用 Amazon 搜索: amazon.com/s?k=xxx
2. 解析产品: name, url, price, rating, reviews_count, image_url
3. 处理 CAPTCHA 验证（Amazon 反爬较强）
4. 处理 cookie consent
5. 如果被拦截，尝试 amazon.com.au 或 amazon.co.uk（反爬较弱）
6. 返回 SearchResult 列表（带 price/rating 扩展字段）

写测试 tests/test_amazon.py：
- 搜索 "mechanical keyboard"
- 断言结果 > 0
- 打印前5条（含 price 和 rating）

运行测试，如果失败修复并重试。Amazon 是高难度目标，可能需要多次尝试。
结果写入 PROGRESS.md。""",
    },
    # 11. Brave Search (搜索引擎)
    {
        "name": "test_brave",
        "prompt": """在 {dir} 目录，为 Brave Search 创建搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/brave.py：
1. 使用 Brave Search: search.brave.com/search?q=xxx
2. 解析搜索结果: title, url, snippet
3. Brave 反爬较弱，适合作为备选搜索引擎
4. 返回 SearchResult 列表

写测试 tests/test_brave.py：
- 搜索 "open source AI models"
- 断言结果 > 0
- 打印前5条

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 12. Wolfram Alpha (知识计算)
    {
        "name": "test_wolfram",
        "prompt": """在 {dir} 目录，为 Wolfram Alpha 创建搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/wolfram.py：
1. 使用 Wolfram Alpha: wolframalpha.com/input?i=xxx
2. 解析计算结果: input_interpretation, result_pods, url
3. Wolfram 页面是 JS 重度渲染，需要等待动态加载
4. 返回 SearchResult 列表

写测试 tests/test_wolfram.py：
- 搜索 "population of China"
- 断言页面加载成功并获取到结果
- 打印解析到的内容

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 13. Internet Archive / Wayback Machine
    {
        "name": "test_archive",
        "prompt": """在 {dir} 目录，为 Internet Archive 创建搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/archive_org.py：
1. 使用 Internet Archive 搜索: archive.org/search?query=xxx
2. 或用 Wayback Machine API: web.archive.org/web/*/example.com
3. 解析结果: title, url, date, media_type, description
4. 返回 SearchResult 列表

写测试 tests/test_archive_org.py：
- 搜索 "NASA Apollo"
- 断言结果 > 0
- 打印前5条

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 14. DevTo / dev.to (技术社区)
    {
        "name": "test_devto",
        "prompt": """在 {dir} 目录，为 dev.to 创建搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/devto.py：
1. 使用 dev.to 搜索: dev.to/search?q=xxx 或 API: dev.to/api/articles?per_page=5&tag=python
2. 解析文章: title, url, author, tags, reactions_count, reading_time
3. dev.to 有公开 API，优先使用
4. 返回 SearchResult 列表

写测试 tests/test_devto.py：
- 搜索 "rust async"
- 断言结果 > 0
- 打印前5条（含 author 和 tags）

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 15. NPM Registry (code/package)
    {
        "name": "test_npm",
        "prompt": """在 {dir} 目录，为 NPM Registry 创建搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/npm_search.py：
1. 使用 NPM 搜索: npmjs.com/search?q=xxx 或 API: registry.npmjs.org/-/v1/search?text=xxx
2. 解析包: name, url, version, description, downloads, license
3. 优先用 NPM API (JSON 响应)
4. 返回 SearchResult 列表

写测试 tests/test_npm_search.py：
- 搜索 "express"
- 断言结果 > 0
- 打印前5条（含 version 和 description）

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # Round 3: camofox-browser 竞品对齐 - 补齐他们有我们没有的

    # 16. YouTube
    {
        "name": "test_youtube",
        "prompt": """在 {dir} 目录，为 YouTube 创建搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/youtube.py：
1. 使用 YouTube 搜索: youtube.com/results?search_query=xxx
2. 解析视频: title, url, channel, views, duration, upload_date
3. YouTube 反爬较强，需要处理 consent 弹窗和 age verification
4. 返回 SearchResult 列表（带 views/duration 扩展字段）

写测试 tests/test_youtube.py：
- 搜索 "Python tutorial"
- 断言结果 > 0
- 打印前5条（含 channel 和 views）

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 17. Yelp
    {
        "name": "test_yelp",
        "prompt": """在 {dir} 目录，为 Yelp 创建搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/yelp.py：
1. 使用 Yelp 搜索: yelp.com/search?find_desc=xxx&find_loc=city
2. 解析商家: name, url, rating, review_count, category, address, price_range
3. 处理位置弹窗（可能要求输入位置）
4. 返回 SearchResult 列表（带 rating/review_count 扩展字段）

写测试 tests/test_yelp.py：
- 搜索 "best pizza" in "New York"
- 断言结果 > 0
- 打印前5条（含 rating 和 price_range）

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 18. Spotify
    {
        "name": "test_spotify",
        "prompt": """在 {dir} 目录，为 Spotify Web 创建搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/spotify.py：
1. 使用 Spotify Web 搜索: open.spotify.com/search/xxx
2. Spotify 页面是 React 渲染，需要等待加载
3. 解析: title, url, artist, album, type (song/album/artist/playlist)
4. 返回 SearchResult 列表

写测试 tests/test_spotify.py：
- 搜索 "Beatles"
- 断言结果 > 0
- 打印前5条（含 artist 和 type）

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 19. TikTok
    {
        "name": "test_tiktok",
        "prompt": """在 {dir} 目录，为 TikTok 创建搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/tiktok.py：
1. 使用 TikTok 搜索: tiktok.com/search?q=xxx
2. TikTok 反爬很强，可能需要登录
3. 如果被拦，备选方案: 用 Google site:tiktok.com 搜索
4. 解析视频: title, url, author, likes
5. 返回 SearchResult 列表

写测试 tests/test_tiktok.py：
- 搜索 "cooking"
- 检查是否有结果（TikTok 可能无法直接搜索）
- 如果直接搜索失败，验证 Google site: 备选方案有效

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 20. Instagram
    {
        "name": "test_instagram",
        "prompt": """在 {dir} 目录，为 Instagram 创建搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/instagram.py：
1. 使用 Instagram 搜索: instagram.com/explore/tags/xxx
2. Instagram 需要登录，直接搜索可能被拦
3. 备选方案: 用 Google site:instagram.com 搜索
4. 解析帖子: caption, url, likes, comments, user
5. 返回 SearchResult 列表

写测试 tests/test_instagram.py：
- 搜索 "travel" tag
- 检查是否有结果
- 如果直接搜索失败，验证备选方案

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 21. Twitch
    {
        "name": "test_twitch",
        "prompt": """在 {dir} 目录，为 Twitch 创建搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/twitch.py：
1. 使用 Twitch 搜索: twitch.tv/search?term=xxx
2. 解析频道/直播: name, url, type (channel/live/video), viewers, game
3. Twitch 有 API (https://dev.twitch.tv/) 但需要 key
4. 优先用页面搜索解析
5. 返回 SearchResult 列表

写测试 tests/test_twitch.py：
- 搜索 "League of Legends"
- 断言结果 > 0
- 打印前5条（含 type 和 viewers）

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 22. Netflix
    {
        "name": "test_netflix",
        "prompt": """在 {dir} 目录，为 Netflix 创建搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/netflix.py：
1. Netflix 搜索需要登录: netflix.com/search?q=xxx
2. 未登录无法搜索，备选方案: 用 Google site:netflix.com 搜索
3. 解析: title, url, type (movie/series), year, rating
4. 返回 SearchResult 列表

写测试 tests/test_netflix.py：
- 搜索 "Stranger Things"
- 验证至少能通过 Google site: 方案返回 Netflix 相关结果

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 23. Reddit Subreddit (JSON API)
    {
        "name": "test_reddit_subreddit",
        "prompt": """在 {dir} 目录，为 Reddit 子版块创建 JSON API 适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/reddit_subreddit.py：
1. 使用 Reddit JSON API: reddit.com/r/{subreddit}.json?limit=25
2. 这是 Reddit 的公开 JSON 接口，不需要 OAuth
3. 解析帖子: title, url, author, score, num_comments, created_utc, selftext
4. 支持排序: hot, new, top, rising
5. 返回 SearchResult 列表（带 score/comments 扩展字段）

写测试 tests/test_reddit_subreddit.py：
- 获取 r/Python 子版块的热门帖子
- 断言结果 > 0
- 打印前5条（含 score 和 num_comments）

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # Round 4: union-search-skill 竞品对齐 - 中文平台 + 搜索引擎补全

    # 24. 百度
    {
        "name": "test_baidu",
        "prompt": """在 {dir} 目录，为百度搜索创建适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/baidu.py：
1. 使用百度搜索: baidu.com/s?wd=xxx
2. 解析结果: title, url, snippet, source
3. 百度有反爬，需要处理验证码弹窗
4. 返回 SearchResult 列表

写测试 tests/test_baidu.py：
- 搜索 "人工智能"
- 断言结果 > 0
- 打印前5条

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 25. 搜狗
    {
        "name": "test_sogou",
        "prompt": """在 {dir} 目录，为搜狗搜索创建适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/sogou.py：
1. 使用搜狗搜索: sogou.com/web?query=xxx
2. 解析结果: title, url, snippet
3. 返回 SearchResult 列表

写测试 tests/test_sogou.py：
- 搜索 "Python 教程"
- 断言结果 > 0
- 打印前5条

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 26. 360搜索
    {
        "name": "test_so360",
        "prompt": """在 {dir} 目录，为 360 搜索创建适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/so360.py：
1. 使用 360 搜索: so.com/s?q=xxx
2. 解析结果: title, url, snippet
3. 返回 SearchResult 列表

写测试 tests/test_so360.py：
- 搜索 "AI"
- 断言结果 > 0
- 打印前5条

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 27. Startpage
    {
        "name": "test_startpage",
        "prompt": """在 {dir} 目录，为 Startpage 创建适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/startpage.py：
1. 使用 Startpage: startpage.com/sp/search?query=xxx
2. Startpage 是隐私搜索引擎，代理 Google 结果
3. 解析结果: title, url, snippet
4. 返回 SearchResult 列表

写测试 tests/test_startpage.py：
- 搜索 "privacy tools"
- 断言结果 > 0
- 打印前5条

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 28. Ecosia
    {
        "name": "test_ecosia",
        "prompt": """在 {dir} 目录，为 Ecosia 创建适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/ecosia.py：
1. 使用 Ecosia: ecosia.org/search?q=xxx
2. Ecosia 是绿色搜索引擎（基于 Bing）
3. 解析结果: title, url, snippet
4. 返回 SearchResult 列表

写测试 tests/test_ecosia.py：
- 搜索 "climate change"
- 断言结果 > 0
- 打印前5条

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 29. Qwant
    {
        "name": "test_qwant",
        "prompt": """在 {dir} 目录，为 Qwant 创建适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/qwant.py：
1. 使用 Qwant: qwant.com/?q=xxx
2. Qwant 是欧洲隐私搜索引擎
3. 解析结果: title, url, snippet
4. 返回 SearchResult 列表

写测试 tests/test_qwant.py：
- 搜索 "open source"
- 断言结果 > 0
- 打印前5条

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 30. B站 (Bilibili)
    {
        "name": "test_bilibili",
        "prompt": """在 {dir} 目录，为 B站 创建搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/bilibili.py：
1. 使用 B站搜索: search.bilibili.com/all?keyword=xxx
2. 解析视频: title, url, author, play_count, danmaku_count, duration
3. B站搜索页是动态加载的，需要等待 JS 渲染
4. 返回 SearchResult 列表（带 play_count 扩展字段）

写测试 tests/test_bilibili.py：
- 搜索 "Python 入门"
- 断言结果 > 0
- 打印前5条（含 author 和 play_count）

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 31. 知乎
    {
        "name": "test_zhihu",
        "prompt": """在 {dir} 目录，为知乎创建搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/zhihu.py：
1. 使用知乎搜索: zhihu.com/search?type=content&q=xxx
2. 解析结果: title, url, excerpt, author, voteup_count, comment_count
3. 知乎有反爬机制，可能需要处理登录弹窗
4. 返回 SearchResult 列表

写测试 tests/test_zhihu.py：
- 搜索 "机器学习"
- 断言结果 > 0
- 打印前5条（含 author 和 voteup_count）

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 32. 小红书
    {
        "name": "test_xiaohongshu",
        "prompt": """在 {dir} 目录，为小红书创建搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/xiaohongshu.py：
1. 使用小红书搜索: xiaohongshu.com/search_result?keyword=xxx
2. 小红书反爬很强，需要登录 cookie
3. 备选方案: 用 Google site:xiaohongshu.com 搜索
4. 解析笔记: title, url, author, likes, notes_type (图文/视频)
5. 返回 SearchResult 列表

写测试 tests/test_xiaohongshu.py：
- 搜索 "旅行攻略"
- 如果直接搜索失败，验证 Google site: 备选方案
- 打印结果

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 33. 抖音
    {
        "name": "test_douyin",
        "prompt": """在 {dir} 目录，为抖音创建搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/douyin.py：
1. 使用抖音搜索: douyin.com/search/xxx
2. 抖音反爬极强，需要登录且需要滑块验证
3. 备选方案: 用 Google site:douyin.com 搜索
4. 解析视频: title, url, author, likes
5. 返回 SearchResult 列表

写测试 tests/test_douyin.py：
- 搜索 "美食"
- 如果直接搜索失败，验证备选方案

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 34. 微博
    {
        "name": "test_weibo",
        "prompt": """在 {dir} 目录，为微博创建搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/weibo.py：
1. 使用微博搜索: s.weibo.com/weibo?q=xxx
2. 微博需要 Cookie 才能搜索
3. 备选方案: 用 Google site:weibo.com 搜索
4. 解析微博: text, url, user, reposts, comments, likes
5. 返回 SearchResult 列表

写测试 tests/test_weibo.py：
- 搜索 "科技新闻"
- 验证至少能通过 Google site: 方案返回结果

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 35. 今日头条
    {
        "name": "test_toutiao",
        "prompt": """在 {dir} 目录，为今日头条创建搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/toutiao.py：
1. 使用今日头条搜索: so.toutiao.com/search?keyword=xxx
2. 解析文章: title, url, source, abstract, comments_count
3. 头条页面是动态加载
4. 返回 SearchResult 列表

写测试 tests/test_toutiao.py：
- 搜索 "人工智能"
- 断言结果 > 0
- 打印前5条

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 36. Unsplash
    {
        "name": "test_unsplash",
        "prompt": """在 {dir} 目录，为 Unsplash 创建图片搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/unsplash.py：
1. 使用 Unsplash 搜索: unsplash.com/s/photos/xxx
2. 解析图片: title, url, photographer, download_url, width, height
3. Unsplash 也有免费 API (api.unsplash.com) 但需要 key
4. 优先用页面解析
5. 返回 SearchResult 列表（带 image_url 扩展字段）

写测试 tests/test_unsplash.py：
- 搜索 "mountain landscape"
- 断言结果 > 0
- 打印前5条（含 photographer）

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 37. Pixabay
    {
        "name": "test_pixabay",
        "prompt": """在 {dir} 目录，为 Pixabay 创建图片搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/pixabay.py：
1. 使用 Pixabay 搜索: pixabay.com/images/search/xxx
2. Pixabay 有免费 API 但需要注册
3. 优先用页面解析
4. 解析图片: title, url, tags, downloads, likes, user
5. 返回 SearchResult 列表（带 image_url 扩展字段）

写测试 tests/test_pixabay.py：
- 搜索 "ocean"
- 断言结果 > 0
- 打印前5条

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 38. Pexels
    {
        "name": "test_pexels",
        "prompt": """在 {dir} 目录，为 Pexels 创建图片搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/pexels.py：
1. 使用 Pexels 搜索: pexels.com/search/xxx
2. Pexels 有免费 API 但需要 key
3. 优先用页面解析
4. 解析图片: title, url, photographer, alt_text
5. 返回 SearchResult 列表（带 image_url 扩展字段）

写测试 tests/test_pexels.py：
- 搜索 "sunset"
- 断言结果 > 0
- 打印前5条

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
    # 39. 小宇宙FM
    {
        "name": "test_xiaoyuzhou",
        "prompt": """在 {dir} 目录，为小宇宙FM创建播客搜索适配器：

```bash
source ~/tools/cloakbrowser/venv/bin/activate
cd {dir}
```

创建 agent_search/engines/xiaoyuzhou.py：
1. 使用小宇宙FM搜索: xiaoyuzhoufm.com/search?q=xxx
2. 解析播客: title, url, podcast_name, duration, description
3. 页面是 React 渲染，需要等待
4. 返回 SearchResult 列表

写测试 tests/test_xiaoyuzhou.py：
- 搜索 "AI"
- 断言结果 > 0
- 打印前5条（含 podcast_name）

运行测试，如果失败修复并重试。
结果写入 PROGRESS.md。""",
    },
]


def read_progress():
    p = os.path.join(PROJECT_DIR, "PROGRESS.md")
    if os.path.exists(p):
        with open(p) as f:
            return f.read()
    return ""


def get_next_task_index():
    """根据 PROGRESS.md 判断已完成到哪一步"""
    progress = read_progress()
    for i, task in enumerate(TASKS):
        if f"✅ {task['name']}" not in progress:
            return i
    return len(TASKS)


def run_task(task_index):
    """运行单个任务"""
    task = TASKS[task_index]
    log.info(f"📋 任务 [{task_index+1}/{len(TASKS)}]: {task['name']}")

    client = ACPClient(cli_path=KIRO_CLI)
    try:
        client.start(cwd=PROJECT_DIR)
        session_id = client.new_session(cwd=PROJECT_DIR)

        prompt = task["prompt"].format(dir=PROJECT_DIR) + CLOAK_SOURCE_NOTE
        log.info(f"📤 发送 ({len(prompt)} 字符)")

        result = client.prompt(session_id, prompt, auto_approve=True)

        log.info(f"✅ 完成: stop={result.stop_reason}, "
                 f"ctx={result.kiro_context_pct:.1f}%, "
                 f"credits={result.kiro_credits:.2f}")

        # 保存响应
        with open(os.path.join(PROJECT_DIR, f"response_{task['name']}.txt"), "w") as f:
            f.write(f"Time: {datetime.now().isoformat()}\n")
            f.write(f"Task: {task['name']}\n")
            f.write(f"Stop: {result.stop_reason}\n")
            f.write(f"Context: {result.kiro_context_pct:.1f}%\n\n")
            f.write(result.text)

        # 更新 PROGRESS.md
        progress_path = os.path.join(PROJECT_DIR, "PROGRESS.md")
        entry = f"\n## ✅ {task['name']} — {datetime.now().strftime('%H:%M:%S')}\n"
        entry += f"- Stop: {result.stop_reason}\n"
        entry += f"- Context: {result.kiro_context_pct:.1f}%\n"
        entry += f"- Credits: {result.kiro_credits:.2f}\n"
        entry += f"- Response: {result.text[:500]}...\n\n"

        existing = read_progress()
        with open(progress_path, "w") as f:
            if not existing:
                f.write("# AgentSearch - Progress\n\n")
            else:
                f.write(existing)
            f.write(entry)

        return True

    except TimeoutError:
        log.warning(f"⏰ 任务 {task['name']} 超时")
        # 记录超时但标记为部分完成
        with open(os.path.join(PROJECT_DIR, "PROGRESS.md"), "a") as f:
            f.write(f"\n## ⏰ {task['name']} — TIMEOUT at {datetime.now().strftime('%H:%M:%S')}\n\n")
        return False
    except Exception as e:
        log.error(f"❌ 任务 {task['name']} 错误: {e}")
        return False
    finally:
        client.stop()


def main():
    os.makedirs(PROJECT_DIR, exist_ok=True)

    log.info("=" * 60)
    log.info("AgentSearch v2 - 增量监督启动")
    log.info("=" * 60)

    # 清理残留 kiro 进程
    os.system("pkill -f 'kiro-cli.*acp' 2>/dev/null")
    time.sleep(2)

    # 找到下一个待执行的任务
    start_idx = get_next_task_index()
    log.info(f"📍 从任务 {start_idx + 1}/{len(TASKS)} 开始")

    for i in range(start_idx, len(TASKS)):
        success = False
        for retry in range(3):
            log.info(f"\n{'='*40}")
            log.info(f"任务 {i+1}/{len(TASKS)}: {TASKS[i]['name']} (尝试 {retry+1}/3)")
            log.info(f"{'='*40}")

            success = run_task(i)
            if success:
                break

            log.info(f"等待 15 秒后重试...")
            time.sleep(15)
            # 清理
            os.system("pkill -f 'kiro-cli.*acp' 2>/dev/null")
            time.sleep(3)

        if not success:
            log.warning(f"⚠️ 任务 {TASKS[i]['name']} 3次全部失败，继续下一个")

        time.sleep(5)

    log.info("🏁 所有任务完成！")
    log.info(f"进度文件: {os.path.join(PROJECT_DIR, 'PROGRESS.md')}")


if __name__ == "__main__":
    main()
