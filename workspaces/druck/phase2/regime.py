"""SPY/VIX → regime classifier (AUTONOMOUS_PM_OPERATING_MODEL.md §13.6).

One trusted output, computed once per run and attached to every row.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .adapters import massive
from .schema import Regime


@dataclass
class RegimeResult:
    regime: str
    spy_close: Optional[float]
    spy_20d_ma: Optional[float]
    spy_50d_ma: Optional[float]
    vix_close: Optional[float]
    spy_5d_pct: Optional[float]
    reason: str


def classify(
    spy_close: Optional[float],
    spy_20d_ma: Optional[float],
    spy_50d_ma: Optional[float],
    vix_close: Optional[float],
    spy_5d_pct: Optional[float],
) -> tuple[str, str]:
    """Pure-function classifier — handy for tests & replay."""
    # Crisis takes precedence
    if (spy_5d_pct is not None and spy_5d_pct <= -0.05) or (vix_close is not None and vix_close > 35):
        return Regime.CRISIS.value, "spy_5d <= -5% or vix > 35"
    # risk_off
    if (spy_close is not None and spy_50d_ma is not None and spy_close < spy_50d_ma) \
       or (vix_close is not None and vix_close > 25):
        return Regime.RISK_OFF.value, "spy < 50d MA or vix > 25"
    # caution
    if spy_close is not None and spy_20d_ma is not None and spy_close < spy_20d_ma \
       and (vix_close is None or 18 <= vix_close <= 25):
        return Regime.CAUTION.value, "spy < 20d MA and vix in [18,25]"
    # risk_on
    if all(v is not None for v in (spy_close, spy_20d_ma, spy_50d_ma, vix_close)) \
       and spy_close > spy_20d_ma > spy_50d_ma and vix_close < 18:  # type: ignore[operator]
        return Regime.RISK_ON.value, "spy > 20d > 50d MA and vix < 18"
    # Missing VIX means macro context is incomplete; do not force a bearish cap.
    if vix_close is None:
        return Regime.MACRO_UNKNOWN.value, "vix unavailable; macro regime incomplete"
    return Regime.NEUTRAL.value, "default"


def _valid_close(value: object) -> Optional[float]:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out > 0 else None


def _resolve_vix_close() -> Optional[float]:
    """Best-effort VIX resolution with conservative validation.

    Finnhub's `VIX` quote can return zero-like placeholder data, so we only
    accept strictly positive closes.
    """
    for sym in ("I:VIX", "VIX", "^VIX"):
        try:
            bars = massive.daily_aggregates(sym, lookback_days=10)
        except Exception:
            bars = []
        if bars:
            close = _valid_close((bars[-1] or {}).get("c"))
            if close is not None:
                return close
        try:
            prev = massive.previous_close(sym)
        except Exception:
            prev = None
        if prev:
            close = _valid_close(prev.get("c"))
            if close is not None:
                return close
    try:
        from .adapters import finnhub
        for sym in ("^VIX", "VIX"):
            q = finnhub.quote(sym)
            close = _valid_close((q or {}).get("c"))
            if close is not None:
                return close
    except Exception:
        pass
    return None


def compute() -> RegimeResult:
    """Pull SPY + VIX from upstream sources and compute regime conservatively."""
    spy_bars = massive.daily_aggregates("SPY", lookback_days=120)

    spy_close = float(spy_bars[-1]["c"]) if spy_bars else None
    spy_20 = massive.sma(spy_bars, 20) if spy_bars else None
    spy_50 = massive.sma(spy_bars, 50) if spy_bars else None
    spy_5d = massive.pct_change(spy_bars, 5) if spy_bars else None
    vix_close = _resolve_vix_close()

    label, reason = classify(spy_close, spy_20, spy_50, vix_close, spy_5d)
    return RegimeResult(label, spy_close, spy_20, spy_50, vix_close, spy_5d, reason)
