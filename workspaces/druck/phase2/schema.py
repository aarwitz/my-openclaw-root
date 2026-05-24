"""Canonical schema for Phase II candidate records.

One record per (date, ticker). Field set is exhaustive — adapters fill what
they can, scoring and classification respect missing values (None) and
fail-closed.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Optional
from enum import Enum


class Regime(str, Enum):
    RISK_ON = "risk_on"
    NEUTRAL = "neutral"
    CAUTION = "caution"
    RISK_OFF = "risk_off"
    CRISIS = "crisis"


class SetupState(str, Enum):
    BREAKOUT_CONTINUATION = "breakout_continuation"
    POST_EARNINGS_DRIFT = "post_earnings_drift"
    SELL_THE_NEWS_DIGESTION = "sell_the_news_digestion"
    SYMPATHY_MOMENTUM = "sympathy_momentum"
    MEAN_REVERSION_BOUNCE = "mean_reversion_bounce"
    OVEREXTENDED_CHASE = "overextended_chase"
    NONE = "none"


class CatalystType(str, Enum):
    EARNINGS_DOUBLE_BEAT = "earnings_double_beat"
    GUIDANCE_RAISE = "guidance_raise"
    MAJOR_CORPORATE_EVENT = "major_corporate_event"
    ANALYST_REVISION_CLUSTER = "analyst_revision_cluster"
    SECTOR_SYMPATHY_CONFIRMED = "sector_sympathy_confirmed"
    NONE = "none"


class RecommendationClass(str, Enum):
    BUY_READY = "buy_ready"
    CONDITIONAL_BUY = "conditional_buy"
    WATCH_ONLY = "watch_only"
    AVOID = "avoid"
    INCOMPLETE = "incomplete"
    PENDING = "pending"


@dataclass
class CandidateRecord:
    # ---- identity ----
    date: str                            # YYYY-MM-DD (Eastern, business day)
    ticker: str
    fetch_timestamp: Optional[str] = None  # ISO-8601 UTC

    # ---- catalyst ----
    verified_catalyst_type: str = CatalystType.NONE.value
    catalyst_pass: bool = False
    catalyst_source: Optional[str] = None
    earnings_date: Optional[str] = None
    eps_actual: Optional[float] = None
    eps_estimate: Optional[float] = None
    revenue_actual: Optional[float] = None
    revenue_estimate: Optional[float] = None
    guidance_raise_flag: bool = False
    major_event_flag: bool = False
    analyst_revision_cluster_flag: bool = False
    sector_sympathy_flag: bool = False
    catalyst_notes: Optional[str] = None

    # ---- price / structure ----
    prev_close: Optional[float] = None
    last_close: Optional[float] = None
    five_day_change_pct: Optional[float] = None
    twenty_day_high: Optional[float] = None
    fifty_day_ma: Optional[float] = None
    twenty_day_ma: Optional[float] = None
    atr_abs: Optional[float] = None        # 14-day Wilder ATR in $
    atr_pct: Optional[float] = None        # atr_abs / last_close
    volume_ratio: Optional[float] = None   # day vol / 20d avg vol
    dollar_volume_m: Optional[float] = None
    extension_vs_20d_ma_atr: Optional[float] = None  # (close - 20dMA)/atr_abs
    realized_vol_5d: Optional[float] = None  # annualized

    # ---- setup ----
    setup_state: str = SetupState.NONE.value
    setup_state_reason: Optional[str] = None
    breakout_level: Optional[float] = None
    gap_hold_flag: bool = False
    reclaim_flag: bool = False
    overextended_flag: bool = False
    rsi_14: Optional[float] = None

    # ---- analyst sentiment (FMP) ----
    fmp_consensus: Optional[str] = None
    fmp_buy_count: Optional[int] = None
    fmp_hold_count: Optional[int] = None
    fmp_sell_count: Optional[int] = None
    fmp_target_consensus: Optional[float] = None
    fmp_target_high: Optional[float] = None
    fmp_target_low: Optional[float] = None
    fmp_target_activity_last_month: Optional[int] = None
    fmp_data_available: bool = False

    # ---- portfolio fit ----
    overlaps_existing_sector: bool = False
    overlaps_existing_factor: bool = False
    portfolio_fit_bucket: Optional[str] = None  # missing|neutral|doubles
    existing_related_positions: list[str] = field(default_factory=list)
    sector: Optional[str] = None
    factor: Optional[str] = None

    # ---- live execution (Alpaca, Monday-open) ----
    alpaca_bid: Optional[float] = None
    alpaca_ask: Optional[float] = None
    alpaca_last: Optional[float] = None
    alpaca_quote_ts: Optional[str] = None
    alpaca_spread_bps: Optional[float] = None
    alpaca_live_conflict_flag: bool = False
    alpaca_live_context_note: Optional[str] = None

    # ---- regime (computed once per run, attached to every row) ----
    spy_close: Optional[float] = None
    spy_20d_ma: Optional[float] = None
    spy_50d_ma: Optional[float] = None
    vix_close: Optional[float] = None
    regime: str = Regime.NEUTRAL.value

    # ---- scoring (deterministic) ----
    catalyst_score: float = 0.0
    price_score: float = 0.0
    setup_quality_score: float = 0.0
    sector_score: float = 0.0
    portfolio_fit_score: float = 0.0
    liquidity_score: float = 0.0
    volatility_efficiency_score: float = 0.0
    extension_penalty: float = 0.0       # negative or 0
    crowding_penalty: float = 0.0
    redundancy_penalty: float = 0.0
    total_score_pre_penalty: float = 0.0
    total_score_final: float = 0.0
    recommendation_class: str = RecommendationClass.PENDING.value

    # ---- action / discipline ----
    suggested_risk_pct: Optional[float] = None
    suggested_stop: Optional[float] = None
    trigger: Optional[str] = None
    invalidator: Optional[str] = None
    falsifier_by_wednesday: Optional[str] = None
    next_check: Optional[str] = None
    notes: Optional[str] = None

    # ---- diagnostics ----
    errors: list[str] = field(default_factory=list)
    sources_used: list[str] = field(default_factory=list)

    # ----------- helpers -----------
    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)


# Header order written to the Candidates sheet (matches PHASE_II_PLAN.md §8).
CANDIDATES_HEADER: list[str] = [
    "date", "ticker", "regime", "setup_state", "verified_catalyst_type",
    "catalyst_pass", "eps_actual", "eps_estimate", "revenue_actual",
    "revenue_estimate", "five_day_change_pct", "volume_ratio", "atr_pct",
    "dollar_volume_m", "catalyst_score", "price_score", "setup_quality_score",
    "sector_score", "portfolio_fit_score", "liquidity_score",
    "volatility_efficiency_score", "extension_penalty", "crowding_penalty",
    "redundancy_penalty", "total_score_pre_penalty", "total_score_final",
    "recommendation_class", "suggested_risk_pct", "suggested_stop",
    "trigger", "invalidator", "falsifier_by_wednesday", "next_check", "notes",
]

OUTCOMES_HEADER: list[str] = [
    "date_added", "ticker", "regime", "setup_state", "recommendation_class",
    "entry_reference_price", "1d_return", "5d_return", "10d_return",
    "max_runup", "max_drawdown", "falsifier_resolved", "outcome_label",
    "postmortem",
]
