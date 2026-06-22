#!/usr/bin/env python3
"""Event Registry (newsapi.ai) connector — RECENT news with entity tags, sentiment, themes.

Our plan covers recent news only (no multi-year archive), so this feeds the REAL-TIME catalyst/
sentiment layer (Phase B) — not historical backtesting. stdlib only; short cache (news is fresh).
Credential: ~/.openclaw/credentials/news-api-ai.json (key 'API key').
"""

from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

from ._http import ConnectorError, cache_read, cache_write, http_post

CRED = Path(os.path.expanduser("~/.openclaw/credentials/news-api-ai.json"))
URL = "https://eventregistry.org/api/v1/article/getArticles"


def _key() -> str:
    if not CRED.exists():
        raise ConnectorError(f"newsapi.ai credentials missing at {CRED}")
    d = json.loads(CRED.read_text())
    k = d.get("API key") or d.get("apiKey") or d.get("api key")
    if not k:
        raise ConnectorError("newsapi.ai credentials missing 'API key'")
    return k


def recent_news(keyword: str, days: int = 14, count: int = 25, cache_h: float = 6.0) -> list[dict]:
    """Recent articles mentioning `keyword` (use the company name). Returns normalized dicts:
    {date, title, url, sentiment, source, concepts:[(label,type,score)], categories:[label]}."""
    ck = f"er_{keyword.lower().replace(' ', '_')[:40]}_{days}_{count}"
    hit = cache_read(ck, max_age_h=cache_h)
    if hit is not None and "data" in hit:
        return hit["data"]
    from datetime import date, timedelta
    body = {
        "action": "getArticles", "keyword": keyword, "lang": "eng",
        "dateStart": (date.today() - timedelta(days=days)).isoformat(),
        "dateEnd": date.today().isoformat(),
        "articlesPage": 1, "articlesCount": min(count, 100), "articlesSortBy": "date",
        "includeArticleConcepts": True, "includeArticleCategories": True,
        "includeArticleSentiment": True, "resultType": "articles", "apiKey": _key(),
    }
    try:
        raw = json.loads(http_post(URL, body, timeout=25.0))   # 429/Retry-After-aware backoff
    except ConnectorError:
        raise
    except Exception as exc:
        raise ConnectorError(f"eventregistry getArticles failed: {str(exc)[:120]}") from exc
    out = []
    for a in (raw.get("articles") or {}).get("results", []):
        out.append({
            "date": a.get("date"), "title": a.get("title"), "url": a.get("url"),
            "sentiment": a.get("sentiment"), "source": (a.get("source") or {}).get("title"),
            "concepts": [(c.get("label", {}).get("eng") or c.get("uri"), c.get("type"), c.get("score"))
                         for c in (a.get("concepts") or [])[:8]],
            "categories": [c.get("label") for c in (a.get("categories") or [])[:4]],
        })
    cache_write(ck, {"data": out})
    return out
