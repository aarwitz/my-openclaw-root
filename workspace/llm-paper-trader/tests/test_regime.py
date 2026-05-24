"""Tests for the market regime detector."""

import pytest
from src.regime.detector import detect_regime, Regime


def _bull_prices(n=250):
    """Steadily trending up prices."""
    import numpy as np
    return list(np.linspace(100, 200, n))


def _bear_prices(n=250):
    """Steadily trending down."""
    import numpy as np
    return list(np.linspace(200, 100, n))


def _volatile_prices(n=250):
    """Random walk with high vol."""
    import numpy as np
    np.random.seed(42)
    returns = np.random.normal(0, 0.04, n - 1)  # 4% daily std
    prices = [100.0]
    for r in returns:
        prices.append(prices[-1] * (1 + r))
    return prices


def test_bull_market_detected():
    result = detect_regime(_bull_prices(), high_vol_threshold=0.50)
    assert result.regime == Regime.BULL


def test_bear_market_detected():
    result = detect_regime(_bear_prices(), high_vol_threshold=0.50)
    assert result.regime == Regime.BEAR


def test_high_vol_detected():
    result = detect_regime(_volatile_prices(), high_vol_threshold=0.10)
    assert result.regime == Regime.HIGH_VOL


def test_insufficient_data_returns_sideways():
    result = detect_regime([100.0] * 5)
    assert result.regime == Regime.SIDEWAYS


def test_result_has_description():
    result = detect_regime(_bull_prices())
    assert isinstance(result.description, str)
    assert len(result.description) > 5
