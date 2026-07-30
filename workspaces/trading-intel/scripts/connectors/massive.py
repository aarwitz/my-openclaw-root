#!/usr/bin/env python3
"""Massive (formerly Polygon) connector — the unthrottled market-data backbone.

Upgraded plan: unlimited calls, ~10yr split-adjusted history incl. delisted names, + a ticker-news
endpoint (historical articles back to ~2021; AI `insights.sentiment` only on recent articles, so
historical sentiment is computed from title/description by the caller). Base https://api.massive.com
(Polygon-compatible), auth via `apiKey=` query param. stdlib only.
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

from ._http import ConnectorError, cache_read, cache_write, http_get

CRED = Path(os.path.expanduser("~/.openclaw/credentials/massive-api.json"))
BASE = "https://api.massive.com"


def available() -> bool:
    return CRED.exists()


def _key() -> str:
    if not CRED.exists():
        raise ConnectorError(f"massive credentials missing at {CRED}")
    d = json.loads(CRED.read_text())
    k = d.get("api key") or d.get("apikey") or d.get("key")
    if not k:
        raise ConnectorError("massive credentials missing 'api key'")
    return k


def _get(url: str, *, timeout: float = 30.0, retries: int = 3) -> dict:
    sep = "&" if "?" in url else "?"
    full = url if "apiKey=" in url else f"{url}{sep}apiKey={_key()}"
    raw = http_get(full, timeout=timeout, retries=retries)
    return json.loads(raw.decode("utf-8", "replace") or "{}")


def daily_bars(symbol: str, frm: str = "2015-01-01", to: str | None = None,
               cache_h: float = 12.0) -> list[dict]:
    """Split-adjusted daily OHLCV, oldest first, shape {t(iso),c,h,v} (matches our price loaders)."""
    to = to or date.today().isoformat()
    ck = f"massive_{symbol.lower()}_1d_{frm}_{to}"
    hit = cache_read(ck, max_age_h=cache_h)
    if hit is not None and "bars" in hit:
        return hit["bars"]
    url = f"{BASE}/v2/aggs/ticker/{symbol}/range/1/day/{frm}/{to}?adjusted=true&sort=asc&limit=50000"
    d = _get(url)
    out = []
    for b in d.get("results", []) or []:
        iso = datetime.fromtimestamp(b["t"] / 1000, timezone.utc).date().isoformat()
        out.append({"t": iso, "c": b.get("c"), "h": b.get("h") or b.get("c"), "v": b.get("v") or 0})
    if out:
        cache_write(ck, {"bars": out})
    return out


def latest_trades(
    symbols: list[str] | tuple[str, ...] | set[str],
    *,
    timeout: float = 8.0,
) -> dict[str, dict]:
    """One bounded snapshot request for many symbols.

    Serial per-ticker snapshots made a 38-name book take minutes to mark when
    DNS or the provider degraded. This call has no retry loop by design: a
    money-path pass must fail/degrade promptly, not hang behind N×timeouts.
    """
    wanted = sorted({str(symbol).upper() for symbol in symbols if str(symbol).strip()})
    if not wanted:
        return {}
    query = urllib.parse.urlencode({"tickers": ",".join(wanted)})
    try:
        payload = _get(
            f"{BASE}/v2/snapshot/locale/us/markets/stocks/tickers?{query}",
            timeout=timeout,
            retries=0,
        )
    except ConnectorError:
        return {}
    out: dict[str, dict] = {}
    for ticker in payload.get("tickers", []) or []:
        symbol = str(ticker.get("ticker") or "").upper()
        price = (
            (ticker.get("lastTrade") or {}).get("p")
            or (ticker.get("day") or {}).get("c")
            or (ticker.get("prevDay") or {}).get("c")
        )
        if symbol and price:
            out[symbol] = {
                "price": float(price),
                "ts": ticker.get("updated"),
                "source": "massive_bulk_snapshot",
            }
    return out


def cached_daily_bars(symbol: str, max_age_h: float = 36.0) -> list[dict]:
    """Read the normal daily-bar cache without triggering a network fallback."""
    today = date.today().isoformat()
    hit = cache_read(
        f"massive_{symbol.lower()}_1d_2015-01-01_{today}",
        max_age_h=max_age_h,
    )
    return list((hit or {}).get("bars") or [])


def cached_daily_close(symbol: str, max_age_h: float = 36.0) -> dict | None:
    bars = cached_daily_bars(symbol, max_age_h=max_age_h)
    if not bars or bars[-1].get("c") is None:
        return None
    return {
        "price": float(bars[-1]["c"]),
        "ts": str(bars[-1].get("t") or "")[:10],
        "source": "massive_cached_daily_close",
    }


def latest_trade(symbol: str) -> dict | None:
    """Most-recent trade price via the snapshot endpoint (Polygon-compatible), for live marks.

    Returns {"price": float, "ts": <ns epoch|None>, "source": "massive"} or None. Falls back
    lastTrade -> day close -> prevDay close so a quiet/closed tape still yields a mark. Shape
    matches the connector-neutral market-data contract ({"price": ...}).
    Deliberately uncached: daily_bars is the cached historical path.
    """
    return latest_trades([symbol]).get(symbol.upper())


def short_interest(ticker: str, cache_h: float = 24.0) -> list[dict]:
    """Point-in-time short-interest history (FINRA, bi-monthly settlement dates).

    Returns oldest-first [{date, short_interest, avg_daily_volume, days_to_cover}] where
    `date` is the FINRA *settlement_date* (the "as of" date). NOTE on point-in-time use:
    FINRA disseminates each settlement ~8 business days later, so the caller must lag `date`
    by the dissemination delay before stamping a feature (see feature_store `_short_interest`).
    `days_to_cover` (= short_interest / avg_daily_volume) is the clean, directly-usable field;
    short-%-of-float is NOT provided by this endpoint (Massive only exposes a *current*
    shares-outstanding snapshot, which would leak if applied to historical short interest).
    """
    ck = f"massive_si_{ticker.lower()}"
    hit = cache_read(ck, max_age_h=cache_h)
    if hit is not None and "data" in hit:
        return hit["data"]
    url = f"{BASE}/stocks/v1/short-interest?ticker={ticker}&limit=10000"
    out: list[dict] = []
    pages = 0
    while url and pages < 50:
        d = _get(url)
        for r in d.get("results", []) or []:
            sd = (r.get("settlement_date") or "")[:10]
            si = r.get("short_interest")
            if not sd or si is None:
                continue
            out.append({
                "date": sd,
                "short_interest": int(si),
                "avg_daily_volume": int(r.get("avg_daily_volume") or 0),
                "days_to_cover": (float(r["days_to_cover"]) if r.get("days_to_cover") is not None else None),
            })
        url = d.get("next_url")
        pages += 1
    out.sort(key=lambda x: x["date"])
    if out:
        cache_write(ck, {"data": out})
    return out


def ticker_news(symbol: str, gte: str = "2021-01-01", to: str | None = None,
                max_pages: int = 40, cache_h: float = 168.0) -> list[dict]:
    """Historical articles for `symbol`: [{date, title, description, sentiment|None, relevance}].
    `sentiment` from Massive AI insights when present (recent), else None (caller scores text)."""
    to = to or date.today().isoformat()
    ck = f"massive_news_{symbol.lower()}_{gte}_{to}"
    hit = cache_read(ck, max_age_h=cache_h)
    if hit is not None and "data" in hit:
        return hit["data"]
    url = (f"{BASE}/v2/reference/news?ticker={symbol}&published_utc.gte={gte}"
           f"&published_utc.lte={to}&order=asc&limit=1000")
    out, pages = [], 0
    while url and pages < max_pages:
        d = _get(url)
        for a in d.get("results", []) or []:
            sent = None
            for ins in (a.get("insights") or []):
                if ins.get("ticker") == symbol and ins.get("sentiment"):
                    sent = {"positive": 1.0, "negative": -1.0, "neutral": 0.0}.get(ins["sentiment"])
            out.append({"date": (a.get("published_utc") or "")[:10], "title": a.get("title") or "",
                        "description": a.get("description") or "", "sentiment": sent, "relevance": 1.0})
        url = d.get("next_url")
        pages += 1
    cache_write(ck, {"data": out})
    return out


def news_articles(symbol: str, gte: str = "2024-06-01", to: str | None = None,
                  max_pages: int = 4, cache_h: float = 336.0) -> list[dict]:
    """Raw articles KEEPING the multi-ticker tags + keywords (for co-mention / catalyst graph edges):
    [{id, date, tickers:[...], keywords:[...]}]. Separate cache from ticker_news (which drops the tags)."""
    to = to or date.today().isoformat()
    ck = f"massive_newsfull_{symbol.lower()}_{gte}_{to}"
    hit = cache_read(ck, max_age_h=cache_h)
    if hit is not None and "data" in hit:
        return hit["data"]
    url = (f"{BASE}/v2/reference/news?ticker={symbol}&published_utc.gte={gte}"
           f"&published_utc.lte={to}&order=desc&limit=1000")
    out, pages = [], 0
    while url and pages < max_pages:
        d = _get(url)
        for a in d.get("results", []) or []:
            out.append({"id": a.get("id") or a.get("article_url"),
                        "date": (a.get("published_utc") or "")[:10],
                        "tickers": a.get("tickers") or [],
                        "keywords": [k.lower() for k in (a.get("keywords") or [])]})
        url = d.get("next_url")
        pages += 1
    cache_write(ck, {"data": out})
    return out
