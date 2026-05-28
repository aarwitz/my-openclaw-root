"""Broad-market liquid-movers scanner (Priority 1).

Returns ranked candidates across the full US market with hard liquidity filters.

Data flow:
  1. Discover universe via Massive snapshot (gainers/losers/most-actives) +
     curated ETF list + optional seed.
  2. Enrich each candidate with daily aggregates from Massive (ATR/MA/vol).
  3. Pull single snapshot for current-day OHLCV + spread context.
  4. Apply hard filters (price, dollar-vol, spread, junk).
  5. Compute ranked buckets (gainers_1d, gainers_5d, dollar_volume,
     unusual_volume, sector_etfs, ipo_movers).

Source authority:
  Finnhub catalyst tagging is layered by `catalyst_verifier.py`, NOT here.
  This module produces *liquid mover* truth only — no catalyst gate.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Iterable, Optional

from .adapters import massive
from .universe import (
    DEFAULT_MAX_SPREAD_PCT,
    DEFAULT_MIN_DOLLAR_VOL_M,
    DEFAULT_MIN_PRICE,
    SECTOR_ETFS,
    etf_universe,
    is_etf,
    is_junk_ticker,
)


# ===================================================================
# Data class — one record per liquid mover.
# ===================================================================

@dataclass
class LiquidMover:
    ticker: str
    asset_type: str = "stock"           # "stock" | "etf"
    last_price: Optional[float] = None
    pct_change_1d: Optional[float] = None
    pct_change_5d: Optional[float] = None
    pct_change_from_open: Optional[float] = None
    gap_pct: Optional[float] = None
    high_of_day_distance_pct: Optional[float] = None
    low_of_day_distance_pct: Optional[float] = None
    volume: Optional[float] = None
    avg_volume_20d: Optional[float] = None
    volume_ratio: Optional[float] = None
    dollar_volume: Optional[float] = None
    market_cap: Optional[float] = None  # only when asset_type=stock & known
    sector: Optional[str] = None
    industry: Optional[str] = None
    spread_pct: Optional[float] = None
    atr_14: Optional[float] = None
    atr_pct: Optional[float] = None
    above_20dma: Optional[bool] = None
    above_50dma: Optional[bool] = None
    above_200dma: Optional[bool] = None
    ipo_date: Optional[str] = None
    days_since_ipo: Optional[int] = None
    has_options: Optional[bool] = None
    timestamp: Optional[str] = None

    # ---- ranking add-ons ----
    extension_flag: bool = False        # >2 ATR above 20d MA
    source_tags: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


# ===================================================================
# Discovery — build candidate universe.
# ===================================================================

def _last_trading_day(offset: int = 0) -> str:
    """Return last completed trading day (no holidays calendar — basic Mon-Fri logic).

    offset=0 → most recent trading day, offset=1 → day before, etc.
    """
    from datetime import datetime, timedelta, timezone
    d = datetime.now(timezone.utc).date()
    skipped = 0
    while skipped <= offset:
        d = d - timedelta(days=1)
        if d.weekday() < 5:  # 0=Mon..4=Fri
            skipped += 1
            if skipped > offset:
                break
    return d.isoformat()


def _discover_from_grouped() -> tuple[list[str], list[str], dict[str, dict]]:
    """Use grouped_daily for broad-market discovery.

    Returns (top_gainers, unusual_volume, ticker→bar_record).
    Compares yesterday vs day-before for daily move + previous-day volume baseline.
    """
    today_d = _last_trading_day(0)
    prev_d = _last_trading_day(1)
    try:
        today = massive.grouped_daily(today_d)
        yesterday = massive.grouped_daily(prev_d)
    except Exception:
        return [], [], {}

    prev_map = {b.get("T"): b for b in yesterday if b.get("T")}
    today_map = {b.get("T"): b for b in today if b.get("T")}

    gainer_scored: list[tuple[str, float]] = []
    vol_scored: list[tuple[str, float]] = []

    for tk, bar in today_map.items():
        if not tk or is_junk_ticker(tk):
            continue
        try:
            c = float(bar.get("c") or 0)
            v = float(bar.get("v") or 0)
            if c < DEFAULT_MIN_PRICE or v <= 0:
                continue
            dv = (v * c) / 1e6
            if dv < DEFAULT_MIN_DOLLAR_VOL_M:
                continue
            pb = prev_map.get(tk)
            if not pb:
                continue
            pc = float(pb.get("c") or 0)
            pv = float(pb.get("v") or 0)
            if pc <= 0 or pv <= 0:
                continue
            pct = (c - pc) / pc
            ratio = v / pv
            gainer_scored.append((tk, pct))
            if ratio >= 2.0:
                vol_scored.append((tk, ratio))
        except Exception:
            continue

    gainer_scored.sort(key=lambda x: -x[1])
    vol_scored.sort(key=lambda x: -x[1])
    return (
        [t for t, _ in gainer_scored[:75]],
        [t for t, _ in vol_scored[:75]],
        today_map,
    )


def _discover_from_snapshots() -> dict[str, list[str]]:
    """Legacy snapshot-based discovery (requires authorized Polygon snapshot tier).

    Falls back to grouped_daily-based discovery automatically. Kept for tests.
    """
    gainers, unusual, _ = _discover_from_grouped()
    return {"top_gainer": gainers, "high_volume_breakout": unusual}


def _unusual_volume_from_all(min_vol_ratio: float = 3.0, min_dollar_m: float = 25.0) -> list[str]:
    """Backwards-compat — derive from grouped_daily."""
    _, unusual, _ = _discover_from_grouped()
    return unusual


# ===================================================================
# Enrichment — fill LiquidMover fields from daily aggs + snapshot + ref.
# ===================================================================

def _enrich(
    ticker: str,
    source_tags: list[str],
    grouped_today_map: Optional[dict[str, dict]] = None,
    grouped_prev_map: Optional[dict[str, dict]] = None,
) -> Optional[LiquidMover]:
    """Pull daily bars + snapshot + reference data; build LiquidMover or return None on failure."""
    grouped_bar = (grouped_today_map or {}).get(ticker)
    prev_grouped = (grouped_prev_map or {}).get(ticker)
    try:
        bars = massive.daily_aggregates(ticker, lookback_days=300)
    except Exception:
        bars = []
    if bars and len(bars) >= 21:
        last = bars[-1]
        prev = bars[-2] if len(bars) >= 2 else None

        last_close = float(last.get("c") or 0)
        if last_close < DEFAULT_MIN_PRICE:
            return None

        open_ = float(last.get("o") or 0)
        high = float(last.get("h") or 0)
        low = float(last.get("l") or 0)
        vol = float(last.get("v") or 0)

        avg_v_20 = massive.avg_volume(bars, 20) or 0
        dollar_vol = (vol * last_close) / 1e6  # millions
        if dollar_vol < DEFAULT_MIN_DOLLAR_VOL_M:
            return None

        atr = massive.wilder_atr(bars, 14)
        atr_pct = (atr / last_close) if (atr and last_close > 0) else None
        sma20 = massive.sma(bars, 20)
        sma50 = massive.sma(bars, 50)
        sma200 = massive.sma(bars, 200)

        pct_1d = ((last_close / float(prev["c"])) - 1) if prev and float(prev.get("c") or 0) > 0 else None
        pct_5d = massive.pct_change(bars, 5)
        pct_from_open = ((last_close / open_) - 1) if open_ > 0 else None
        gap = ((open_ / float(prev["c"])) - 1) if prev and float(prev.get("c") or 0) > 0 else None
        hod_dist = ((high - last_close) / last_close) if last_close > 0 else None
        lod_dist = ((last_close - low) / last_close) if last_close > 0 else None
        vol_ratio = (vol / avg_v_20) if avg_v_20 > 0 else None

        extension = False
        if atr and sma20 and atr > 0:
            extension = (last_close - sma20) / atr > 2.0
    else:
        if not grouped_bar:
            return None
        last_close = float(grouped_bar.get("c") or 0)
        if last_close < DEFAULT_MIN_PRICE:
            return None
        open_ = float(grouped_bar.get("o") or 0)
        high = float(grouped_bar.get("h") or 0)
        low = float(grouped_bar.get("l") or 0)
        vol = float(grouped_bar.get("v") or 0)
        dollar_vol = (vol * last_close) / 1e6
        if dollar_vol < DEFAULT_MIN_DOLLAR_VOL_M:
            return None
        prev_close = float(prev_grouped.get("c") or 0) if prev_grouped else 0
        prev_vol = float(prev_grouped.get("v") or 0) if prev_grouped else 0
        pct_1d = ((last_close / prev_close) - 1) if prev_close > 0 else None
        pct_5d = None
        pct_from_open = ((last_close / open_) - 1) if open_ > 0 else None
        gap = ((open_ / prev_close) - 1) if prev_close > 0 else None
        hod_dist = ((high - last_close) / last_close) if last_close > 0 else None
        lod_dist = ((last_close - low) / last_close) if last_close > 0 else None
        vol_ratio = (vol / prev_vol) if prev_vol > 0 else None
        atr = None
        atr_pct = None
        sma20 = None
        sma50 = None
        sma200 = None
        extension = False

    # --- snapshot for spread (best-effort, may fail in off-hours or unauthorized tier) ---
    spread_pct = None
    try:
        snap = massive.snapshot_ticker(ticker)
        if snap:
            lq = snap.get("lastQuote") or {}
            bid = float(lq.get("p") or 0)
            ask = float(lq.get("P") or 0)
            if bid > 0 and ask > 0 and ask >= bid:
                mid = (bid + ask) / 2
                spread_pct = (ask - bid) / mid if mid > 0 else None
    except Exception:
        pass

    # spread filter is HARD-CAP only when we actually got a value
    if spread_pct is not None and spread_pct > DEFAULT_MAX_SPREAD_PCT:
        return None  # too wide, drop

    # --- reference data (sector, market cap, ipo date) ---
    sector = None; industry = None; mcap = None; ipo_date = None; days_since_ipo = None
    asset_type = "etf" if is_etf(ticker) else "stock"
    if asset_type == "stock":
        try:
            ref = massive.ticker_overview(ticker)
            if ref:
                sector = ref.get("sic_description") or ref.get("type")
                industry = ref.get("sic_description")
                mcap = ref.get("market_cap")
                ipo_date = ref.get("list_date")
                if ipo_date:
                    try:
                        d_ipo = datetime.fromisoformat(ipo_date).date()
                        days_since_ipo = (datetime.now(timezone.utc).date() - d_ipo).days
                    except Exception:
                        pass
        except Exception:
            pass

    return LiquidMover(
        ticker=ticker.upper(),
        asset_type=asset_type,
        last_price=last_close,
        pct_change_1d=pct_1d,
        pct_change_5d=pct_5d,
        pct_change_from_open=pct_from_open,
        gap_pct=gap,
        high_of_day_distance_pct=hod_dist,
        low_of_day_distance_pct=lod_dist,
        volume=vol,
        avg_volume_20d=avg_v_20,
        volume_ratio=vol_ratio,
        dollar_volume=dollar_vol,
        market_cap=mcap,
        sector=sector,
        industry=industry,
        spread_pct=spread_pct,
        atr_14=atr,
        atr_pct=atr_pct,
        above_20dma=(last_close > sma20) if sma20 else None,
        above_50dma=(last_close > sma50) if sma50 else None,
        above_200dma=(last_close > sma200) if sma200 else None,
        ipo_date=ipo_date,
        days_since_ipo=days_since_ipo,
        extension_flag=extension,
        source_tags=list(source_tags),
        timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


# ===================================================================
# Public API — scan_market.
# ===================================================================

def scan_market(
    *,
    extra_seed: Optional[Iterable[str]] = None,
    include_etfs: bool = True,
    include_unusual_vol: bool = True,
    workers: int = 8,
    max_total: int = 250,
) -> list[LiquidMover]:
    """Run a full broad-market liquid-mover scan.

    Returns a flat list of LiquidMover; each carries its source_tags (e.g.
    ["top_gainer"], ["unusual_volume"], ["etf_sector"], ["seed"]).

    Use bucket helpers below to slice into ranked lists.
    """
    # ---- discovery ----
    src_to_tickers: dict[str, list[str]] = {}
    grouped_gainers, grouped_unusual, grouped_today = _discover_from_grouped()
    prev_day = _last_trading_day(1)
    try:
        grouped_prev_rows = massive.grouped_daily(prev_day)
    except Exception:
        grouped_prev_rows = []
    grouped_prev_map = {b.get("T"): b for b in grouped_prev_rows if b.get("T")}

    snaps = {"top_gainer": grouped_gainers, "high_volume_breakout": grouped_unusual}
    src_to_tickers.update(snaps)

    if include_unusual_vol:
        src_to_tickers["unusual_volume"] = grouped_unusual

    if include_etfs:
        src_to_tickers["etf_universe"] = etf_universe()
        src_to_tickers["etf_sector"] = list(SECTOR_ETFS.values())

    if extra_seed:
        src_to_tickers["seed"] = [t.upper() for t in extra_seed]

    # invert to ticker -> [source_tags], dedup
    ticker_tags: dict[str, list[str]] = {}
    for tag, tickers in src_to_tickers.items():
        for t in tickers:
            if not t or is_junk_ticker(t):
                continue
            ticker_tags.setdefault(t.upper(), []).append(tag)
            if len(ticker_tags) >= max_total:
                break
        if len(ticker_tags) >= max_total:
            break

    # ---- enrichment ----
    out: list[LiquidMover] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_enrich, t, tags, grouped_today, grouped_prev_map): t
            for t, tags in ticker_tags.items()
        }
        try:
            completed = as_completed(futures, timeout=120)
            for f in completed:
                try:
                    lm = f.result(timeout=15)
                    if lm is not None:
                        out.append(lm)
                except Exception:
                    continue
        except Exception:
            for f in futures:
                if f.done():
                    try:
                        lm = f.result(timeout=0)
                        if lm is not None:
                            out.append(lm)
                    except Exception:
                        continue
    return out


# ===================================================================
# Ranked-bucket helpers — slice the flat scan into named lists.
# ===================================================================

def top_gainers_1d(movers: list[LiquidMover], n: int = 25) -> list[LiquidMover]:
    return sorted(
        [m for m in movers if m.pct_change_1d is not None],
        key=lambda m: -m.pct_change_1d,
    )[:n]


def top_gainers_5d(movers: list[LiquidMover], n: int = 25) -> list[LiquidMover]:
    return sorted(
        [m for m in movers if m.pct_change_5d is not None],
        key=lambda m: -m.pct_change_5d,
    )[:n]


def top_dollar_volume(movers: list[LiquidMover], n: int = 25) -> list[LiquidMover]:
    return sorted(
        [m for m in movers if m.dollar_volume is not None],
        key=lambda m: -m.dollar_volume,
    )[:n]


def unusual_volume_leaders(movers: list[LiquidMover], n: int = 25, min_ratio: float = 2.0) -> list[LiquidMover]:
    return sorted(
        [m for m in movers if m.volume_ratio is not None and m.volume_ratio >= min_ratio],
        key=lambda m: -m.volume_ratio,
    )[:n]


def recent_ipo_movers(movers: list[LiquidMover], n: int = 15, max_days_since: int = 365) -> list[LiquidMover]:
    return sorted(
        [m for m in movers if m.days_since_ipo is not None and m.days_since_ipo <= max_days_since],
        key=lambda m: (m.days_since_ipo or 0),
    )[:n]


def sector_etf_leaders(movers: list[LiquidMover], n: int = 11) -> list[LiquidMover]:
    sector_tickers = set(SECTOR_ETFS.values())
    return sorted(
        [m for m in movers if m.ticker in sector_tickers and m.pct_change_5d is not None],
        key=lambda m: -m.pct_change_5d,
    )[:n]


def thematic_etf_leaders(movers: list[LiquidMover], n: int = 15) -> list[LiquidMover]:
    return sorted(
        [m for m in movers if m.asset_type == "etf" and m.pct_change_5d is not None
         and m.ticker not in set(SECTOR_ETFS.values())],
        key=lambda m: -m.pct_change_5d,
    )[:n]


def ranked_buckets(movers: list[LiquidMover]) -> dict[str, list[dict]]:
    """Convenience: return all standard buckets as JSON-friendly dicts."""
    return {
        "top_gainers_1d":      [m.as_dict() for m in top_gainers_1d(movers)],
        "top_gainers_5d":      [m.as_dict() for m in top_gainers_5d(movers)],
        "top_dollar_volume":   [m.as_dict() for m in top_dollar_volume(movers)],
        "unusual_volume":      [m.as_dict() for m in unusual_volume_leaders(movers)],
        "recent_ipo_movers":   [m.as_dict() for m in recent_ipo_movers(movers)],
        "sector_etf_leaders":  [m.as_dict() for m in sector_etf_leaders(movers)],
        "thematic_etf_leaders":[m.as_dict() for m in thematic_etf_leaders(movers)],
    }
