"""Normalize one (date, ticker) into a fully populated CandidateRecord.

This is the single chokepoint between source adapters and scoring.
Each source contributes only fields within its authority.
"""
from __future__ import annotations

from datetime import datetime, timezone, date as _dt_date
from typing import Optional

from . import catalyst_verifier, scoring
from .adapters import alpaca, finnhub, fmp, massive
from .http_util import now_iso
from .regime import RegimeResult
from .schema import CandidateRecord, CatalystType
from .setup_classifier import SetupInputs, classify as classify_setup


# Coarse static sector/factor map for portfolio-fit. Replace with a richer
# source as needed (e.g., FMP profile, manual override).
_FACTOR_HINT = {
    # tech / ai-beta / semiconductors
    "NVDA": ("Tech", "AI"), "AMD": ("Tech", "AI"), "AVGO": ("Tech", "AI"),
    "SMCI": ("Tech", "AI"), "MU": ("Tech", "Semiconductor"),
    "MSFT": ("Tech", "AI"), "GOOGL": ("Tech", "AI"), "GOOG": ("Tech", "AI"),
    "META": ("Tech", "AI"), "PLTR": ("Tech", "AI"), "CRM": ("Tech", "SaaS"),
    "ORCL": ("Tech", "Database"), "NFLX": ("Tech", "Streaming"),
    # financial / crypto
    "COIN": ("Financial", "Crypto"), "LLY": ("Healthcare", "Pharma"),
    # consumer / ecommerce
    "AMZN": ("Consumer", "Ecommerce"), "TSLA": ("Auto", "Growth"),
    "WDAY": ("Tech", "SaaS"), "DDOG": ("Tech", "SaaS"), "CRWD": ("Tech", "Cyber"),
    # broad market / indices
    "SPY": ("Index", "Market"), "QQQ": ("Index", "Tech"), "IWM": ("Index", "SmallCap"),
    # healthcare
    "UNH": ("Healthcare", "Insurance"), "JNJ": ("Healthcare", "Pharma"),
    # industrials / defense
    "LMT": ("Industrial", "Defense"), "RTX": ("Industrial", "Defense"),
    # energy
    "XLE": ("Energy", "Oil"), "COP": ("Energy", "Oil"),
    # financials
    "JPM": ("Financial", "Banking"), "BAC": ("Financial", "Banking"),
}


def _sector_for(ticker: str) -> tuple[Optional[str], Optional[str]]:
    """Return (sector, factor) for a ticker, defaulting to None."""
    return _FACTOR_HINT.get(ticker.upper(), (None, None))


def _date_str(d: Optional[str] = None) -> str:
    return d or datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------- catalyst stage ----------

def _resolve_catalyst(ticker: str, r: CandidateRecord) -> tuple[bool, str, Optional[dict]]:
    """Return (catalyst_pass, catalyst_type, supporting_payload)."""
    verified = catalyst_verifier.verify(ticker, days_back=14, allow_sympathy=True)
    if verified.catalyst_type != CatalystType.NONE.value:
        if verified.catalyst_type == CatalystType.SECTOR_SYMPATHY_CONFIRMED.value:
            r.sector_sympathy_flag = True
        elif verified.catalyst_type == CatalystType.GUIDANCE_RAISE.value:
            r.guidance_raise_flag = True
        elif verified.catalyst_type == CatalystType.MAJOR_CORPORATE_EVENT.value:
            r.major_event_flag = True
        r.catalyst_notes = verified.catalyst_headline

    try:
        ec = finnhub.earnings_calendar(ticker, days_back=14)
    except Exception as e:
        r.add_error(f"finnhub.earnings_calendar: {e}")
        ec = []
    try:
        news = finnhub.company_news(ticker, days_back=10)
    except Exception as e:
        r.add_error(f"finnhub.company_news: {e}")
        news = []

    db = finnhub.detect_double_beat(ec)
    if db:
        r.eps_actual = db.get("epsActual"); r.eps_estimate = db.get("epsEstimate")
        r.revenue_actual = db.get("revenueActual"); r.revenue_estimate = db.get("revenueEstimate")
        r.earnings_date = db.get("date")
        # also check for guidance raise → upgrade label
        if finnhub.detect_guidance_raise(news):
            r.guidance_raise_flag = True
        return True, CatalystType.EARNINGS_DOUBLE_BEAT.value, db

    if verified.catalyst_type == CatalystType.SECTOR_SYMPATHY_CONFIRMED.value:
        return True, verified.catalyst_type, {
            "headline": verified.catalyst_headline,
            "date": verified.catalyst_date,
            "source": verified.catalyst_source,
            "confidence": verified.catalyst_confidence,
        }

    if finnhub.detect_guidance_raise(news):
        r.guidance_raise_flag = True
        return True, CatalystType.GUIDANCE_RAISE.value, None

    ev = finnhub.detect_major_event(news)
    if ev:
        r.major_event_flag = True
        r.catalyst_notes = f"{ev.get('_event_category')}: {ev.get('headline')}"
        return True, CatalystType.MAJOR_CORPORATE_EVENT.value, ev

    if verified.catalyst_type != CatalystType.NONE.value:
        return True, verified.catalyst_type, {
            "headline": verified.catalyst_headline,
            "date": verified.catalyst_date,
            "source": verified.catalyst_source,
            "confidence": verified.catalyst_confidence,
        }

    return False, CatalystType.NONE.value, None


# ---------- structure stage ----------

def _populate_structure(ticker: str, r: CandidateRecord) -> Optional[list[dict]]:
    try:
        bars = massive.daily_aggregates(ticker, lookback_days=120)
    except Exception as e:
        r.add_error(f"massive.daily_aggregates: {e}")
        return None
    if len(bars) < 25:
        r.add_error(f"only {len(bars)} bars from Massive")
        return bars

    last = bars[-1]; prev = bars[-2]
    r.prev_close = float(prev["c"]); r.last_close = float(last["c"])
    r.twenty_day_ma  = massive.sma(bars, 20)
    r.fifty_day_ma   = massive.sma(bars, 50)
    r.twenty_day_high = massive.rolling_high(bars, 20)
    r.atr_abs        = massive.wilder_atr(bars, 14)
    if r.atr_abs and r.last_close:
        r.atr_pct = round(r.atr_abs / r.last_close, 4)
    avgv = massive.avg_volume(bars, 20)
    if avgv and float(last["v"]) > 0:
        r.volume_ratio = round(float(last["v"]) / avgv, 2)
    r.dollar_volume_m = round(float(last["v"]) * r.last_close / 1_000_000, 2)
    r.five_day_change_pct = massive.pct_change(bars, 5)
    r.realized_vol_5d = massive.realized_vol(bars, 5)
    r.rsi_14 = massive.rsi(bars, 14)
    if r.atr_abs and r.twenty_day_ma and r.last_close:
        r.extension_vs_20d_ma_atr = round((r.last_close - r.twenty_day_ma) / r.atr_abs, 2)

    # post-earnings geometry (for PED / STN setups)
    _populate_post_earnings_geometry(r, bars)
    return bars


def _populate_post_earnings_geometry(r: CandidateRecord, bars: list[dict]) -> None:
    """Compute post_earnings_high, pre_event_close, sold_off_since_earnings, reclaim_on_volume."""
    if not r.earnings_date:
        return
    try:
        d_e = datetime.fromisoformat(r.earnings_date).date()
        d_now = datetime.fromisoformat(r.date).date()
        days_since = (d_now - d_e).days
        if days_since < -1 or days_since > 10:  # earnings must be recent
            return
    except Exception:
        return

    # find bar index for earnings date
    earnings_idx = None
    for i, bar in enumerate(bars):
        try:
            bar_date = datetime.fromtimestamp(int(bar.get("t", 0)) / 1000, tz=timezone.utc).date()
            if bar_date == d_e:
                earnings_idx = i
                break
        except Exception:
            continue

    if earnings_idx is None or earnings_idx < 1:
        return

    # pre-event close
    r.pre_event_close = float(bars[earnings_idx - 1]["c"])

    # post-earnings high over next 5 bars
    post_high = max((float(b["h"]) for b in bars[earnings_idx:min(earnings_idx + 5, len(bars))]), default=None)
    if post_high:
        r.post_earnings_high = post_high

    # sold off: if the high right after earnings is >1% above open, but close that day is down, or next day opens lower
    if earnings_idx + 1 < len(bars):
        earn_bar = bars[earnings_idx]
        next_bar = bars[earnings_idx + 1]
        earn_close = float(earn_bar["c"]); next_open = float(next_bar["o"])
        if earn_close > r.pre_event_close and next_open < earn_close:
            r.sold_off_since_earnings = True

    # reclaim: if current close > pre_event_close AND today's volume > 20d avg
    if r.last_close and r.last_close > r.pre_event_close:
        avgv = massive.avg_volume(bars, 20)
        today_vol = float(bars[-1]["v"])
        if avgv and today_vol > avgv * 1.1:
            r.reclaim_on_volume = True


# ---------- analyst sentiment ----------

def _populate_fmp(ticker: str, r: CandidateRecord) -> None:
    gc = fmp.grades_consensus(ticker)
    rs = fmp.ratings_snapshot(ticker)
    pt = fmp.price_target_consensus(ticker)
    pts = fmp.price_target_summary(ticker)
    any_data = False
    if gc:
        any_data = True
        r.fmp_consensus = gc.get("consensus")
        r.fmp_buy_count = (gc.get("strongBuy") or 0) + (gc.get("buy") or 0)
        r.fmp_hold_count = gc.get("hold")
        r.fmp_sell_count = (gc.get("sell") or 0) + (gc.get("strongSell") or 0)
    if pt:
        any_data = True
        r.fmp_target_consensus = pt.get("targetConsensus")
        r.fmp_target_high = pt.get("targetHigh")
        r.fmp_target_low = pt.get("targetLow")
    if pts:
        any_data = True
        r.fmp_target_activity_last_month = pts.get("lastMonth") or pts.get("lastMonthCount")
    if rs and not r.fmp_consensus:
        any_data = True
        r.fmp_consensus = rs.get("rating")
    r.fmp_data_available = any_data


# ---------- live confirmation ----------

def _populate_alpaca_live(ticker: str, r: CandidateRecord) -> None:
    q = alpaca.latest_quote(ticker)
    if not q:
        r.alpaca_live_context_note = "no quote"
        return
    bid = q.get("bp") or q.get("bid_price"); ask = q.get("ap") or q.get("ask_price")
    if bid: r.alpaca_bid = float(bid)
    if ask: r.alpaca_ask = float(ask)
    if r.alpaca_bid and r.alpaca_ask:
        mid = (r.alpaca_bid + r.alpaca_ask) / 2
        r.alpaca_last = mid
        if mid > 0:
            r.alpaca_spread_bps = round(10000 * (r.alpaca_ask - r.alpaca_bid) / mid, 2)
    r.alpaca_quote_ts = q.get("t") or q.get("timestamp")
    # conflict: live mid >2% from Massive last_close
    if r.last_close and r.alpaca_last:
        deviation = abs(r.alpaca_last - r.last_close) / r.last_close
        if deviation > 0.02:
            r.alpaca_live_conflict_flag = True
            r.alpaca_live_context_note = f"alpaca/massive deviation {deviation:.2%}"


# ---------- portfolio fit ----------

def _populate_portfolio_fit(ticker: str, r: CandidateRecord) -> None:
    held = []
    for p in alpaca.positions():
        sym = (p.get("symbol") or "").upper()
        if sym:
            held.append(sym)
    sec, fac = _sector_for(ticker.upper())
    r.sector = sec; r.factor = fac
    held_specs = {_sector_for(h) for h in held}
    held_sectors = {s for s, _ in held_specs if s}
    held_factors = {f for _, f in held_specs if f}
    if sec and sec in held_sectors:
        r.overlaps_existing_sector = True
    if fac and fac in held_factors:
        r.overlaps_existing_factor = True
    related = [h for h in held if _sector_for(h) == (sec, fac) and (sec or fac)]
    r.existing_related_positions = related
    if r.overlaps_existing_sector and r.overlaps_existing_factor:
        r.portfolio_fit_bucket = "doubles"
    elif sec and not r.overlaps_existing_sector:
        r.portfolio_fit_bucket = "missing"
    else:
        r.portfolio_fit_bucket = "neutral"


# ---------- sector support ----------

_SECTOR_ETFS = ["XLK", "XLY", "XLV", "XLE", "XLF", "XLI", "XLP", "XLB", "XLU", "XLRE", "XLC"]
_TICKER_TO_ETF = {
    "Tech": "XLK", "Consumer": "XLY", "Health": "XLV", "Energy": "XLE",
    "Financial": "XLF", "Industrial": "XLI", "Staples": "XLP",
    "Materials": "XLB", "Utilities": "XLU", "RealEstate": "XLRE", "Comm": "XLC",
}


def _populate_sector_support(r: CandidateRecord) -> None:
    """Set sector_score in 0..10 by sector ETF 5d-return percentile."""
    if not r.sector:
        return
    etf = _TICKER_TO_ETF.get(r.sector)
    if not etf:
        return
    moves: dict[str, float] = {}
    for e in _SECTOR_ETFS:
        try:
            bars = massive.daily_aggregates(e, lookback_days=15)
            mv = massive.pct_change(bars, 5)
            if mv is not None:
                moves[e] = mv
        except Exception:
            continue
    if etf not in moves or not moves:
        return
    sorted_etfs = sorted(moves.items(), key=lambda kv: kv[1])
    rank = next(i for i, (e, _) in enumerate(sorted_etfs) if e == etf)
    pct = rank / max(len(sorted_etfs) - 1, 1)
    r.sector_score = round(10 * pct, 2)


# ---------- main ----------

def normalize(
    ticker: str,
    *,
    date: Optional[str] = None,
    regime: Optional[RegimeResult] = None,
    include_alpaca_live: bool = False,
    nav_usd: Optional[float] = None,
) -> CandidateRecord:
    r = CandidateRecord(date=_date_str(date), ticker=ticker.upper(), fetch_timestamp=now_iso())

    # 1. catalyst (Finnhub)
    passed, cat_type, _ = _resolve_catalyst(ticker, r)
    r.catalyst_pass = passed
    r.verified_catalyst_type = cat_type
    r.catalyst_source = "finnhub" if passed else None
    r.sources_used.append("finnhub")

    # 2. structure (Massive)
    bars = _populate_structure(ticker, r)
    r.sources_used.append("massive")

    # 3. analyst sentiment (FMP) — secondary
    try:
        _populate_fmp(ticker, r)
        r.sources_used.append("fmp")
    except Exception as e:
        r.add_error(f"fmp: {e}")

    # 4. portfolio fit (Alpaca paper book)
    try:
        _populate_portfolio_fit(ticker, r)
        r.sources_used.append("alpaca_positions")
    except Exception as e:
        r.add_error(f"alpaca_positions: {e}")

    # 5. sector support
    try:
        _populate_sector_support(r)
    except Exception as e:
        r.add_error(f"sector_support: {e}")

    # 6. live confirmation (Alpaca, optional — only near open)
    if include_alpaca_live:
        try:
            _populate_alpaca_live(ticker, r)
            r.sources_used.append("alpaca")
        except Exception as e:
            r.add_error(f"alpaca: {e}")

    # 7. regime attached
    if regime is not None:
        r.regime = regime.regime
        r.spy_close = regime.spy_close
        r.spy_20d_ma = regime.spy_20d_ma
        r.spy_50d_ma = regime.spy_50d_ma
        r.vix_close = regime.vix_close

    # 8. setup classification
    days_since_earnings = None
    if r.earnings_date:
        try:
            d_e = datetime.fromisoformat(r.earnings_date).date()
            d_now = datetime.fromisoformat(r.date).date()
            days_since_earnings = (d_now - d_e).days
        except Exception:
            pass
    si = SetupInputs(
        last_close=r.last_close, prev_close=r.prev_close,
        twenty_day_high=r.twenty_day_high, twenty_day_ma=r.twenty_day_ma,
        fifty_day_ma=r.fifty_day_ma, atr_abs=r.atr_abs, atr_pct=r.atr_pct,
        volume_ratio=r.volume_ratio, rsi_14=r.rsi_14,
        five_day_change_pct=r.five_day_change_pct,
        earnings_double_beat_recent=(r.verified_catalyst_type == CatalystType.EARNINGS_DOUBLE_BEAT.value),
        days_since_earnings=days_since_earnings,
        sector_sympathy_confirmed=r.sector_sympathy_flag,
        catalyst_in_last_5d=passed,
        post_earnings_high=r.post_earnings_high,
        pre_event_close=r.pre_event_close,
        sold_off_since_earnings=r.sold_off_since_earnings,
        reclaim_on_volume=r.reclaim_on_volume,
    )
    state, reason = classify_setup(si)
    r.setup_state = state
    r.setup_state_reason = reason
    if state == "overextended_chase":
        r.overextended_flag = True

    # 9. score + class
    scoring.score(r)

    # 10. sizing
    if nav_usd is not None and nav_usd > 0:
        scoring.attach_sizing(r, nav_usd)

    return r
