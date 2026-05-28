"""Deterministic Phase II scorer (AUTONOMOUS_PM_OPERATING_MODEL.md §13.4).

Pure function: takes a `CandidateRecord` populated through normalize() and
fills the scoring + penalty + class fields. Calibration constants live at
the top of this module so quant tweaks happen in one place.

Hard contract:
  - score and class always agree (class is derived from score+penalties+rules)
  - scores never overflow their bucket caps
  - penalties stored separately, all <= 0
  - missing required inputs => recommendation_class = INCOMPLETE
"""
from __future__ import annotations

from typing import Optional

from .schema import (
    CandidateRecord, RecommendationClass, Regime, SetupState, CatalystType,
)

# ============================================================
#                        CALIBRATION
# (See RULES.md for derivation rationale.)
# ============================================================

# Bucket caps (max contributions). Sum should be 100.
W_CATALYST          = 30
W_PRICE             = 20
W_SETUP             = 15
W_SECTOR            = 10
W_PORTFOLIO_FIT     = 10
W_LIQUIDITY         = 5
W_VOL_EFFICIENCY    = 10

# Penalty caps (negative, applied separately).
EXT_PEN_MIN_ATR     = 1.5    # extension below this → 0 penalty
EXT_PEN_MAX_ATR     = 2.5    # extension at/above this → -15
EXT_PEN_FLOOR       = -15.0

CROWDING_MOVE_PCT   = 0.25   # >25% in 10d ⇒ -10
CROWDING_PEN_FLOOR  = -10.0

REDUNDANCY_FLOOR    = -10.0

# Liquidity tiers ($M/day)
LIQ_HIGH_MUSD       = 50.0
LIQ_MID_MUSD        = 20.0

# Class thresholds
BUY_READY_MIN       = 75
COND_BUY_MIN        = 60
WATCH_ONLY_MIN      = 40

# Volatility-efficiency reference: historical 5d expected move (in ATR units)
# by setup. Calibrated from your spec; tuned by replay over time.
HIST_5D_MOVE_BY_SETUP = {
    SetupState.BREAKOUT_CONTINUATION.value:    1.6,
    SetupState.POST_EARNINGS_DRIFT.value:      1.8,
    SetupState.SELL_THE_NEWS_DIGESTION.value:  1.2,
    SetupState.SYMPATHY_MOMENTUM.value:        1.0,
    SetupState.MEAN_REVERSION_BOUNCE.value:    1.1,
    SetupState.OVEREXTENDED_CHASE.value:       0.4,
    SetupState.NONE.value:                     0.5,
}


# ============================================================
#                      BUCKET SCORERS
# ============================================================

def _score_catalyst(r: CandidateRecord) -> float:
    """0..30. Stronger event evidence = larger score."""
    if not r.catalyst_pass:
        return 0.0
    t = r.verified_catalyst_type
    if t == CatalystType.EARNINGS_DOUBLE_BEAT.value and r.guidance_raise_flag:
        return 30.0
    if t == CatalystType.EARNINGS_DOUBLE_BEAT.value:
        # magnitude bonus from beat sizes
        beat_eps = (r.eps_actual - r.eps_estimate) / abs(r.eps_estimate) if r.eps_actual is not None and r.eps_estimate not in (None, 0) else 0
        beat_rev = (r.revenue_actual - r.revenue_estimate) / abs(r.revenue_estimate) if r.revenue_actual is not None and r.revenue_estimate not in (None, 0) else 0
        bonus = min(8, 4 * (max(beat_eps, 0) + max(beat_rev, 0)) * 100)  # cap +8
        return min(30.0, 18.0 + bonus)
    if t == CatalystType.GUIDANCE_RAISE.value:
        return 22.0
    if t == CatalystType.MAJOR_CORPORATE_EVENT.value:
        return 20.0
    if t == CatalystType.ANALYST_REVISION_CLUSTER.value:
        return 15.0
    if t == CatalystType.SECTOR_SYMPATHY_CONFIRMED.value:
        return 10.0
    return 0.0


def _score_price(r: CandidateRecord) -> float:
    """0..20."""
    s = 0.0
    # close vs 20d high (8)
    if r.last_close is not None and r.twenty_day_high is not None and r.twenty_day_high > 0:
        ratio = r.last_close / r.twenty_day_high
        if ratio >= 1.0:
            s += 8
        elif ratio >= 0.97:
            s += 5
        elif ratio >= 0.93:
            s += 2
    # vol ratio (8)
    if r.volume_ratio is not None:
        if r.volume_ratio >= 2.0:
            s += 8
        elif r.volume_ratio >= 1.5:
            s += 6
        elif r.volume_ratio >= 1.2:
            s += 3
    # above 50d MA (4)
    if r.last_close is not None and r.fifty_day_ma is not None and r.last_close >= r.fifty_day_ma:
        s += 4
    return min(W_PRICE, s)


def _score_setup_quality(r: CandidateRecord) -> float:
    """0..15. Penalize structural inconsistency for the labeled setup."""
    state = r.setup_state
    if state in (SetupState.NONE.value, SetupState.OVEREXTENDED_CHASE.value):
        return 0.0
    base = 10.0
    # bonus when key structural confirmations are present
    if state == SetupState.BREAKOUT_CONTINUATION.value:
        if r.volume_ratio is not None and r.volume_ratio >= 1.8:
            base += 3
        if r.last_close is not None and r.twenty_day_high is not None and r.last_close >= r.twenty_day_high:
            base += 2
    elif state == SetupState.POST_EARNINGS_DRIFT.value:
        if r.gap_hold_flag:
            base += 5
    elif state == SetupState.SELL_THE_NEWS_DIGESTION.value:
        if r.reclaim_flag:
            base += 5
    elif state == SetupState.SYMPATHY_MOMENTUM.value:
        if r.sector_sympathy_flag:
            base += 5
    elif state == SetupState.MEAN_REVERSION_BOUNCE.value:
        if r.rsi_14 is not None and r.rsi_14 < 25:
            base += 3
    return min(W_SETUP, base)


def _score_sector(r: CandidateRecord, sector_rank_pct: Optional[float] = None) -> float:
    """0..10. Defaults to sector_score already on the record (set upstream)."""
    if sector_rank_pct is None:
        return min(W_SECTOR, max(0.0, r.sector_score))
    return round(W_SECTOR * max(0.0, min(1.0, sector_rank_pct)), 2)


def _score_portfolio_fit(r: CandidateRecord) -> float:
    """0..10."""
    bucket = r.portfolio_fit_bucket
    if bucket == "missing":
        return 10.0
    if bucket == "doubles":
        return 0.0
    return 5.0  # neutral / unknown


def _score_liquidity(r: CandidateRecord) -> float:
    """0..5. Returns 0 also forces watch_only via hard rule."""
    dv = r.dollar_volume_m
    if dv is None:
        return 0.0
    if dv >= LIQ_HIGH_MUSD:
        return 5.0
    if dv >= LIQ_MID_MUSD:
        return 3.0
    return 0.0


def _score_vol_efficiency(r: CandidateRecord) -> float:
    """0..10.

    score = clip( hist_5d_move_atr_units / max(atr_pct_norm, eps), 0..10 )

    `atr_pct_norm` rescales ATR% so a typical 3% mover ≈ 1.0. This avoids
    over-rewarding chaotic high-ATR names and under-rewarding tight setups.
    """
    if r.atr_pct is None or r.atr_pct <= 0:
        return 0.0
    expected_atr_units = HIST_5D_MOVE_BY_SETUP.get(r.setup_state, 0.5)
    atr_pct_norm = r.atr_pct / 0.03
    raw = expected_atr_units / max(atr_pct_norm, 0.5)
    return round(min(W_VOL_EFFICIENCY, max(0.0, raw * 10)), 2)


# ============================================================
#                        PENALTIES
# ============================================================

def _extension_penalty(r: CandidateRecord) -> float:
    ext = r.extension_vs_20d_ma_atr
    if ext is None or ext <= EXT_PEN_MIN_ATR:
        return 0.0
    if ext >= EXT_PEN_MAX_ATR:
        return EXT_PEN_FLOOR
    # linear from 0 at MIN to FLOOR at MAX
    span = EXT_PEN_MAX_ATR - EXT_PEN_MIN_ATR
    frac = (ext - EXT_PEN_MIN_ATR) / span
    return round(EXT_PEN_FLOOR * frac, 2)


def _crowding_penalty(r: CandidateRecord) -> float:
    pct = r.five_day_change_pct or 0.0
    # We use 5d% as a proxy for "10d up >25%" — replace with 10d once available.
    if pct >= CROWDING_MOVE_PCT:
        return CROWDING_PEN_FLOOR
    if pct >= 0.15:
        # graded
        return round(CROWDING_PEN_FLOOR * ((pct - 0.15) / (CROWDING_MOVE_PCT - 0.15)), 2)
    return 0.0


def _redundancy_penalty(r: CandidateRecord) -> float:
    if r.overlaps_existing_sector and r.overlaps_existing_factor:
        return REDUNDANCY_FLOOR
    if r.overlaps_existing_sector or r.overlaps_existing_factor:
        return REDUNDANCY_FLOOR / 2  # -5 partial
    return 0.0


# ============================================================
#                  MISSING-INPUT GATEKEEPING
# ============================================================

REQUIRED_FIELDS = (
    "last_close", "atr_pct", "twenty_day_ma", "twenty_day_high",
    "volume_ratio", "dollar_volume_m",
)


def _missing_required(r: CandidateRecord) -> list[str]:
    return [f for f in REQUIRED_FIELDS if getattr(r, f) is None]


# ============================================================
#                        TOP-LEVEL
# ============================================================

def score(r: CandidateRecord) -> CandidateRecord:
    """Mutate-and-return the record with all score / class fields filled."""
    missing = _missing_required(r)
    if missing:
        r.recommendation_class = RecommendationClass.INCOMPLETE.value
        r.notes = (r.notes or "") + f" missing:{','.join(missing)}"
        return r

    r.catalyst_score             = _score_catalyst(r)
    r.price_score                = _score_price(r)
    r.setup_quality_score        = _score_setup_quality(r)
    r.sector_score               = _score_sector(r)
    r.portfolio_fit_score        = _score_portfolio_fit(r)
    r.liquidity_score            = _score_liquidity(r)
    r.volatility_efficiency_score = _score_vol_efficiency(r)

    r.extension_penalty   = _extension_penalty(r)
    r.crowding_penalty    = _crowding_penalty(r)
    r.redundancy_penalty  = _redundancy_penalty(r)

    base = (
        r.catalyst_score + r.price_score + r.setup_quality_score
        + r.sector_score + r.portfolio_fit_score + r.liquidity_score
        + r.volatility_efficiency_score
    )
    r.total_score_pre_penalty = round(base, 2)
    r.total_score_final = round(
        base + r.extension_penalty + r.crowding_penalty + r.redundancy_penalty, 2
    )

    r.recommendation_class = _classify(r)
    return r


def _classify(r: CandidateRecord) -> str:
    # Hard rules first
    hard_block_watch = False
    hard_demote_cond = False

    # Catalyst gate
    if not r.catalyst_pass:
        hard_block_watch = True

    # Liquidity floor
    if r.liquidity_score == 0:
        hard_block_watch = True

    # Setup-state hard rules
    if r.setup_state == SetupState.OVEREXTENDED_CHASE.value:
        hard_block_watch = True

    # Penalty caps
    single_pens = (r.extension_penalty, r.crowding_penalty, r.redundancy_penalty)
    if any(p <= -10 for p in single_pens):
        hard_demote_cond = True
    if sum(single_pens) <= -20:
        hard_block_watch = True

    # Live conflict / Alpaca missing near open
    if r.alpaca_live_conflict_flag:
        hard_demote_cond = True

    s = r.total_score_final

    if hard_block_watch:
        if s < WATCH_ONLY_MIN:
            return RecommendationClass.AVOID.value
        return RecommendationClass.WATCH_ONLY.value

    if s >= BUY_READY_MIN and not hard_demote_cond:
        cls = RecommendationClass.BUY_READY.value
    elif s >= COND_BUY_MIN or (s >= BUY_READY_MIN and hard_demote_cond):
        cls = RecommendationClass.CONDITIONAL_BUY.value
    elif s >= WATCH_ONLY_MIN:
        cls = RecommendationClass.WATCH_ONLY.value
    else:
        cls = RecommendationClass.AVOID.value

    return _apply_regime_overlay(cls, r)


def _apply_regime_overlay(cls: str, r: CandidateRecord) -> str:
    rg = r.regime
    if rg == Regime.CRISIS.value:
        return RecommendationClass.WATCH_ONLY.value
    if rg == Regime.RISK_OFF.value:
        if cls == RecommendationClass.BUY_READY.value:
            return RecommendationClass.CONDITIONAL_BUY.value
        if r.setup_state == SetupState.MEAN_REVERSION_BOUNCE.value \
           and cls in (RecommendationClass.CONDITIONAL_BUY.value, RecommendationClass.BUY_READY.value):
            return RecommendationClass.WATCH_ONLY.value
        return cls
    if rg == Regime.CAUTION.value and cls == RecommendationClass.BUY_READY.value:
        if r.setup_state in (
            SetupState.BREAKOUT_CONTINUATION.value,
            SetupState.SYMPATHY_MOMENTUM.value,
        ):
            return RecommendationClass.CONDITIONAL_BUY.value
    return cls


# ============================================================
#                    POSITION SIZING
# ============================================================

def attach_sizing(r: CandidateRecord, nav_usd: float) -> CandidateRecord:
    """Fill suggested_risk_pct + suggested_stop. Advisory only, no orders."""
    if r.recommendation_class not in (
        RecommendationClass.BUY_READY.value,
        RecommendationClass.CONDITIONAL_BUY.value,
    ):
        return r
    if r.last_close is None or r.atr_abs is None or r.atr_abs <= 0:
        return r
    risk_pct = 2.0 if r.recommendation_class == RecommendationClass.BUY_READY.value else 1.5
    r.suggested_risk_pct = risk_pct
    r.suggested_stop = round(r.last_close - 1.5 * r.atr_abs, 2)
    return r
