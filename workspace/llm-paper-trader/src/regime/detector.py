"""
Market Regime Detection

Classifies current market regime using:
  - Rolling volatility (realized vol window)
  - Rolling trend: 50-day vs 200-day SMA crossover
  - Returns regime: BULL | BEAR | HIGH_VOL | SIDEWAYS
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

import numpy as np


class Regime:
    BULL = "bull"
    BEAR = "bear"
    HIGH_VOL = "high_vol"
    SIDEWAYS = "sideways"


@dataclass
class RegimeResult:
    regime: str
    realized_vol_annualized: float
    trend_signal: float
    description: str


def detect_regime(
    prices: List[float],
    vol_window: int = 20,
    short_window: int = 50,
    long_window: int = 200,
    high_vol_threshold: float = 0.25,
) -> RegimeResult:
    """
    Detect market regime from a list of recent prices.

    Args:
        prices: Historical daily close prices (most recent last).
        vol_window: Window for realized vol.
        short_window: Short SMA window for trend.
        long_window: Long SMA window for trend.
        high_vol_threshold: Annualized vol above this → HIGH_VOL.
    """
    arr = np.array(prices, dtype=float)
    if len(arr) < max(vol_window, long_window) + 1:
        return RegimeResult(
            regime=Regime.SIDEWAYS,
            realized_vol_annualized=0.0,
            trend_signal=0.0,
            description="Insufficient price history for regime detection.",
        )

    returns = np.diff(arr) / arr[:-1]
    realized_vol = float(np.std(returns[-vol_window:]) * np.sqrt(252))

    short_sma = float(arr[-short_window:].mean()) if len(arr) >= short_window else float(arr.mean())
    long_sma = float(arr[-long_window:].mean()) if len(arr) >= long_window else short_sma
    trend_signal = (short_sma - long_sma) / long_sma  # positive = bull, negative = bear

    if realized_vol > high_vol_threshold:
        regime = Regime.HIGH_VOL
        desc = f"High volatility ({realized_vol:.1%} ann. vol). Risk-off posture recommended."
    elif trend_signal > 0.01:
        regime = Regime.BULL
        desc = f"Bull trend (SMA signal +{trend_signal:.1%}). Momentum positive."
    elif trend_signal < -0.01:
        regime = Regime.BEAR
        desc = f"Bear trend (SMA signal {trend_signal:.1%}). Defensive allocation recommended."
    else:
        regime = Regime.SIDEWAYS
        desc = f"Sideways market (SMA signal {trend_signal:.1%}). Mean-reversion conditions."

    return RegimeResult(
        regime=regime,
        realized_vol_annualized=realized_vol,
        trend_signal=trend_signal,
        description=desc,
    )
