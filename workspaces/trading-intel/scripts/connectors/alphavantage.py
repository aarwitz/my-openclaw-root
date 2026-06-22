#!/usr/bin/env python3
"""Alpha Vantage NEWS_SENTIMENT connector — historical, ticker-tagged AI news sentiment (~2022→now).

The historical-news source for BACKTESTING news mechanisms (Event Registry is real-time only on our
tier). Plain REST + key (fits our stdlib connector pattern). Premium key needed for backfill volume.
Credential: ~/.openclaw/credentials/alphavantage.json  (key 'apikey' or 'api key').
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from ._http import ConnectorError, cache_read, cache_write

CRED = Path(os.path.expanduser("~/.openclaw/credentials/alphavantage.json"))
URL = "https://www.alphavantage.co/query"


def available() -> bool:
    return CRED.exists()


def _key() -> str:
    if not CRED.exists():
        raise ConnectorError(f"alphavantage credentials missing at {CRED}")
    d = json.loads(CRED.read_text())
    k = d.get("apikey") or d.get("api key") or d.get("API key") or d.get("key")
    if not k:
        raise ConnectorError("alphavantage credentials missing 'apikey'")
    return k


def _window(ticker, t_from, t_to, key):
    p = {"function": "NEWS_SENTIMENT", "tickers": ticker, "time_from": t_from, "time_to": t_to,
         "limit": "1000", "sort": "EARLIEST", "apikey": key}
    raw = json.loads(urllib.request.urlopen(URL + "?" + urllib.parse.urlencode(p), timeout=30.0)
                     .read().decode("utf-8", "replace") or "{}")
    if "Information" in raw or "Note" in raw:            # rate-limit / tier message
        raise ConnectorError(f"alphavantage: {str(raw)[:120]}")
    out = []
    for f in raw.get("feed", []):
        tp = f.get("time_published", "")[:8]
        for ts in f.get("ticker_sentiment", []):
            if ts.get("ticker") == ticker:
                try:
                    out.append((f"{tp[:4]}-{tp[4:6]}-{tp[6:8]}",
                                float(ts.get("ticker_sentiment_score") or 0),
                                float(ts.get("relevance_score") or 0)))
                except (ValueError, TypeError):
                    pass
    return out


def news_sentiment(ticker: str, start: str = "2022-01-01", cache_h: float = 720.0) -> list[dict]:
    """Per-article ticker sentiment from `start` to today, paginated monthly. Returns
    [{date, sentiment, relevance}] (historical, point-in-time by publish date)."""
    ck = f"av_news_{ticker.lower()}_{start}"
    hit = cache_read(ck, max_age_h=cache_h)
    if hit is not None and "data" in hit:
        return hit["data"]
    key = _key()
    y0, m0 = int(start[:4]), int(start[5:7])
    today = date.today()
    rows = []
    y, m = y0, m0
    while (y, m) <= (today.year, today.month):
        t_from = f"{y:04d}{m:02d}01T0000"
        nm = (m % 12) + 1
        ny = y + (1 if m == 12 else 0)
        t_to = f"{ny:04d}{nm:02d}01T0000"
        try:
            rows += [{"date": d, "sentiment": s, "relevance": r} for d, s, r in _window(ticker, t_from, t_to, key)]
        except ConnectorError:
            raise
        time.sleep(0.8)                                  # be gentle on rate limits
        y, m = ny, nm
    cache_write(ck, {"data": rows})
    return rows
