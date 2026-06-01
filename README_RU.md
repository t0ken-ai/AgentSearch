<div align="center">

<img src="https://img.shields.io/badge/-🔍_AgentSearch-1f2937?style=for-the-badge" alt="AgentSearch" height="48"/>

### Поисковая система для AI-агентов.

# **Бесплатно · Локально · Приватно · Обходит Cloudflare**

**Один Python-пакет. 90+ сайтов. Ноль API-ключей. Ноль утечек данных.**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![Sites: 90+](https://img.shields.io/badge/Sites-90%2B-success.svg)]()
[![No API Key](https://img.shields.io/badge/API_ключ-не_нужен-success.svg)]()
[![Local Only](https://img.shields.io/badge/Данные-только_у_вас-orange.svg)]()

[English](README.md) · [中文](README_CN.md) · [Português](README_PT.md) · [한국어](README_KR.md) · [日本語](README_JA.md) · **[Русский](README_RU.md)** · [Tiếng Việt](README_VI.md) · [हिन्दी](README_HI.md) · [ไทย](README_TH.md) · [Bahasa Indonesia](README_ID.md) · [Čeština](README_CS.md)

</div>

---

## ⚡ Старт за 30 секунд

```bash
pip install cloakbrowser && pip install -e .

# Поиск по любому из 90+ движков
agentsearch search "санкции 2026"            --engine yandex  --limit 5
agentsearch search "курс рубля прогноз"      --engine mail_ru --limit 5
agentsearch search "react hooks учебник"     --engine google  --limit 5

# SERP + тело топ-3 за один вызов
agentsearch search "ИИ стартапы Россия" --engine yandex --limit 5 --depth 3 --json
```

90 stealth-сайтов · CLI · MCP-сервер · HTTP API · всё работает локально. **Обходит Cloudflare, PerimeterX, Akamai, DataDome.**

---

## 🇷🇺 Российские поисковики (локальный приоритет)

AgentSearch поддерживает оба ведущих поисковика рунета: **Yandex** (доминирующий) и **Mail.ru** (второй по популярности). Когда пользователь пишет по-русски или спрашивает о России / Беларуси / Казахстане, начинайте с них, а `google` оставляйте на запасной случай.

| Движок | Источник | Сильные стороны | Когда использовать |
|---|---|---|---|
| **`yandex`** | [yandex.ru](https://yandex.ru/search) — лидер рунета | Лучшая полнота для .ru-контента, новости, бизнес, региональные запросы | **Первый выбор для русскоязычных запросов** |
| **`mail_ru`** | [go.mail.ru](https://go.mail.ru/search) | Альтернатива Яндексу, помогает дополнить выдачу | Когда Яндекс перегружен или нужно сравнить выдачу |

### Готовые примеры

```bash
# Новости / политика
agentsearch search "выборы президента"        -e yandex  --limit 10
agentsearch search "санкции ЕС нефть"          -e mail_ru --limit 10

# Экономика / финансы
agentsearch search "ставка ЦБ заседание"       -e yandex  --limit 5
agentsearch search "курс доллара прогноз"      -e mail_ru --limit 5

# Технологии / стартапы
agentsearch search "ИИ стартапы Россия 2026"   -e yandex  --limit 5
agentsearch search "Сбер GigaChat"             -e yandex  --limit 5

# Развлечения / культура
agentsearch search "Кинопоиск рейтинг 2026"    -e yandex  --limit 5
agentsearch search "новинки сериалов"          -e mail_ru --limit 5
```

### Из MCP (Cursor / Claude Desktop / Cline / Continue / Kiro)

```jsonc
search(query="ставка ЦБ", engine="yandex",  limit=5, depth=3)
search(query="курс рубля", engine="mail_ru", limit=10)
```

Поиск картинок: `yandex_images` (часто превосходит Google для русскоязычных запросов и обратного поиска лиц).

---

## 🌐 Другие полезные движки для русскоязычных пользователей

| Задача | Движок |
|---|---|
| Глобальный общий поиск | `google` · `duckduckgo` · `bing` |
| Обсуждения / "что думают люди" | `reddit` (англ.) |
| Видео / уроки | `youtube` |
| Научные статьи | `arxiv` (CS/ML) · `pubmed` (медицина) |
| Код / разработка | `github` · `stackoverflow` |
| Покупки | `amazon` · `ebay` |
| Документация для разработчиков | `dev_docs` (Stripe / OpenAI / AWS / 142 платформы) |
| Рекламные библиотеки | `meta_ad_library` · `tiktok_creative_center` · `google_ad_transparency` |
| App Store + Google Play | `search_app` / `lookup_app` |

Полный список: `agentsearch list-engines` или [англ. README](README.md).

---

## ⚙️ Установка

```bash
git clone https://github.com/t0ken-ai/AgentSearch.git
cd AgentSearch
pip install cloakbrowser
pip install -e .
```

Требуется Python 3.9+. CloakBrowser (stealth Chromium) скачается автоматически при первом запуске.

---

## 🔌 MCP-сервер

Добавьте в `~/.kiro/settings/mcp.json` (или эквивалент в Cursor / Claude Desktop / Cline / Continue):

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

Доступные инструменты: `search`, `extract`, `extract_many`, `list_engines`, `list_dev_docs_platforms`, `search_app`, `lookup_app`, `find_competitor_ads`, `download_ad_media`.

---

## 📚 Полная документация

Всё, чего **нет** в этом файле (полная таблица опций движков, рекламные библиотеки, воркфлоу анализа конкурентов, список 142 платформ dev_docs и т. п.) находится в [**англоязычном README**](README.md) и [SKILL.md](skills/agent-search/SKILL.md).

---

*Один Python-пакет · 90+ поисковых движков · 142 платформы документации · 5 рекламных библиотек · App Store · 9 MCP-инструментов — всё локально, без API-ключей.*
