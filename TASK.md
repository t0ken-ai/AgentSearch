# Cloak Stealth Suite - 开发任务书

## 项目目标

基于 CloakBrowser（开源反检测 Chromium）进行二次开发，打造一个**生产级无头浏览器自动化框架**，能稳定绕过以下网站的反爬检测：

### 目标网站（优先级排序）

**P0 - 必须通过：**
- Google Search (google.com/search)
- Bing (bing.com)
- DuckDuckGo (duckduckgo.com) - 已通过 ✅
- Reddit (reddit.com)
- Twitter/X (x.com)

**P1 - 重要：**
- Facebook/Meta (facebook.com)
- Instagram (instagram.com)
- LinkedIn (linkedin.com)
- Amazon (amazon.com)
- Cloudflare Turnstile 保护的网站

**P2 - 加分：**
- TikTok (tiktok.com)
- YouTube (youtube.com)
- Wikipedia (wikipedia.org)

### 反检测测试标准

每个目标网站必须通过以下全部测试：

1. **Headless 模式**下能正常访问和交互
2. 通过以下反检测测试工具：
   - bot.sannysoft.com
   - CreepJS (abrahamjuliot.github.io/creepjs/)
   - PixelScan (pixelscan.net)
   - BrowserLeaks (browserleaks.com)
   - reCAPTCHA v3 分数 >= 0.7
3. 能执行搜索、滚动、点击等基本交互
4. 连续运行 10 次不触发验证码/CAPTCHA

## 技术约束

- **基础**: CloakBrowser (pip install cloakbrowser)
- **语言**: Python 3.14
- **平台**: macOS Apple Silicon (M4 Mac Mini)
- **模式**: Headless（无头）为主
- **虚拟环境**: ~/tools/cloakbrowser/venv/
- **CloakBrowser 二进制**: ~/.cloakbrowser/

## 开发要求

1. 创建 Python 模块 `cloak_stealth_suite/`，包含：
   - `core.py` - 核心浏览器启动和配置
   - `engines/` - 各搜索引擎适配器 (google.py, bing.py, reddit.py 等)
   - `stealth/` - 反检测增强模块
   - `tests/` - 自动化测试套件
   - `cli.py` - 命令行工具

2. 每个网站适配器必须：
   - 自动检测被拦截（验证码、重定向、空白页）
   - 自动重试机制
   - 支持代理集成
   - 有详细的错误日志

3. 自测流程：
   - 每完成一个适配器，立即用 headless 模式测试
   - 记录失败原因，分析反检测机制
   - 自动调整策略重试
   - 输出测试报告

4. 项目目录: ~/projects/AgentSearch/

## 已知问题

- Google Search headless 模式下结果 DOM 结构不稳定
- Bing 可能返回空白结果
- Reddit 需要处理 rate limiting
- bot.sannysoft.com 可能超时（网络问题）

## 交付物

1. 完整的 Python 包（可 pip install -e .）
2. 每个目标网站的测试报告
3. 反检测测试通过截图/日志
4. 使用文档
