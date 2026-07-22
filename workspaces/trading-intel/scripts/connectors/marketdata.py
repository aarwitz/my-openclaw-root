#!/usr/bin/env python3
"""Market-data façade — the single seam for prices / quotes / clock.

Backed by Massive (→ FMP), replacing the Alpaca market-data feed as the last step
of the D52 cutover (2026-07-22). Drop-in for the former
`from connectors.alpaca import daily_bars, latest_trade, market_clock,
is_trading_day, spy_trend, ConnectorError` — so removing Alpaca is a mechanical
import repoint at ~13 call sites, and the provider is swapped HERE, once, ever
after.

Deterministic + stdlib only:
  * daily_bars / latest_trade  → Massive (unthrottled, split-adjusted) → FMP.
  * market_clock / is_trading_day → a computed NYSE session calendar (no broker
    round-trip): regular 09:30–16:00 ET, half-days close 13:00 ET, full US market
    holidays incl. Good Friday (computus) and the NYSE observed-day rules.
  * spy_trend → SMA50/200 over Massive SPY closes (same shape as the old helper).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from typing import Any

from ._http import ConnectorError  # noqa: F401  (re-exported for callers)
from . import fmp, massive

_ET = None


def _et():
    """America/New_York tz (DST-aware). Falls back to fixed EST if zoneinfo is absent."""
    global _ET
    if _ET is None:
        try:
            from zoneinfo import ZoneInfo
            _ET = ZoneInfo("America/New_York")
        except Exception:
            _ET = timezone(timedelta(hours=-5))
    return _ET


# --------------------------------------------------------------------------- prices
def daily_bars(symbol: str, days: int = 260, adjustment: str = "raw") -> list[dict[str, Any]]:
    """Up to `days` split-adjusted daily bars, oldest first: [{t(YYYY-MM-DD),c,h,v}].

    `adjustment` is accepted for call-site compatibility but ignored — Massive/FMP
    are always split-adjusted (which is what every consumer actually wants).
    """
    bars = massive.daily_bars(symbol)
    if not bars:
        try:
            fb = fmp.historical_price(symbol, frm="2004-01-01")
        except ConnectorError:
            fb = []
        bars = [
            {"t": r["date"], "c": r["close"], "h": r.get("high") or r["close"], "v": r.get("volume") or 0}
            for r in sorted(fb or [], key=lambda r: r["date"])
            if r.get("close")
        ]
    if not bars:
        raise ConnectorError(f"marketdata daily_bars: no bars for {symbol}")
    return bars[-days:] if days else bars


def latest_trade(symbol: str) -> dict[str, Any] | None:
    """LIVE last-trade price for execution-time freshness — {'price','ts','source'} or None.

    Massive snapshot endpoint (Polygon-compatible), lastTrade → day close → prevDay close;
    uncached (marks must be fresh). ~15-min delayed on this tier = the Alpaca free feed it replaces.
    """
    try:
        d = massive._get(f"{massive.BASE}/v2/snapshot/locale/us/markets/stocks/tickers/{symbol}")
    except ConnectorError:
        return None
    t = d.get("ticker") or {}
    price = (
        (t.get("lastTrade") or {}).get("p")
        or (t.get("day") or {}).get("c")
        or (t.get("prevDay") or {}).get("c")
    )
    if not price:
        return None
    return {"price": float(price), "ts": t.get("updated"), "source": "massive"}


# --------------------------------------------------------------------------- NYSE calendar
def _easter(year: int) -> date:
    """Gregorian Easter Sunday (anonymous computus)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = ((h + ell - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """n-th `weekday` (0=Mon) of month."""
    first = date(year, month, 1)
    return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last = nxt - timedelta(days=1)
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def _observed(d: date, shift_saturday: bool = True) -> date:
    """NYSE observed-day rule: Sat→Fri, Sun→Mon. New Year's Day does NOT shift to
    the prior Friday (that Friday stays a normal session), so pass shift_saturday=False."""
    if d.weekday() == 5:  # Saturday
        return d - timedelta(days=1) if shift_saturday else d
    if d.weekday() == 6:  # Sunday
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=64)
def _holidays(year: int) -> frozenset[date]:
    hs = {
        _observed(date(year, 1, 1), shift_saturday=False),  # New Year's Day
        _nth_weekday(year, 1, 0, 3),                         # MLK — 3rd Mon Jan
        _nth_weekday(year, 2, 0, 3),                         # Washington's Bday — 3rd Mon Feb
        _easter(year) - timedelta(days=2),                  # Good Friday
        _last_weekday(year, 5, 0),                           # Memorial — last Mon May
        _observed(date(year, 7, 4)),                         # Independence Day
        _nth_weekday(year, 9, 0, 1),                          # Labor — 1st Mon Sep
        _nth_weekday(year, 11, 3, 4),                         # Thanksgiving — 4th Thu Nov
        _observed(date(year, 12, 25)),                       # Christmas
    }
    if year >= 2022:
        hs.add(_observed(date(year, 6, 19)))                 # Juneteenth (NYSE from 2022)
    return frozenset(hs)


def _is_session(d: date) -> bool:
    return d.weekday() < 5 and d not in _holidays(d.year)


@lru_cache(maxsize=64)
def _early_closes(year: int) -> frozenset[date]:
    """1:00pm ET half-days: day after Thanksgiving, Christmas Eve, July 3 — when each is a session."""
    out = set()
    dat = _nth_weekday(year, 11, 3, 4) + timedelta(days=1)   # Fri after Thanksgiving
    if _is_session(dat):
        out.add(dat)
    for d in (date(year, 12, 24), date(year, 7, 3)):
        if _is_session(d):
            out.add(d)
    return frozenset(out)


def _close_time(d: date) -> time:
    return time(13, 0) if d in _early_closes(d.year) else time(16, 0)


_OPEN = time(9, 30)


def is_trading_day(date_iso: str) -> bool:
    """True if `date_iso` (YYYY-MM-DD, ET) is a full or half NYSE session."""
    return _is_session(date.fromisoformat(date_iso[:10]))


def _next_session(d: date) -> date:
    d += timedelta(days=1)
    while not _is_session(d):
        d += timedelta(days=1)
    return d


def market_clock(now: datetime | None = None) -> dict[str, Any]:
    """Deterministic NYSE clock: {is_open, next_open, next_close, timestamp}. next_open/next_close
    are ET ISO timestamps (matching the shape the Alpaca clock consumers read)."""
    et = _et()
    now_et = (now or datetime.now(timezone.utc)).astimezone(et)
    today = now_et.date()
    session = _is_session(today)
    open_dt = datetime.combine(today, _OPEN, et)
    close_dt = datetime.combine(today, _close_time(today), et)
    is_open = session and open_dt <= now_et < close_dt

    if session and now_et < open_dt:
        next_open = open_dt
    else:
        nd = _next_session(today)
        next_open = datetime.combine(nd, _OPEN, et)
    if session and now_et < close_dt:
        next_close = close_dt
    else:
        nd = _next_session(today)
        next_close = datetime.combine(nd, _close_time(nd), et)

    return {
        "timestamp": now_et.isoformat(),
        "is_open": is_open,
        "next_open": next_open.isoformat(),
        "next_close": next_close.isoformat(),
    }


# --------------------------------------------------------------------------- SPY trend
def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def spy_trend() -> dict[str, Any]:
    """SMA50/200 regime read for SPY (same shape as the retired alpaca.spy_trend)."""
    bars = daily_bars("SPY", days=260)
    closes = [b["c"] for b in bars if b.get("c") is not None]
    if len(closes) < 200:
        raise ConnectorError(f"spy_trend: only {len(closes)} bars, need >=200")
    sma50 = sum(closes[-50:]) / 50.0
    sma200 = sum(closes[-200:]) / 200.0
    last = closes[-1]
    return {
        "close": round(last, 2),
        "sma50": round(sma50, 2),
        "sma200": round(sma200, 2),
        "close_vs_sma200_pct": round((last / sma200 - 1.0) * 100.0, 3),
        "sma50_gt_sma200": sma50 > sma200,
        "as_of": bars[-1].get("t"),
        "retrieved_at": _now_iso(),
        "source": "massive_market_data:SPY:1Day",
    }
