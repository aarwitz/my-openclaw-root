"""
Markowitz Mean-Variance optimizer.

Adapted from aarwitz/PortfolioOptimizer to work as an importable module
(no interactive prompts, returns structured data instead of printing).

Core math:
  - Lagrangian solution for minimum-variance portfolio given a target return
  - Efficient frontier sweep over a range of target returns
  - Global minimum-variance portfolio (unconstrained on return)
  - Sharpe-optimal and Sortino-optimal portfolio selection
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import yfinance as yf


# ──────────────────────────────────────────────────────────────────────────────
# Data models
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class PriceData:
    tickers: List[str]
    start: str
    end: str
    mean_returns: np.ndarray
    covar: np.ndarray
    stds: np.ndarray


@dataclass
class PortfolioPoint:
    weights: Dict[str, float]
    expected_daily_return: float
    daily_std: float
    sharpe_ratio: float
    annualized_return: float
    annualized_std: float


@dataclass
class EfficientFrontier:
    tickers: List[str]
    start: str
    end: str
    global_min: PortfolioPoint
    sharpe_max: PortfolioPoint
    frontier_points: List[PortfolioPoint]
    montecarlo_paths: List[List[float]] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────────────────
# Data loading
# ──────────────────────────────────────────────────────────────────────────────

def load_price_data(tickers: List[str], start: str, end: str) -> PriceData:
    """Download daily closes from yfinance and compute return statistics."""
    import pandas as pd
    df = yf.download(tickers, start=start, end=end, auto_adjust=True, progress=False)["Close"]
    if isinstance(df, pd.Series):
        df = df.to_frame(name=tickers[0])
    df = df.dropna(axis=1)
    valid_tickers = list(df.columns)
    returns = df.pct_change().dropna()
    mean_returns = returns.mean().values
    covar = returns.cov().values
    stds = returns.std().values
    return PriceData(
        tickers=valid_tickers,
        start=start,
        end=end,
        mean_returns=mean_returns,
        covar=covar,
        stds=stds,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Lagrangian portfolio math (core of aarwitz/PortfolioOptimizer)
# ──────────────────────────────────────────────────────────────────────────────

def global_min_variance(covar: np.ndarray) -> np.ndarray:
    """Solve for weights of the global minimum-variance portfolio."""
    n = len(covar)
    row = np.array([np.ones(n)])       # shape (1, n)
    col = np.concatenate((row.T, np.array([[0]])), axis=0)  # shape (n+1, 1)
    A_with_bottom_row = np.concatenate((2 * covar, row), axis=0)  # (n+1, n)
    A = np.concatenate((A_with_bottom_row, col), axis=1)           # (n+1, n+1)
    b = np.concatenate((np.zeros([n, 1]), np.array([[1]])), axis=0)
    z = np.linalg.solve(A, b).flatten()
    return z[:-1]


def min_variance_at_return(covar: np.ndarray, mean_returns: np.ndarray, target_r: float) -> np.ndarray:
    """Solve for minimum-variance weights given a target expected return."""
    n = len(covar)
    ones = np.array([np.ones(n)])      # (1, n)
    e = mean_returns.reshape(1, -1)    # (1, n)

    col1 = np.concatenate((e.T, np.array([[0]]), np.array([[0]])), axis=0)   # (n+2, 1)
    col2 = np.concatenate((ones.T, np.array([[0]]), np.array([[0]])), axis=0) # (n+2, 1)

    A_with_bottom_rows = np.concatenate((2 * covar, e, ones), axis=0)        # (n+2, n)
    A = np.concatenate((A_with_bottom_rows, col1, col2), axis=1)             # (n+2, n+2)

    zeros = np.zeros((n, 1))
    b = np.concatenate((zeros, np.array([[target_r]]), np.array([[1]])), axis=0)

    z = np.linalg.solve(A, b).flatten()
    return z[:-2]


def portfolio_std(covar: np.ndarray, weights: np.ndarray) -> float:
    return float(np.sqrt(weights @ covar @ weights.T))


def portfolio_return(mean_returns: np.ndarray, weights: np.ndarray) -> float:
    return float(np.dot(mean_returns, weights))


# ──────────────────────────────────────────────────────────────────────────────
# Efficient frontier computation
# ──────────────────────────────────────────────────────────────────────────────

def build_efficient_frontier(
    data: PriceData,
    rf_daily: float = 0.0,
    n_points: int = 60,
    range_pct: float = 0.002,
    n_mc_trials: int = 0,
    mc_years: float = 1.0,
) -> EfficientFrontier:
    """Compute global-min, efficient frontier sweep, and Sharpe-max portfolio."""
    covar = data.covar
    mu = data.mean_returns
    tickers = data.tickers

    # Global min variance
    w_glob = global_min_variance(covar)
    r_glob = portfolio_return(mu, w_glob)
    std_glob = portfolio_std(covar, w_glob)

    # Sweep returns from just below to above global min
    r_min = r_glob - range_pct
    r_max = r_glob + range_pct
    target_returns = np.linspace(r_min, r_max, n_points)

    frontier: List[PortfolioPoint] = []
    best_sharpe_pt: Optional[PortfolioPoint] = None
    best_sharpe = -np.inf

    for r in target_returns:
        try:
            w = min_variance_at_return(covar, mu, r)
            std = portfolio_std(covar, w)
            sharpe = (r - rf_daily) / std if std > 0 else 0.0
            ann_ret = (1 + r) ** 252 - 1
            ann_std = std * np.sqrt(252)
            pt = PortfolioPoint(
                weights={t: float(w[i]) for i, t in enumerate(tickers)},
                expected_daily_return=float(r),
                daily_std=float(std),
                sharpe_ratio=float(sharpe),
                annualized_return=float(ann_ret),
                annualized_std=float(ann_std),
            )
            frontier.append(pt)
            if sharpe > best_sharpe:
                best_sharpe = sharpe
                best_sharpe_pt = pt
        except np.linalg.LinAlgError:
            continue

    if best_sharpe_pt is None and frontier:
        best_sharpe_pt = max(frontier, key=lambda p: p.sharpe_ratio)

    glob_pt = PortfolioPoint(
        weights={t: float(w_glob[i]) for i, t in enumerate(tickers)},
        expected_daily_return=float(r_glob),
        daily_std=float(std_glob),
        sharpe_ratio=float((r_glob - rf_daily) / std_glob) if std_glob > 0 else 0.0,
        annualized_return=float((1 + r_glob) ** 252 - 1),
        annualized_std=float(std_glob * np.sqrt(252)),
    )

    # Optional Monte Carlo paths on Sharpe-max portfolio
    mc_paths: List[List[float]] = []
    if n_mc_trials > 0 and best_sharpe_pt is not None:
        mc_paths = _monte_carlo(
            r=best_sharpe_pt.expected_daily_return,
            sigma=best_sharpe_pt.daily_std,
            years=mc_years,
            n=n_mc_trials,
        )

    return EfficientFrontier(
        tickers=tickers,
        start=data.start,
        end=data.end,
        global_min=glob_pt,
        sharpe_max=best_sharpe_pt or glob_pt,
        frontier_points=frontier,
        montecarlo_paths=mc_paths,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Monte Carlo (from aarwitz MCStockSimulator logic)
# ──────────────────────────────────────────────────────────────────────────────

def _monte_carlo(r: float, sigma: float, years: float, n: int, nper_year: int = 252) -> List[List[float]]:
    paths: List[List[float]] = []
    steps = int(nper_year * years)
    for _ in range(n):
        vals = [1.0]
        for _ in range(steps):
            ret = r + np.random.normal() * sigma
            vals.append(vals[-1] * (1 + ret))
        paths.append(vals)
    return paths


# ──────────────────────────────────────────────────────────────────────────────
# Backtest: apply model weights to a different period
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class BacktestResult:
    model_start: str
    model_end: str
    test_start: str
    test_end: str
    portfolios: List[dict]


def backtest_frontier(
    model_data: PriceData,
    test_start: str,
    test_end: str,
    n_portfolios: int = 10,
) -> BacktestResult:
    """Apply weights derived from model_data to a different test period."""
    test_data = load_price_data(model_data.tickers, test_start, test_end)
    covar = model_data.covar
    mu = model_data.mean_returns

    r_min = mu.min()
    r_max = mu.max()
    target_returns = np.linspace(r_min + 0.0001, r_max - 0.0001, n_portfolios)

    results = []
    for i, r in enumerate(target_returns):
        try:
            w = min_variance_at_return(covar, mu, r)
            model_std = portfolio_std(covar, w)
            model_r = portfolio_return(mu, w)
            test_r = portfolio_return(test_data.mean_returns, w)
            test_std = portfolio_std(test_data.covar, w)
            results.append({
                "portfolio": i + 1,
                "target_return_pct": round(r * 100, 4),
                "model_return_pct": round(model_r * 100, 4),
                "model_std_pct": round(model_std * 100, 4),
                "test_return_pct": round(test_r * 100, 4),
                "test_std_pct": round(test_std * 100, 4),
                "outperformed": bool(test_r > r),
                "weights": {t: round(float(w[j]) * 100, 2) for j, t in enumerate(model_data.tickers)},
            })
        except (np.linalg.LinAlgError, ValueError):
            continue

    return BacktestResult(
        model_start=model_data.start,
        model_end=model_data.end,
        test_start=test_start,
        test_end=test_end,
        portfolios=results,
    )
