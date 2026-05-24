"""Build a Phase II candidate universe from multiple pools.

Pools (Druck-approved order):
  1. Market-wide catalyst pool   (Finnhub earnings double-beats)
  2. Broad mover pool            (Massive snapshot gainers + unusual volume)
  3. Sector leadership pool      (top 3 sector ETFs over 5d)
  4. Fresh IPO / new listing     (Massive snapshot, days_since_ipo <= 365)
  5. Manual watchlist / seed

All pools are deduped, but each ticker preserves the source tags that pulled
it in (`top_gainer`, `double_beat`, `sector_leader`, `recent_ipo_mover`,
`high_volume_breakout`, `manual_seed`).

Returns:
  list[CandidatePool] — each row carries (ticker, source_tags) for downstream
  ranking and journaling.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Iterable, Optional

from .adapters import finnhub, massive
from .universe import (
    DEFAULT_MIN_DOLLAR_VOL_M,
    DEFAULT_MIN_PRICE,
    SECTOR_ETFS,
    is_junk_ticker,
)


DEFAULT_SEED = (
    "NVDA", "MSFT", "AAPL", "GOOGL", "META", "AMZN", "AVGO", "AMD", "TSLA",
    "PLTR", "SMCI", "NFLX", "MU", "CRM", "ORCL", "COIN", "LLY", "UNH",
)


@dataclass
class CandidatePool:
    ticker: str
    source_tags: list[str] = field(default_factory=list)

    def add_tag(self, tag: str) -> None:
        if tag not in self.source_tags:
            self.source_tags.append(tag)

    def as_dict(self) -> dict:
        return asdict(self)


# ===================================================================
# Pool 1 — catalyst (Finnhub double-beat earnings)
# ===================================================================

def _pool_catalyst(days_back: int = 10) -> list[str]:
    try:
        rows = finnhub.market_earnings_calendar(days_back=days_back)
    except Exception:
        return []
    out: set[str] = set()
    for row in rows:
        try:
            ea, ee = row.get("epsActual"), row.get("epsEstimate")
            ra, re_ = row.get("revenueActual"), row.get("revenueEstimate")
            if ea is None or ee is None or ra is None or re_ is None:
                continue
            if float(ea) > float(ee) and float(ra) > float(re_):
                sym = (row.get("symbol") or "").upper()
                if sym and not is_junk_ticker(sym):
                    out.add(sym)
        except (TypeError, ValueError):
            continue
    return sorted(out)


# ===================================================================
# Pool 2 — broad movers (Massive grouped_daily — one bulk call)
# ===================================================================

def _pool_movers() -> tuple[list[str], list[str]]:
    """Returns (top_gainers, unusual_volume). Uses grouped_daily for full coverage."""
    from .market_scanner import _discover_from_grouped
    g, u, _ = _discover_from_grouped()
    return g, u


# ===================================================================
# Pool 3 — sector leaders (top 3 sector ETFs over 5d)
# ===================================================================

def _pool_sector_leaders(top_n: int = 3) -> list[str]:
    moves: list[tuple[str, float]] = []
    for etf in SECTOR_ETFS.values():
        try:
            bars = massive.daily_aggregates(etf, lookback_days=15)
            mv = massive.pct_change(bars, 5)
            if mv is not None:
                moves.append((etf, mv))
        except Exception:
            continue
    moves.sort(key=lambda x: -x[1])
    return [etf for etf, _ in moves[:top_n]]


# ===================================================================
# Pool 4 — recent IPO movers (uses grouped_daily + ref-data lookups)
# ===================================================================

def _pool_recent_ipos(max_days: int = 365, min_dollar_vol_m: float = 25.0) -> list[str]:
    """Recent IPOs from grouped_daily + ref-data lookup.

    NOTE: ticker_overview is per-ticker and slow; cap at top-200 by liquidity.
    """
    out: list[str] = []
    try:
        from .market_scanner import _last_trading_day
        bars = massive.grouped_daily(_last_trading_day(0))
    except Exception:
        return []
    scored = []
    for b in bars:
        tk = b.get("T")
        c = float(b.get("c") or 0)
        v = float(b.get("v") or 0)
        if not tk or is_junk_ticker(tk) or c < DEFAULT_MIN_PRICE:
            continue
        dv = (v * c) / 1e6
        if dv >= min_dollar_vol_m:
            scored.append((tk, dv))
    scored.sort(key=lambda x: -x[1])
    for tk, _ in scored[:200]:
        try:
            ref = massive.ticker_overview(tk)
            ipo = ref.get("list_date") if ref else None
            if not ipo:
                continue
            from datetime import datetime as _dt, timezone as _tz
            d_ipo = _dt.fromisoformat(ipo).date()
            age = (_dt.now(_tz.utc).date() - d_ipo).days
            if 0 <= age <= max_days:
                out.append(tk)
                if len(out) >= 25:
                    break
        except Exception:
            continue
    return out


# ===================================================================
# Public — generate composite universe with source tags
# ===================================================================

def generate_pools(
    *,
    seed: Optional[Iterable[str]] = None,
    days_back: int = 10,
    max_total: int = 60,
    include_movers: bool = True,
    include_sector_leaders: bool = True,
    include_ipos: bool = False,
) -> list[CandidatePool]:
    """Compose multi-pool candidate universe with source tags preserved."""
    seed_list = [t.upper() for t in (seed or DEFAULT_SEED)]
    by_ticker: dict[str, CandidatePool] = {}

    def _add(t: str, tag: str) -> None:
        if not t or is_junk_ticker(t):
            return
        cp = by_ticker.setdefault(t, CandidatePool(ticker=t))
        cp.add_tag(tag)

    for t in _pool_catalyst(days_back=days_back):
        _add(t, "double_beat")

    if include_movers:
        gainers, unusual = _pool_movers()
        for t in gainers:
            _add(t, "top_gainer")
        for t in unusual:
            _add(t, "high_volume_breakout")

    if include_sector_leaders:
        for t in _pool_sector_leaders():
            _add(t, "sector_leader")

    if include_ipos:
        for t in _pool_recent_ipos():
            _add(t, "recent_ipo_mover")

    for t in seed_list:
        _add(t, "manual_seed")

    def _priority(cp: CandidatePool) -> int:
        if "double_beat" in cp.source_tags:           return 0
        if "high_volume_breakout" in cp.source_tags:  return 1
        if "top_gainer" in cp.source_tags:            return 2
        if "sector_leader" in cp.source_tags:         return 3
        if "recent_ipo_mover" in cp.source_tags:      return 4
        return 5

    return sorted(by_ticker.values(), key=_priority)[:max_total]


# ===================================================================
# Backwards-compat shim
# ===================================================================

def generate(
    *,
    seed: Optional[Iterable[str]] = None,
    days_back: int = 10,
    max_tickers: int = 25,
    skip_movers: bool = False,
) -> list[str]:
    """Legacy interface — returns tickers only.

    For source-tagged output, use generate_pools().
    """
    pools = generate_pools(
        seed=seed,
        days_back=days_back,
        max_total=max_tickers,
        include_movers=not skip_movers,
        include_sector_leaders=False,
        include_ipos=False,
    )
    return [cp.ticker for cp in pools]
