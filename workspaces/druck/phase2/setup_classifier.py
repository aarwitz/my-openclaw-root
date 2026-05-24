"""Rule-based setup-state classifier (PHASE_II_PLAN.md §3, QUANT_DEV_SPEC §1).

All rules are deterministic and operate on derived structural fields. Setup
labels are mutually exclusive — precedence below resolves multi-true cases.

Precedence (highest first):
  1. overextended_chase            (safety override)
  2. post_earnings_drift           (event-anchored)
  3. sell_the_news_digestion       (event-anchored)
  4. sympathy_momentum             (event-anchored, weak)
  5. breakout_continuation         (structural)
  6. mean_reversion_bounce         (structural, contrarian)
  7. none

Each function returns (matches: bool, reason: str).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .schema import SetupState


@dataclass
class SetupInputs:
    last_close: Optional[float]
    prev_close: Optional[float]
    twenty_day_high: Optional[float]
    twenty_day_ma: Optional[float]
    fifty_day_ma: Optional[float]
    atr_abs: Optional[float]
    atr_pct: Optional[float]
    volume_ratio: Optional[float]            # day vol / 20d avg
    rsi_14: Optional[float]
    five_day_change_pct: Optional[float]

    # event-context flags from catalyst stage
    earnings_double_beat_recent: bool = False
    days_since_earnings: Optional[int] = None
    sector_sympathy_confirmed: bool = False
    catalyst_in_last_5d: bool = False

    # post-earnings price geometry
    post_earnings_high: Optional[float] = None
    pre_event_close: Optional[float] = None
    sold_off_since_earnings: bool = False
    reclaim_on_volume: bool = False


def _ext_atr(close: Optional[float], ma: Optional[float], atr: Optional[float]) -> Optional[float]:
    if close is None or ma is None or atr is None or atr <= 0:
        return None
    return (close - ma) / atr


# ---------- individual rules ----------

def is_overextended_chase(s: SetupInputs) -> tuple[bool, str]:
    ext = _ext_atr(s.last_close, s.twenty_day_ma, s.atr_abs)
    if ext is None:
        return False, "missing extension inputs"
    if ext > 2.0 and not s.catalyst_in_last_5d:
        return True, f"ext={ext:.2f} ATR > 2.0 and no catalyst in 5d"
    return False, ""


def is_post_earnings_drift(s: SetupInputs) -> tuple[bool, str]:
    if not s.earnings_double_beat_recent:
        return False, "no recent double beat"
    if s.days_since_earnings is None or s.days_since_earnings > 10:
        return False, "earnings >10d ago"
    if s.last_close is None or s.post_earnings_high is None or s.atr_abs is None:
        return False, "missing geometry"
    delta = s.post_earnings_high - s.last_close
    if delta < 0:
        return False, "above post-earnings high"
    if delta > s.atr_abs:
        return False, "more than 1 ATR below post-earnings high"
    if s.pre_event_close is not None and s.last_close < s.pre_event_close:
        return False, "lost the gap"
    return True, f"within 1 ATR of post-earnings high; days={s.days_since_earnings}"


def is_sell_the_news_digestion(s: SetupInputs) -> tuple[bool, str]:
    if not s.earnings_double_beat_recent:
        return False, "no beat"
    if not s.sold_off_since_earnings:
        return False, "no immediate sell-off recorded"
    if not s.reclaim_on_volume:
        return False, "no volume reclaim"
    if s.pre_event_close is None or s.last_close is None:
        return False, "missing pre-event close"
    if s.last_close < s.pre_event_close:
        return False, "still below pre-event level"
    return True, "reclaimed pre-event level on volume after sell-off"


def is_sympathy_momentum(s: SetupInputs) -> tuple[bool, str]:
    if not s.sector_sympathy_confirmed:
        return False, "no sympathy gate pass"
    return True, "sector leader catalyst confirmed; peer correlation gate passed upstream"


def is_breakout_continuation(s: SetupInputs) -> tuple[bool, str]:
    if s.last_close is None or s.twenty_day_high is None or s.volume_ratio is None or s.atr_abs is None:
        return False, "missing inputs"
    if s.last_close < s.twenty_day_high:
        return False, "below 20d high"
    if s.volume_ratio < 1.5:
        return False, f"vol_ratio={s.volume_ratio:.2f} < 1.5"
    if (s.last_close - s.twenty_day_high) > s.atr_abs:
        return False, "more than 1 ATR above breakout"
    return True, f"close>=20dH on vol={s.volume_ratio:.2f}"


def is_mean_reversion_bounce(s: SetupInputs) -> tuple[bool, str]:
    if s.rsi_14 is None or s.last_close is None or s.prev_close is None:
        return False, "missing inputs"
    # require RSI<30 AND today's close > yesterday's high (proxy: > prev_close + 0.5*ATR)
    proxy_prev_high = s.prev_close + 0.5 * (s.atr_abs or 0)
    if s.rsi_14 >= 30:
        return False, f"rsi={s.rsi_14:.1f} >= 30"
    if s.last_close <= proxy_prev_high:
        return False, "no reclaim of prior day high"
    ext = _ext_atr(s.last_close, s.twenty_day_ma, s.atr_abs)
    if ext is not None and ext > 1.0:
        return False, "name already extended; bounce setup rejected"
    return True, "RSI<30 + reclaim on quality non-extended name"


# ---------- top-level dispatch ----------

PRECEDENCE = [
    (SetupState.OVEREXTENDED_CHASE.value,    is_overextended_chase),
    (SetupState.POST_EARNINGS_DRIFT.value,   is_post_earnings_drift),
    (SetupState.SELL_THE_NEWS_DIGESTION.value, is_sell_the_news_digestion),
    (SetupState.SYMPATHY_MOMENTUM.value,     is_sympathy_momentum),
    (SetupState.BREAKOUT_CONTINUATION.value, is_breakout_continuation),
    (SetupState.MEAN_REVERSION_BOUNCE.value, is_mean_reversion_bounce),
]


def classify(s: SetupInputs) -> tuple[str, str]:
    """Return (setup_state, reason). Falls back to NONE."""
    for label, rule in PRECEDENCE:
        ok, reason = rule(s)
        if ok:
            return label, reason
    return SetupState.NONE.value, "no rule matched"
