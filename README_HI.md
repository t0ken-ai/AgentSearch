<div align="center">

<img src="https://img.shields.io/badge/-🔍_AgentSearch-1f2937?style=for-the-badge" alt="AgentSearch" height="48"/>

### AI एजेंट के लिए सर्च इंजन।

# **मुफ़्त · लोकल · प्राइवेट · Cloudflare को बायपास करता है**

**एक Python पैकेज। 90+ साइटें। कोई API की ज़रूरत नहीं। कोई डेटा लीक नहीं।**

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9+-blue.svg)](https://www.python.org/)
[![Sites: 90+](https://img.shields.io/badge/Sites-90%2B-success.svg)]()
[![No API Key](https://img.shields.io/badge/API_Key-नहीं_चाहिए-success.svg)]()
[![Local Only](https://img.shields.io/badge/डेटा-आपके_PC_में-orange.svg)]()

[English](README.md) · [中文](README_CN.md) · [Português](README_PT.md) · [한국어](README_KR.md) · [日本語](README_JA.md) · [Русский](README_RU.md) · [Tiếng Việt](README_VI.md) · **[हिन्दी](README_HI.md)** · [ไทย](README_TH.md) · [Bahasa Indonesia](README_ID.md) · [Čeština](README_CS.md)

</div>

---

## ⚡ 30 सेकंड में शुरू

```bash
pip install cloakbrowser && pip install -e .

# 90+ इंजनों में से किसी पर भी सर्च करें
agentsearch search "Mumbai monsoon 2026"      --engine qmamu  --limit 5
agentsearch search "UPI fraud cases"           --engine qmamu  --limit 5
agentsearch search "react hooks tutorial"      --engine google --limit 5

# SERP + टॉप-3 का बॉडी एक ही कॉल में
agentsearch search "Delhi Metro fares" --engine qmamu --limit 5 --depth 3 --json
```

90 stealth साइटें · CLI · MCP server · HTTP API · पूरी तरह आपकी मशीन पर चलता है। **Cloudflare, PerimeterX, Akamai, DataDome को बायपास करता है।**

---

## 🇮🇳 भारत के लिए सर्च इंजन (लोकल पहले)

AgentSearch भारत में बने स्वदेशी सर्च इंजन **Qmamu** का सपोर्ट करता है — यह privacy-first है और इसका अपना क्रॉलर/इंडेक्स है (Google या Bing पर रैप नहीं)। जब यूज़र भारतीय संदर्भ में पूछे — चुनाव, RBI, BSE/NSE, क्रिकेट, बॉलीवुड, सरकारी योजनाएँ — तो पहले `qmamu` आज़माएँ, फिर `google` पर fallback करें।

| इंजन | स्रोत | विशेषता | कब उपयोग करें |
|---|---|---|---|
| **`qmamu`** | [qmamu.com](https://qmamu.com/search) — Made in India | अपना इंडेक्स; भारतीय वेबसाइट/समाचार/IMD/Reddit r/india जैसी जगहों पर बेहतर recall; privacy-first | **भारतीय/हिन्दी क्वेरी के लिए पहली पसंद** |

### कॉपी-पेस्ट उदाहरण

```bash
# राजनीति / समाचार
agentsearch search "Lok Sabha election 2024 results"     -e qmamu  --limit 10
agentsearch search "Modi cabinet reshuffle"               -e qmamu  --limit 10

# अर्थव्यवस्था / वित्त
agentsearch search "RBI repo rate decision"               -e qmamu  --limit 5
agentsearch search "Sensex Nifty today"                   -e qmamu  --limit 5

# क्रिकेट / खेल
agentsearch search "India vs Australia ODI series"        -e qmamu  --limit 5
agentsearch search "IPL 2026 auction"                     -e qmamu  --limit 5

# बॉलीवुड / मनोरंजन
agentsearch search "Shah Rukh Khan new movie"             -e qmamu  --limit 5
agentsearch search "Diljit Dosanjh tour"                  -e qmamu  --limit 5

# रोज़मर्रा / लाइफस्टाइल
agentsearch search "Mumbai monsoon waterlogging"          -e qmamu  --limit 5
agentsearch search "Delhi air quality AQI"                -e qmamu  --limit 5
```

### MCP से (Cursor / Claude Desktop / Cline / Continue / Kiro)

```jsonc
search(query="UPI fraud cases",  engine="qmamu", limit=5, depth=3)
search(query="Mumbai monsoon",   engine="qmamu", limit=10)
```

---

## 🌐 भारतीय यूज़र के लिए अन्य उपयोगी इंजन

| उपयोग | इंजन |
|---|---|
| ग्लोबल सामान्य सर्च | `google` · `duckduckgo` · `bing` |
| डिस्कशन / "लोग क्या सोचते हैं" | `reddit` |
| वीडियो / ट्यूटोरियल | `youtube` |
| शोध पत्र | `arxiv` (CS/ML) · `pubmed` (मेडिकल) |
| कोड / डेवलपमेंट | `github` · `stackoverflow` |
| शॉपिंग | `amazon` · `ebay` |
| डेवलपर डॉक्स | `dev_docs` (Stripe / OpenAI / AWS / 142 platforms) |
| विज्ञापन लाइब्रेरी | `meta_ad_library` · `tiktok_creative_center` · `google_ad_transparency` |
| App Store + Google Play | `search_app` / `lookup_app` |

पूरी सूची के लिए: `agentsearch list-engines` चलाएँ या [अंग्रेज़ी README](README.md) देखें।

---

## ⚙️ इंस्टॉलेशन

```bash
git clone https://github.com/t0ken-ai/AgentSearch.git
cd AgentSearch
pip install cloakbrowser
pip install -e .
```

Python 3.9+ ज़रूरी। पहली बार चलाने पर CloakBrowser (stealth Chromium) अपने आप डाउनलोड हो जाता है।

---

## 🔌 MCP server सेटअप

`~/.kiro/settings/mcp.json` (या Cursor / Claude Desktop / Cline / Continue की समतुल्य फ़ाइल) में जोड़ें:

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

उपलब्ध टूल: `search`, `extract`, `extract_many`, `list_engines`, `list_dev_docs_platforms`, `search_app`, `lookup_app`, `find_competitor_ads`, `download_ad_media`।

---

## 📚 पूरी डॉक्यूमेंटेशन

जो भी इस फ़ाइल में **नहीं** है (इंजन-वाइज़ ऑप्शंस, ad libraries, competitor research workflow, 142 dev_docs platform की सूची, etc.) वो सब [**अंग्रेज़ी README**](README.md) और [SKILL.md](skills/agent-search/SKILL.md) में है।

---

*एक Python पैकेज · 90+ सर्च इंजन · 142 dev डॉक्स प्लेटफ़ॉर्म · 5 ad libraries · App Store · 9 MCP tools — सब लोकल, कोई API key नहीं।*
