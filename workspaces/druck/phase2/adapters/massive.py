"""Massive (Polygon-compatible) adapter — price-structure truth.

Authority: daily aggregates, ATR, dollar volume, multi-day setup geometry.
Not used for: catalyst proof (Finnhub), live execution (Alpaca).

Cache strategy: every endpoint reads the on-disk JSON cache first; only
calls upstream if the day's cache is missing. Avoids 429 on rate-limited tier.
"""
from __future__ import annotations

import math
import time
from datetime import date as _date, timedelta
from typing import Optional

from ..credentials import massive_key
from ..http_util import http_get_json, read_cache, read_latest_cache, write_cache

BASE = "https://api.massive.com"
FALLBACK = "https://api.polygon.io"


# ---- global throttle (Polygon free tier = 5 req/min → 13s between calls).
# Set DRUCK_MASSIVE_THROTTLE=0 to disable (paid plan).
import os as _os
_LAST_CALL = [0.0]
_MIN_INTERVAL = float(_os.environ.get("DRUCK_MASSIVE_THROTTLE", "13.0"))


def _throttle() -> None:
    if _MIN_INTERVAL <= 0:
        return
    elapsed = time.time() - _LAST_CALL[0]
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _LAST_CALL[0] = time.time()


def _get(path: str, params: dict, ticker_for_cache: str, endpoint_label: str,
         cache_first: bool = True) -> dict:
    """GET with cache-first behavior. Reads today's cache if present, else fetches."""
    if cache_first:
        cached = read_cache("massive", ticker_for_cache, endpoint_label)
        if cached is not None:
            return cached
        stale = read_latest_cache("massive", ticker_for_cache, endpoint_label)
        if stale is not None:
            return stale
    _throttle()
    p = {**params, "apiKey": massive_key()}
    try:
        out = http_get_json(f"{BASE}{path}", params=p)
    except Exception:
        _throttle()
        out = http_get_json(f"{FALLBACK}{path}", params=p)
    write_cache("massive", ticker_for_cache, endpoint_label, out)
    return out


def daily_aggregates(ticker: str, lookback_days: int = 90) -> list[dict]:
    """Return list of daily bars (oldest→newest) over `lookback_days` calendar days."""
    today = _date.today()
    frm = (today - timedelta(days=lookback_days)).isoformat()
    to = today.isoformat()
    out = _get(
        f"/v2/aggs/ticker/{ticker}/range/1/day/{frm}/{to}",
        {"adjusted": "true", "sort": "asc", "limit": 50000},
        ticker,
        f"aggs_{lookback_days}d",
    )
    return out.get("results") or []


def previous_close(ticker: str) -> Optional[dict]:
    out = _get(f"/v2/aggs/ticker/{ticker}/prev", {"adjusted": "true"}, ticker, "prev")
    res = out.get("results") or []
    return res[0] if res else None


# ---------- structural derivations (rules-only) ----------

def true_range(high: float, low: float, prev_close: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def wilder_atr(bars: list[dict], window: int = 14) -> Optional[float]:
    """Wilder's ATR over the last `window` bars. Returns absolute $."""
    if len(bars) < window + 1:
        return None
    trs: list[float] = []
    for i in range(1, len(bars)):
        h = float(bars[i]["h"]); lo = float(bars[i]["l"]); pc = float(bars[i - 1]["c"])
        trs.append(true_range(h, lo, pc))
    # initial seed = simple avg of first `window` TRs, then Wilder smoothing
    if len(trs) < window:
        return None
    atr = sum(trs[:window]) / window
    for tr in trs[window:]:
        atr = (atr * (window - 1) + tr) / window
    return atr


def sma(bars: list[dict], window: int, key: str = "c") -> Optional[float]:
    if len(bars) < window:
        return None
    return sum(float(b[key]) for b in bars[-window:]) / window


def rolling_high(bars: list[dict], window: int) -> Optional[float]:
    if len(bars) < window:
        return None
    return max(float(b["h"]) for b in bars[-window:])


def realized_vol(bars: list[dict], window: int = 5) -> Optional[float]:
    """Annualized close-to-close vol over last `window` returns."""
    if len(bars) < window + 1:
        return None
    rets = []
    for i in range(-window, 0):
        prev = float(bars[i - 1]["c"]); cur = float(bars[i]["c"])
        if prev > 0:
            rets.append(math.log(cur / prev))
    if not rets:
        return None
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / max(len(rets) - 1, 1)
    return math.sqrt(var) * math.sqrt(252)


def avg_volume(bars: list[dict], window: int = 20) -> Optional[float]:
    if len(bars) < window:
        return None
    return sum(float(b["v"]) for b in bars[-window:]) / window


def pct_change(bars: list[dict], days: int = 5) -> Optional[float]:
    if len(bars) < days + 1:
        return None
    a = float(bars[-days - 1]["c"]); b = float(bars[-1]["c"])
    if a <= 0:
        return None
    return (b - a) / a


def rsi(bars: list[dict], window: int = 14) -> Optional[float]:
    """Wilder RSI on closes."""
    if len(bars) < window + 1:
        return None
    gains, losses = 0.0, 0.0
    for i in range(-window, 0):
        ch = float(bars[i]["c"]) - float(bars[i - 1]["c"])
        if ch >= 0:
            gains += ch
        else:
            losses -= ch
    avg_g = gains / window; avg_l = losses / window
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return 100 - (100 / (1 + rs))


# ---------- snapshot endpoints (broad-market discovery) ----------

def snapshot_gainers() -> list[dict]:
    """Top US stock gainers (snapshot, near-real-time)."""
    out = _get(
        "/v2/snapshot/locale/us/markets/stocks/gainers",
        {},
        "_market",
        "snapshot_gainers",
    )
    return out.get("tickers") or []


def snapshot_losers() -> list[dict]:
    """Top US stock losers (snapshot)."""
    out = _get(
        "/v2/snapshot/locale/us/markets/stocks/losers",
        {},
        "_market",
        "snapshot_losers",
    )
    return out.get("tickers") or []


def snapshot_all_tickers() -> list[dict]:
    """Full US stock universe snapshot — heavy call, use sparingly. ~10k tickers.

    Each entry: {ticker, day:{c,h,l,o,v,vw}, prevDay, lastTrade, lastQuote, todaysChange, todaysChangePerc, updated}
    """
    out = _get(
        "/v2/snapshot/locale/us/markets/stocks/tickers",
        {},
        "_market",
        "snapshot_all",
    )
    return out.get("tickers") or []


def snapshot_ticker(ticker: str) -> Optional[dict]:
    """Single-ticker snapshot — current day OHLCV + prev day + last quote."""
    try:
        out = _get(
            f"/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}",
            {},
            ticker,
            "snapshot",
        )
    except RuntimeError as e:
        # Some Massive/Polygon plans reject single-ticker snapshots with 401/403.
        # Treat that as "snapshot unavailable" rather than a hard pipeline failure.
        msg = str(e)
        if "401" in msg or "403" in msg:
            return None
        raise
    return out.get("ticker")


def grouped_daily(date_str: str) -> list[dict]:
    """All US stocks' daily bars for one date — efficient bulk pull.

    date_str: YYYY-MM-DD (must be a market day).
    """
    out = _get(
        f"/v2/aggs/grouped/locale/us/market/stocks/{date_str}",
        {"adjusted": "true"},
        "_market",
        f"grouped_{date_str}",
    )
    return out.get("results") or []


def ticker_overview(ticker: str) -> Optional[dict]:
    """Reference-data ticker details — market cap, sector, listing date."""
    out = _get(
        f"/v3/reference/tickers/{ticker}",
        {},
        ticker,
        "reference",
    )
    return out.get("results")
