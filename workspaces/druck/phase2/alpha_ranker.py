"""1-week alpha ranker (Priority 4).

Estimate odds that a ticker BEATS SPY over the next 1-5 trading days, NOT
generic investing quality.

Formula (deterministic, no options):
  alpha_score = sum of weighted components, then regime overlay.

Components (weights sum to 100):
  trend_strength       (25)  — close vs SMA20/SMA50, RSI tilt
  momentum_5d          (20)  — pct_change_5d outperforming SPY 5d
  catalyst_strength    (20)  — from catalyst_verifier confidence
  vol_efficiency       (10)  — realized vol vs ATR, prefer steady move
  liquidity_quality    (10)  — dollar-vol tier, tight-spread bonus
  sector_leadership    (10)  — sector ETF 5d return percentile
  freshness_bonus      ( 5)  — recent gap/breakout, post-IPO momentum

Penalties (subtract after base):
  extension_penalty    (-15..0)  — > 2 ATR over SMA20
  crowding_penalty     (-10..0)  — already at top of 5d move distribution
  spread_penalty       (-10..0)  — wide spread degrades execution

Regime overlay:
  crisis    → cap at 30 (effectively avoid)
  risk_off  → cap at 50
  caution   → cap at 70
  neutral   → no cap
  risk_on   → +5 boost (capped 100)

Output:
  AlphaScore.score (0..100)  → estimated SPY-beat odds for next week.
  AlphaScore.confidence       → data-completeness fraction (0..1).
  AlphaScore.dominant_risk    → top reason this trade could fail.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Optional

from .catalyst_verifier import CatalystResult
from .market_scanner import LiquidMover
from .schema import Regime


@dataclass
class AlphaScore:
    ticker: str
    score: float = 0.0                  # 0..100, higher = better SPY-beat odds
    confidence: float = 0.0             # 0..1 data completeness
    dominant_risk: Optional[str] = None
    regime: Optional[str] = None
    benchmark_relative_score: float = 0.0  # raw "beat-SPY" odds before regime overlay
    components: dict = field(default_factory=dict)
    penalties: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


# ---- weights (total 100) ----
W_TREND        = 25.0
W_MOMENTUM_5D  = 20.0
W_CATALYST     = 20.0
W_VOL_EFF      = 10.0
W_LIQUIDITY    = 10.0
W_SECTOR_LEAD  = 10.0
W_FRESHNESS    = 5.0


def _trend_strength(m: LiquidMover) -> float:
    """0..W_TREND. Reward stocks above SMA20/50/200 and not yet overextended."""
    score = 0.0
    if m.above_20dma:    score += W_TREND * 0.35
    if m.above_50dma:    score += W_TREND * 0.35
    if m.above_200dma:   score += W_TREND * 0.30
    return score


def _momentum_5d(m: LiquidMover, spy_5d: Optional[float]) -> float:
    """0..W_MOMENTUM_5D. Reward outperformance vs SPY 5d. Linear up to +5% over."""
    if m.pct_change_5d is None:
        return 0.0
    bench = spy_5d or 0.0
    excess = m.pct_change_5d - bench
    # 0% excess → 0; +5% excess → full weight
    val = max(0.0, min(excess / 0.05, 1.0)) * W_MOMENTUM_5D
    return val


def _catalyst_strength(c: Optional[CatalystResult]) -> float:
    if c is None:
        return 0.0
    return c.catalyst_confidence * W_CATALYST


def _vol_efficiency(m: LiquidMover) -> float:
    """0..W_VOL_EFF. Reward ATR% in efficient range (1..4%)."""
    if m.atr_pct is None:
        return 0.0
    a = m.atr_pct
    if 0.01 <= a <= 0.04:
        return W_VOL_EFF
    if a < 0.01:
        return W_VOL_EFF * (a / 0.01)  # too quiet → less reward
    # too wild
    if a >= 0.10:
        return 0.0
    # 0.04..0.10 linear decay
    return W_VOL_EFF * max(0.0, 1.0 - (a - 0.04) / 0.06)


def _liquidity_quality(m: LiquidMover) -> float:
    """0..W_LIQUIDITY. Tiered by dollar volume + spread."""
    if m.dollar_volume is None:
        return 0.0
    dv = m.dollar_volume   # millions
    base = 0.0
    if dv >= 200:    base = W_LIQUIDITY
    elif dv >= 100:  base = W_LIQUIDITY * 0.8
    elif dv >= 50:   base = W_LIQUIDITY * 0.6
    elif dv >= 25:   base = W_LIQUIDITY * 0.4
    else:            base = 0.0
    # spread penalty already separate; here just bonus for tight spread
    if m.spread_pct is not None and m.spread_pct < 0.005:
        base = min(W_LIQUIDITY, base + 1.0)
    return base


def _sector_leadership(m: LiquidMover, sector_5d_pct: Optional[float]) -> float:
    """0..W_SECTOR_LEAD. Reward stocks whose sector is leading the market."""
    if sector_5d_pct is None:
        return W_SECTOR_LEAD * 0.5  # neutral default
    # sector +2% → full; -2% → zero
    norm = max(0.0, min((sector_5d_pct + 0.02) / 0.04, 1.0))
    return W_SECTOR_LEAD * norm


def _freshness_bonus(m: LiquidMover) -> float:
    """0..W_FRESHNESS. Reward fresh post-gap or recent IPO momentum."""
    score = 0.0
    if m.gap_pct is not None and m.gap_pct >= 0.02:
        score += W_FRESHNESS * 0.6
    if m.days_since_ipo is not None and 14 <= m.days_since_ipo <= 180:
        score += W_FRESHNESS * 0.4
    return min(score, W_FRESHNESS)


# ---- penalties ----

def _extension_penalty(m: LiquidMover) -> float:
    """-15..0. Linear from 1.5 ATR (no penalty) to 2.5 ATR (max penalty)."""
    # extension is computed in scanner as boolean only; recompute magnitude here if possible
    # We rely on extension_flag and pct_change_5d as a proxy.
    if not m.extension_flag:
        return 0.0
    # heuristic: if extension flag is on, apply at least -8; if 5d move >25%, full -15
    if m.pct_change_5d is not None and m.pct_change_5d >= 0.25:
        return -15.0
    return -8.0


def _crowding_penalty(m: LiquidMover) -> float:
    """-10..0. Penalty for crowded longs (5d move >15%, graded)."""
    if m.pct_change_5d is None:
        return 0.0
    p = m.pct_change_5d
    if p < 0.15:
        return 0.0
    if p >= 0.30:
        return -10.0
    return -10.0 * ((p - 0.15) / 0.15)


def _spread_penalty(m: LiquidMover) -> float:
    """-10..0. Wide spread degrades execution; under 1% no penalty."""
    if m.spread_pct is None:
        return 0.0
    s = m.spread_pct
    if s <= 0.01:
        return 0.0
    if s >= 0.02:
        return -10.0
    return -10.0 * ((s - 0.01) / 0.01)


# ---- regime overlay ----

REGIME_CAPS = {
    Regime.CRISIS.value:   30.0,
    Regime.RISK_OFF.value: 50.0,
    Regime.CAUTION.value:  70.0,
    Regime.MACRO_UNKNOWN.value: 100.0,
    Regime.NEUTRAL.value:  100.0,
    Regime.RISK_ON.value:  100.0,
}

REGIME_BOOST = {
    Regime.RISK_ON.value: 5.0,
}


def _apply_regime(score: float, regime: Optional[str]) -> float:
    if not regime:
        return score
    capped = min(score, REGIME_CAPS.get(regime, 100.0))
    return min(100.0, capped + REGIME_BOOST.get(regime, 0.0))


# ---- dominant-risk helper ----

def _dominant_risk(
    m: LiquidMover, c: Optional[CatalystResult], regime: Optional[str], penalties: dict
) -> str:
    if regime in (Regime.CRISIS.value, Regime.RISK_OFF.value):
        return f"regime_{regime}"
    if regime == Regime.MACRO_UNKNOWN.value:
        return "macro_unknown"
    if penalties.get("extension", 0) <= -10:
        return "extension"
    if penalties.get("crowding", 0) <= -7:
        return "crowding"
    if penalties.get("spread", 0) <= -5:
        return "wide_spread"
    if c is None or c.catalyst_confidence < 0.3:
        return "no_verified_catalyst"
    if m.dollar_volume is not None and m.dollar_volume < 50:
        return "thin_liquidity"
    if not m.above_20dma:
        return "broken_trend"
    return "none"


# ---- public API ----

def score_one(
    m: LiquidMover,
    catalyst: Optional[CatalystResult] = None,
    *,
    regime: Optional[str] = None,
    spy_5d_pct: Optional[float] = None,
    sector_5d_pct: Optional[float] = None,
) -> AlphaScore:
    """Score a single mover for 1-week SPY outperformance."""
    components = {
        "trend":        round(_trend_strength(m), 2),
        "momentum_5d":  round(_momentum_5d(m, spy_5d_pct), 2),
        "catalyst":     round(_catalyst_strength(catalyst), 2),
        "vol_eff":      round(_vol_efficiency(m), 2),
        "liquidity":    round(_liquidity_quality(m), 2),
        "sector_lead":  round(_sector_leadership(m, sector_5d_pct), 2),
        "freshness":    round(_freshness_bonus(m), 2),
    }
    penalties = {
        "extension":    round(_extension_penalty(m), 2),
        "crowding":     round(_crowding_penalty(m), 2),
        "spread":       round(_spread_penalty(m), 2),
    }
    base = sum(components.values())
    raw = max(0.0, base + sum(penalties.values()))

    final = _apply_regime(raw, regime)

    # --- confidence: fraction of input fields populated ---
    needed = (m.atr_pct, m.pct_change_5d, m.dollar_volume, m.above_20dma, m.above_50dma,
              m.spread_pct, regime, sector_5d_pct, spy_5d_pct, catalyst)
    confidence = sum(1 for x in needed if x is not None) / len(needed)

    return AlphaScore(
        ticker=m.ticker,
        score=round(final, 2),
        benchmark_relative_score=round(raw, 2),
        confidence=round(confidence, 2),
        dominant_risk=_dominant_risk(m, catalyst, regime, penalties),
        regime=regime,
        components=components,
        penalties=penalties,
    )


def rank(
    movers: list[LiquidMover],
    catalysts: dict[str, CatalystResult],
    *,
    regime: Optional[str] = None,
    spy_5d_pct: Optional[float] = None,
    sector_5d_map: Optional[dict[str, float]] = None,  # {sector_etf_ticker: 5d_pct}
    top_n: int = 50,
) -> list[tuple[LiquidMover, AlphaScore]]:
    """Rank movers by 1-week SPY-beat odds. Returns top_n descending by score."""
    pairs: list[tuple[LiquidMover, AlphaScore]] = []
    for m in movers:
        cat = catalysts.get(m.ticker)
        sector_pct = None
        if sector_5d_map and m.sector:
            from .universe import sector_etf_for
            etf = sector_etf_for(m.sector)
            if etf:
                sector_pct = sector_5d_map.get(etf)
        s = score_one(m, cat, regime=regime, spy_5d_pct=spy_5d_pct, sector_5d_pct=sector_pct)
        pairs.append((m, s))
    pairs.sort(key=lambda p: -p[1].score)
    return pairs[:top_n]
