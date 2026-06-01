<div align="center">

<img src="https://img.shields.io/badge/-🔍_AgentSearch-1f2937?style=for-the-badge" alt="AgentSearch" height="48"/>

### AI 에이전트를 위한 검색 엔진.

# **무료 · 로컬 · 프라이빗 · Cloudflare 우회**

**파이썬 패키지 하나. 90+ 사이트. API 키 0개. 데이터 유출 0건.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![Sites: 90+](https://img.shields.io/badge/Sites-90%2B-success.svg)]()
[![No API Key](https://img.shields.io/badge/API_Key-불필요-success.svg)]()
[![Local Only](https://img.shields.io/badge/데이터-내_PC에만-orange.svg)]()

[English](README.md) · [中文](README_CN.md) · [Português](README_PT.md) · **[한국어](README_KR.md)** · [日本語](README_JA.md) · [Русский](README_RU.md) · [Tiếng Việt](README_VI.md) · [हिन्दी](README_HI.md) · [ไทย](README_TH.md) · [Bahasa Indonesia](README_ID.md) · [Čeština](README_CS.md)

</div>

---

## ⚡ 30초 시작 가이드

```bash
pip install cloakbrowser && pip install -e .

# 90+ 사이트 어디든 검색
agentsearch search "삼성전자 신제품"     --engine naver  --limit 5
agentsearch search "카카오 주가"          --engine daum   --limit 5
agentsearch search "react hooks 튜토리얼" --engine google --limit 5

# SERP + 상위 3개 본문을 한 번에
agentsearch search "AI 반도체" --engine naver --limit 5 --depth 3 --json
```

90 stealth 사이트 · CLI · MCP 서버 · HTTP API · 모두 로컬에서 실행. **Cloudflare / PerimeterX / Akamai / DataDome 우회.**

---

## 🇰🇷 한국 검색 엔진 (로컬 우선)

AgentSearch는 한국 시장의 두 강자 **Naver(네이버)**와 **Daum(다음)**을 모두 지원합니다. 사용자가 한국어로 묻거나 한국 관련 주제(정치, K-콘텐츠, 국내 뉴스, 부동산, 맛집)를 다룰 때 `google`보다 먼저 이 둘을 시도하세요.

| 엔진 | 출처 | 강점 | 사용 시점 |
|---|---|---|---|
| **`naver`** | [search.naver.com](https://search.naver.com) — 점유율 ~60% | 통합검색(블로그·카페·뉴스·VIEW), 한국어 콘텐츠 회수율 1위 | **한국어 질의의 기본 선택** |
| **`daum`** | [search.daum.net](https://search.daum.net) — 카카오, 점유율 ~20% | 카카오 생태계 + 보조 한국어 회수 | Naver 결과를 보강할 때 |

### 바로 쓰는 예제

```bash
# 뉴스 / 시사
agentsearch search "윤석열 대통령 정상회담"  -e naver  --limit 10
agentsearch search "북한 미사일 발사"         -e daum   --limit 10

# 비즈니스 / 주식
agentsearch search "삼성전자 실적 발표"        -e naver  --limit 5
agentsearch search "카카오뱅크 주가 전망"      -e daum   --limit 5

# K-콘텐츠 / 엔터
agentsearch search "BTS 새 앨범"               -e naver  --limit 5
agentsearch search "오징어게임 시즌3"          -e daum   --limit 5

# 라이프스타일
agentsearch search "강남 맛집 추천"            -e naver  --limit 5
agentsearch search "제주도 여행 코스"          -e naver  --limit 5
```

### MCP에서 쓰기 (Cursor / Claude Desktop / Cline / Continue / Kiro)

```jsonc
search(query="삼성전자 신제품", engine="naver", limit=5, depth=3)
search(query="카카오 주가",      engine="daum",  limit=10)
```

추가로 한국어 이미지 검색은 `naver_images` / `daum_images`로 사용할 수 있습니다.

---

## 🌐 한국 사용자에게 유용한 다른 엔진

| 용도 | 엔진 |
|---|---|
| 글로벌 일반 검색 | `google` · `duckduckgo` · `bing` |
| 토론 / "사람들 의견" | `reddit` (영어) |
| 영상 / 튜토리얼 | `youtube` |
| 학술 논문 | `arxiv` (CS/ML) · `pubmed` (의학) |
| 코드 / 개발 | `github` · `stackoverflow` |
| 쇼핑 | `amazon` · `ebay` |
| 개발자 문서 | `dev_docs` (Stripe / OpenAI / AWS / 142개) |
| 광고 라이브러리 | `meta_ad_library` · `tiktok_creative_center` · `google_ad_transparency` |
| 앱스토어 | `search_app` / `lookup_app` |

전체 목록: `agentsearch list-engines` 또는 [영문 README](README.md) 참고.

---

## ⚙️ 설치

```bash
git clone https://github.com/t0ken-ai/AgentSearch.git
cd AgentSearch
pip install cloakbrowser
pip install -e .
```

Python 3.9+ 필요. 최초 실행 시 CloakBrowser(스텔스 Chromium)가 자동 다운로드됩니다.

---

## 🔌 MCP 서버 설정

`~/.kiro/settings/mcp.json` (또는 Cursor / Claude Desktop / Cline / Continue 동등 파일)에 추가:

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

노출 도구: `search`, `extract`, `extract_many`, `list_engines`, `list_dev_docs_platforms`, `search_app`, `lookup_app`, `find_competitor_ads`, `download_ad_media`.

---

## 📚 전체 문서

여기에 **포함되지 않은** 내용(엔진별 옵션 전체, 광고 라이브러리, 경쟁사 분석 워크플로우, 142개 dev_docs 플랫폼 목록 등)은 [**영문 README**](README.md)와 [SKILL.md](skills/agent-search/SKILL.md)에 있습니다.

---

*Python 패키지 1개 · 90+ 검색 엔진 · 142개 개발자 문서 · 5개 광고 라이브러리 · 앱스토어 · 9개 MCP 도구 — 모두 로컬, API 키 불필요.*
