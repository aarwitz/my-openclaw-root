#!/usr/bin/env python3
"""Market-data façade — the single seam for prices / quotes / clock.

Backed by Massive (→ FMP) as the canonical market-data facade. The public API
preserves the stable consumer contract used throughout the desk, so provider
changes stay isolated here.

Deterministic + stdlib only:
  * daily_bars / latest_trade  → Massive (unthrottled, split-adjusted) → FMP.
  * market_clock / is_trading_day → a computed NYSE session calendar (no broker
    round-trip): regular 09:30–16:00 ET, half-days close 13:00 ET, full US market
    holidays incl. Good Friday (computus) and the NYSE observed-day rules.
  * spy_trend → SMA50/200 over Massive SPY closes.
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
    # Fall back to FMP when Massive has NOTHING — or when its series went STALE
    # (>7 calendar days behind). A ticker rename (BK->BNY 2026) leaves the old
    # symbol returning frozen-but-nonempty bars, which used to win over a
    # potentially fresher fallback and silently poison every downstream mark.
    stale = False
    if bars:
        try:
            last = datetime.strptime(str(bars[-1]["t"])[:10], "%Y-%m-%d").date()
            stale = (date.today() - last).days > 7
        except (ValueError, KeyError):
            stale = False
    if not bars or stale:
        try:
            fb = fmp.historical_price(symbol, frm="2004-01-01")
        except ConnectorError:
            fb = []
        fb_bars = [
            {"t": r["date"], "c": r["close"], "h": r.get("high") or r["close"], "v": r.get("volume") or 0}
            for r in sorted(fb or [], key=lambda r: r["date"])
            if r.get("close")
        ]
        # keep whichever series is fresher; both stale -> keep what we had (callers
        # that need freshness must check the last bar date themselves)
        if fb_bars and (not bars or str(fb_bars[-1]["t"]) > str(bars[-1]["t"])):
            bars = fb_bars
    if not bars:
        raise ConnectorError(f"marketdata daily_bars: no bars for {symbol}")
    return bars[-days:] if days else bars


def latest_trade(symbol: str) -> dict[str, Any] | None:
    """LIVE last-trade price for execution-time freshness — {'price','ts','source'} or None.

    Massive snapshot endpoint (Polygon-compatible), lastTrade → day close → prevDay close;
    uncached (marks must be fresh). This tier may be about 15 minutes delayed.
    """
    return massive.latest_trade(symbol)


def latest_trades(symbols: list[str] | tuple[str, ...] | set[str]) -> dict[str, dict]:
    """Bounded bulk live marks; returns only symbols the provider could mark."""
    return massive.latest_trades(symbols)


def cached_daily_bars(symbol: str, max_age_h: float = 36.0) -> list[dict]:
    """Offline-only daily bars from the Massive cache."""
    return massive.cached_daily_bars(symbol, max_age_h=max_age_h)


def cached_daily_close(symbol: str, max_age_h: float = 36.0) -> dict | None:
    """Offline-only prior close; never initiates a provider request."""
    return massive.cached_daily_close(symbol, max_age_h=max_age_h)


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


def daily_bar_complete(date_iso: str, now: datetime | None = None) -> bool:
    """Whether an NYSE daily bar can be treated as a completed close.

    Daily feature generation must not infer this from ``is_open``: the market is
    also closed before 09:30, on holidays, and after an early close.  A past
    session is complete; today's session is complete only after its scheduled
    close; future and non-session dates are never valid daily closes.
    """
    bar_date = date.fromisoformat(date_iso[:10])
    now_et = (now or datetime.now(timezone.utc)).astimezone(_et())
    if not _is_session(bar_date) or bar_date > now_et.date():
        return False
    if bar_date < now_et.date():
        return True
    return now_et >= datetime.combine(bar_date, _close_time(bar_date), _et())


def _next_session(d: date) -> date:
    d += timedelta(days=1)
    while not _is_session(d):
        d += timedelta(days=1)
    return d


def market_clock(now: datetime | None = None) -> dict[str, Any]:
    """Deterministic NYSE clock: {is_open, next_open, next_close, timestamp}. next_open/next_close
    are ET ISO timestamps matching the desk's stable clock contract."""
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
    """SMA50/200 regime read for SPY using the stable market-data contract."""
    bars = daily_bars("SPY", days=260)
    closes = [b["c"] for b in bars if b.get("c") is not None]
    if len(closes) < 200:
        raise ConnectorError(f"spy_trend: only {len(closes)} bars, need >=200")
    sma50 = sum(closes[-50:]) / 50.0
    sma200 = sum(closes[-200:]) / 200.0
    last = closes[-1]
    # TM-172: classify_regime's crisis rule reads sma50_lt_sma200_falling_sessions but no
    # provider ever supplied it (silently defaulted 0 -> death-cross persistence could never
    # trigger crisis). Definition: consecutive most-recent sessions with SMA50 < SMA200.
    falling = 0
    for k in range(len(closes), 199, -1):
        w = closes[:k]
        if sum(w[-50:]) / 50.0 < sum(w[-200:]) / 200.0:
            falling += 1
        else:
            break
    return {
        "sma50_lt_sma200_falling_sessions": falling,
        "close": round(last, 2),
        "sma50": round(sma50, 2),
        "sma200": round(sma200, 2),
        "close_vs_sma200_pct": round((last / sma200 - 1.0) * 100.0, 3),
        "sma50_gt_sma200": sma50 > sma200,
        "as_of": bars[-1].get("t"),
        "retrieved_at": _now_iso(),
        "source": "massive_market_data:SPY:1Day",
    }
