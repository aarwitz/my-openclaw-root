"""Tests for the Markowitz optimizer (offline — no network calls)."""

import numpy as np
import pytest

from src.optimizer.markowitz import (
    global_min_variance,
    min_variance_at_return,
    portfolio_std,
    portfolio_return,
    build_efficient_frontier,
    EfficientFrontier,
    PriceData,
)


def _simple_data():
    """Two-asset uncorrelated portfolio for easy hand-verification."""
    mu = np.array([0.0010, 0.0015])
    covar = np.diag([0.0001, 0.0004])  # σ = 1% and 2%
    stds = np.sqrt(np.diag(covar))
    return PriceData(
        tickers=["A", "B"],
        start="2023-01-01",
        end="2024-01-01",
        mean_returns=mu,
        covar=covar,
        stds=stds,
    )


def test_global_min_variance_weights_sum_to_one():
    data = _simple_data()
    w = global_min_variance(data.covar)
    assert abs(w.sum() - 1.0) < 1e-9


def test_global_min_variance_prefers_low_vol_asset():
    """Asset A has 1/4 the variance of B, so GMV should put more weight on A."""
    data = _simple_data()
    w = global_min_variance(data.covar)
    assert w[0] > w[1]  # more weight on A (lower variance)


def test_min_variance_at_return_weights_sum_to_one():
    data = _simple_data()
    target = 0.00125  # between the two assets' returns
    w = min_variance_at_return(data.covar, data.mean_returns, target)
    assert abs(w.sum() - 1.0) < 1e-8


def test_portfolio_return_and_std():
    data = _simple_data()
    w = np.array([0.5, 0.5])
    r = portfolio_return(data.mean_returns, w)
    assert abs(r - 0.00125) < 1e-12
    std = portfolio_std(data.covar, w)
    assert std > 0


def test_build_efficient_frontier_returns_frontier():
    data = _simple_data()
    ef = build_efficient_frontier(data, n_points=10, n_mc_trials=0)
    assert isinstance(ef, EfficientFrontier)
    assert len(ef.frontier_points) > 0
    assert ef.global_min is not None
    assert ef.sharpe_max is not None


def test_build_efficient_frontier_montecarlo():
    data = _simple_data()
    ef = build_efficient_frontier(data, n_points=5, n_mc_trials=5, mc_years=0.1)
    assert len(ef.montecarlo_paths) == 5
    # Each path starts at 1.0
    for path in ef.montecarlo_paths:
        assert abs(path[0] - 1.0) < 1e-12
