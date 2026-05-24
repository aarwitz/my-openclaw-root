from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

from . import alpha_ranker, cache_manager, candidate_gen, regime as regime_mod
from .adapters import alpaca, finnhub, massive
from .catalyst_verifier import verify_many, CatalystResult
from .market_scanner import LiquidMover, scan_market
from .universe import sector_etf_for, is_junk_ticker


DEFAULT_CASH_HURDLE_APR = 0.03
DEFAULT_OBJECTIVE = "beat_spy_this_week"


@dataclass
class EntryPlan:
    order_type: str
    limit_price: Optional[float]
    acceptable_chase_to: Optional[float]
    trigger: str
    invalidator: str
    stop: Optional[float]
    holding_horizon: str
    confidence: str
    reference_price: Optional[float] = None
    price_source: Optional[str] = None


@dataclass
class RankedIdea:
    ticker: str
    action: str
    forward_alpha_score: float
    confidence: float
    dominant_risk: Optional[str]
    recommendation_class: str
    replacement_delta_score: Optional[float] = None
    reason: Optional[str] = None
    entry_plan: Optional[dict] = None
    components: dict = field(default_factory=dict)
    penalties: dict = field(default_factory=dict)
    source_tags: list[str] = field(default_factory=list)


@dataclass
class RotationPlan:
    sell: Optional[dict]
    buy: Optional[dict]
    rotation_delta_score: float
    cash_beats_trade: bool
    execution_notes: str



def _alpaca_positions_map() -> dict[str, dict]:
    out = {}
    for p in alpaca.positions():
        sym = (p.get("symbol") or "").upper()
        if sym:
            out[sym] = p
    return out



def _benchmark_stats() -> dict:
    acct = alpaca.account()
    positions = alpaca.positions()
    equity = float(acct.get("equity") or 0)
    last_equity = float(acct.get("last_equity") or 0)
    portfolio_return_day = ((equity - last_equity) / last_equity) if last_equity else None
    return {
        "equity": equity,
        "last_equity": last_equity,
        "buying_power": float(acct.get("buying_power") or 0),
        "cash": float(acct.get("cash") or 0),
        "positions": positions,
        "portfolio_return_day": portfolio_return_day,
    }



def _live_price(ticker: str, fallback: Optional[float]) -> tuple[Optional[float], str]:
    q = alpaca.latest_quote(ticker)
    if q:
        bid = q.get('bp')
        ask = q.get('ap')
        if bid and ask and float(bid) > 0 and float(ask) > 0:
            return round((float(bid) + float(ask)) / 2, 4), 'alpaca_mid'
        if q.get('ap'):
            return float(q.get('ap')), 'alpaca_ask'
        if q.get('bp'):
            return float(q.get('bp')), 'alpaca_bid'
    try:
        fq = finnhub.quote(ticker)
        if fq and fq.get('c'):
            return float(fq.get('c')), 'finnhub_last'
    except Exception:
        pass
    return fallback, 'daily_close_fallback'


def _limit_plan(m: LiquidMover, score: alpha_ranker.AlphaScore) -> EntryPlan:
    price, price_source = _live_price(m.ticker, m.last_price)
    atr = m.atr_14 or 0
    spread_pct = m.spread_pct or 0
    liquid = (m.dollar_volume or 0) >= 500 and spread_pct <= 0.003
    near_hod = (m.high_of_day_distance_pct or 0) <= 0.003
    if liquid and near_hod:
        order_type = "market"
        limit_price = None
        chase = None
    else:
        order_type = "limit"
        if price is None:
            limit_price = None
            chase = None
        else:
            offset_in = min(max(spread_pct * 0.5, 0.001), 0.004)
            offset_out = min(max(spread_pct, 0.002), 0.008)
            limit_price = round(price * (1 - offset_in), 2)
            chase = round(price * (1 + offset_out), 2)
            if limit_price > price * 1.01 or chase > price * 1.02:
                raise ValueError(f"buy limit sanity check failed for {m.ticker}: limit/chase disconnected from live {round(price,2)}")
    stop = round(price - 1.5 * atr, 2) if price and atr else None
    trigger = "holds above intraday VWAP / stays green vs SPY" if order_type == "limit" else "relative strength persists"
    invalidator = (
        f"loses {round(price - max(atr * 0.75, price * 0.01), 2)} on 15m close"
        if price else "relative strength breaks"
    )
    conf = "high" if score.score >= 65 else "medium" if score.score >= 50 else "low"
    return EntryPlan(order_type, limit_price, chase, trigger, invalidator, stop, "1-5 trading days", conf, round(price, 2) if price else None, price_source)



def _rec_class(score: float, has_catalyst: bool) -> str:
    if not has_catalyst:
        return "watch_only"
    if score >= 75:
        return "buy_ready"
    if score >= 60:
        return "conditional_buy"
    if score >= 40:
        return "watch_only"
    return "avoid"



def _reason(m: LiquidMover, s: alpha_ranker.AlphaScore, c: Optional[CatalystResult]) -> str:
    bits = []
    if c and c.catalyst_type != "none":
        bits.append(c.catalyst_type)
    if m.pct_change_5d is not None:
        bits.append(f"5d {m.pct_change_5d*100:.1f}%")
    if m.volume_ratio is not None:
        bits.append(f"vol {m.volume_ratio:.2f}x")
    if s.dominant_risk and s.dominant_risk != "none":
        bits.append(f"risk {s.dominant_risk}")
    return ", ".join(bits)



def _targeted_scan(universe: list[str]) -> list[LiquidMover]:
    out = []
    for t in universe:
        if is_junk_ticker(t) or len(t) > 5:
            continue
        bars = massive.daily_aggregates(t, lookback_days=300)
        if not bars or len(bars) < 21:
            continue
        last = bars[-1]
        prev = bars[-2] if len(bars) >= 2 else None
        last_close = float(last.get('c') or 0)
        if last_close <= 0:
            continue
        vol = float(last.get('v') or 0)
        avg_v_20 = massive.avg_volume(bars, 20) or 0
        atr = massive.wilder_atr(bars, 14)
        sma20 = massive.sma(bars, 20)
        sma50 = massive.sma(bars, 50)
        sma200 = massive.sma(bars, 200)
        pct_1d = ((last_close / float(prev['c'])) - 1) if prev and float(prev.get('c') or 0) > 0 else None
        pct_5d = massive.pct_change(bars, 5)
        open_ = float(last.get('o') or 0)
        high = float(last.get('h') or 0)
        low = float(last.get('l') or 0)
        gap = ((open_ / float(prev['c'])) - 1) if prev and float(prev.get('c') or 0) > 0 else None
        if ((vol * last_close) / 1e6) < 25:
            continue
        out.append(LiquidMover(
            ticker=t,
            asset_type='etf' if t in {'SPY','IWM','QQQ','TQQQ','XLE','XLK','XLI','XLV','XLF','XLP','XLY','XLC','TLT','ROBO'} else 'stock',
            last_price=last_close,
            pct_change_1d=pct_1d,
            pct_change_5d=pct_5d,
            pct_change_from_open=((last_close/open_)-1) if open_>0 else None,
            gap_pct=gap,
            high_of_day_distance_pct=((high-last_close)/last_close) if last_close>0 else None,
            low_of_day_distance_pct=((last_close-low)/last_close) if last_close>0 else None,
            volume=vol,
            avg_volume_20d=avg_v_20,
            volume_ratio=(vol/avg_v_20) if avg_v_20>0 else None,
            dollar_volume=(vol*last_close)/1e6,
            atr_14=atr,
            atr_pct=(atr/last_close) if atr and last_close>0 else None,
            above_20dma=(last_close > sma20) if sma20 else None,
            above_50dma=(last_close > sma50) if sma50 else None,
            above_200dma=(last_close > sma200) if sma200 else None,
            extension_flag=((last_close - sma20) / atr > 2.0) if atr and sma20 else False,
            source_tags=['targeted_scan'],
        ))
    return out


def run(*, objective: str = DEFAULT_OBJECTIVE, cash_hurdle_apr: float = DEFAULT_CASH_HURDLE_APR, extra_seed: Optional[list[str]] = None, use_cache: bool = True, max_total: int = 120) -> dict:
    as_of = datetime.now().astimezone().isoformat(timespec="seconds")

    bench = _benchmark_stats()
    pos_map = _alpaca_positions_map()
    held = set(pos_map.keys())
    generated = [cp.ticker for cp in candidate_gen.generate_pools(seed=extra_seed, max_total=20, include_ipos=False, include_movers=False)]
    curated = ['SPY','IWM','QQQ','TQQQ','XLE','XLK','XLI','XLV','XLF','XLP','XLY','XLC','TLT','ROBO','WM','RSG','ZTS','PANW','CRWD','ANET','UBER','OXY','DKNG','OKLO','RDDT','GOOG','NVDA','CEG','VST','NRG','VRT','CLS','TSM','RKLB']
    targeted = list(dict.fromkeys(list(held) + (extra_seed or []) + curated + generated))[:max_total]

    def do_scan():
        return [m.as_dict() for m in _targeted_scan(targeted)]

    movers_raw = cache_manager.get_or_compute("intraday_alpha_scan", 300, do_scan) if use_cache else do_scan()
    movers = [LiquidMover(**m) for m in movers_raw]
    regime = regime_mod.compute()
    # Candidate universe: current book + discovered names + focused seeds
    discovered = [m.ticker for m in movers]
    universe = []
    seen = set()
    for t in list(held) + discovered + generated + (extra_seed or []):
        t = t.upper()
        if t and t not in seen:
            seen.add(t)
            universe.append(t)

    mover_map = {m.ticker: m for m in movers}
    filtered_universe = [t for t in universe if t in mover_map]

    def do_cats():
        res = verify_many(filtered_universe[:80])
        return {k: v.as_dict() for k, v in res.items()}

    catalysts_raw = cache_manager.get_or_compute("intraday_alpha_catalysts", 600, do_cats) if use_cache else do_cats()
    catalysts = {k: CatalystResult(**v) for k, v in catalysts_raw.items()}

    sector_5d_map = {}
    spy_5d = None
    for m in movers:
        if m.ticker == "SPY":
            spy_5d = m.pct_change_5d
        if m.ticker in {"XLE", "XLK", "XLI", "XLV", "XLF", "XLP", "XLY", "XLC", "XLU", "XLRE"}:
            sector_5d_map[m.ticker] = m.pct_change_5d

    scored = []
    for t in filtered_universe:
        m = mover_map[t]
        c = catalysts.get(t)
        sector_pct = None
        if m.sector:
            etf = sector_etf_for(m.sector)
            if etf:
                sector_pct = sector_5d_map.get(etf)
        s = alpha_ranker.score_one(m, c, regime=regime.regime, spy_5d_pct=spy_5d, sector_5d_pct=sector_pct)
        scored.append((m, c, s))
    scored.sort(key=lambda x: x[2].score, reverse=True)

    ranked_holdings = []
    ranked_replacements = []
    held_scores = []
    repl_scores = []
    for m, c, s in scored:
        rec = _rec_class(s.score, bool(c and c.catalyst_type != "none"))
        try:
            entry_plan = asdict(_limit_plan(m, s))
        except Exception as e:
            entry_plan = {
                'order_type': 'skip', 'limit_price': None, 'acceptable_chase_to': None,
                'trigger': 'execution plan rejected', 'invalidator': str(e), 'stop': None,
                'holding_horizon': '1-5 trading days', 'confidence': 'low',
                'reference_price': None, 'price_source': 'error'
            }
        idea = RankedIdea(
            ticker=m.ticker,
            action="hold" if m.ticker in held else "watch",
            forward_alpha_score=s.score,
            confidence=s.confidence,
            dominant_risk=s.dominant_risk,
            recommendation_class=rec,
            reason=_reason(m, s, c),
            entry_plan=entry_plan,
            components=s.components,
            penalties=s.penalties,
            source_tags=m.source_tags,
        )
        if m.ticker in held:
            ranked_holdings.append(idea)
            held_scores.append(s.score)
        else:
            ranked_replacements.append(idea)
            repl_scores.append(s.score)

    ranked_holdings.sort(key=lambda x: x.forward_alpha_score)
    ranked_replacements.sort(key=lambda x: x.forward_alpha_score, reverse=True)

    rotations = []
    if ranked_holdings and ranked_replacements:
        weakest = [h for h in ranked_holdings if h.ticker not in {"SPY"}][:5]
        strongest = ranked_replacements[:5]
        for sell in weakest:
            for buy in strongest:
                delta = round(buy.forward_alpha_score - sell.forward_alpha_score, 2)
                if delta < 8:
                    continue
                buy_m = mover_map[buy.ticker]
                cash_beats = buy.forward_alpha_score < 55
                sell_qty = None
                if sell.ticker in pos_map:
                    try:
                        sell_qty = max(1, int(float(pos_map[sell.ticker].get("qty") or 0) * 0.5))
                    except Exception:
                        sell_qty = None
                buy_qty = None
                if sell_qty and buy_m.last_price and sell.ticker in pos_map:
                    try:
                        sell_mv = abs(float(pos_map[sell.ticker].get("market_value") or 0)) * 0.5
                        buy_qty = max(1, int(sell_mv / buy_m.last_price))
                    except Exception:
                        buy_qty = None
                rotations.append(RotationPlan(
                    sell={"ticker": sell.ticker, "qty": sell_qty},
                    buy={"ticker": buy.ticker, "qty": buy_qty, "entry_plan": buy.entry_plan},
                    rotation_delta_score=delta,
                    cash_beats_trade=cash_beats,
                    execution_notes="Wait for sell fill, then place buy. Prefer limit unless order_type says market.",
                ))
        rotations.sort(key=lambda r: r.rotation_delta_score, reverse=True)

    top_buys = ranked_replacements[:10]
    top_holds = ranked_holdings[:10]
    active_return_day = None
    if bench["portfolio_return_day"] is not None and regime.spy_5d_pct is not None:
        active_return_day = None
    return {
        "objective": objective,
        "as_of": as_of,
        "macro_regime": {
            "label": regime.regime,
            "benchmark": "SPY",
            "vix_level": regime.vix_close,
            "notes": regime.reason,
        },
        "portfolio_state": {
            "portfolio_equity": bench["equity"],
            "portfolio_return_day": bench["portfolio_return_day"],
            "cash_hurdle_apr": cash_hurdle_apr,
            "buying_power": bench["buying_power"],
            "cash": bench["cash"],
        },
        "ranked_holdings": [asdict(x) for x in top_holds],
        "ranked_replacements": [asdict(x) for x in top_buys],
        "proposed_rotations": [asdict(x) for x in rotations[:10]],
        "source_freshness": {
            "intraday_scan_cache": cache_manager.info("intraday_alpha_scan"),
            "catalyst_cache": cache_manager.info("intraday_alpha_catalysts"),
        },
    }
