#!/usr/bin/env python3
"""Statistical evaluation for the world model / backtest (pure stdlib, math only).

The point of these is to answer "is the edge real, or chance?" honestly:
  * binomial test of a signal's hit-rate vs a BASE RATE (not vs 0.5 — equities
    drift up, so testing against 50% manufactures fake edge),
  * Benjamini-Hochberg FDR + Bonferroni across many signals (testing 5 signals,
    one will look "significant" by luck — correct for it),
  * calibration reliability + Brier decomposition,
  * equity-curve metrics (CAGR, Sharpe, max drawdown, Calmar, VaR, alpha vs SPY).
"""

from __future__ import annotations

import math


# --- binomial test (exact, two-sided) ---------------------------------------
def _binom_pmf(k: int, n: int, p: float) -> float:
    if p <= 0.0:
        return 1.0 if k == 0 else 0.0
    if p >= 1.0:
        return 1.0 if k == n else 0.0
    return math.exp(math.lgamma(n + 1) - math.lgamma(k + 1) - math.lgamma(n - k + 1)
                    + k * math.log(p) + (n - k) * math.log(1 - p))


def binom_test(k: int, n: int, p0: float = 0.5) -> float:
    """Two-sided exact binomial p-value for k successes in n trials vs p0.
    Sums all outcomes no more probable than the observed one (standard method)."""
    if n == 0:
        return 1.0
    p0 = min(max(p0, 1e-9), 1 - 1e-9)
    obs = _binom_pmf(k, n, p0)
    tol = obs * (1 + 1e-7)
    return min(1.0, sum(_binom_pmf(i, n, p0) for i in range(n + 1) if _binom_pmf(i, n, p0) <= tol))


def benjamini_hochberg(pvals: list[float], alpha: float = 0.05) -> list[bool]:
    """Return a reject/keep mask (True = significant after FDR control at alpha)."""
    m = len(pvals)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvals[i])
    thresh_rank = -1
    for rank, idx in enumerate(order, start=1):
        if pvals[idx] <= alpha * rank / m:
            thresh_rank = rank
    keep = [False] * m
    if thresh_rank > 0:
        for rank, idx in enumerate(order, start=1):
            if rank <= thresh_rank:
                keep[idx] = True
    return keep


def bonferroni(pvals: list[float], alpha: float = 0.05) -> list[bool]:
    m = max(1, len(pvals))
    return [p <= alpha / m for p in pvals]


def ttest_mean_positive(xs: list[float]) -> tuple[float, float, float]:
    """One-sided t-test of H0: mean(xs) <= 0 vs mean > 0. Returns (mean, t, p).
    Catches edges that live in the MEAN/tail rather than the hit-rate (e.g. positive-skew
    mean-reversion: big winners, ~50% hit-rate). Normal-approx p (deterministic, no RNG)."""
    n = len(xs)
    if n < 3:
        return (sum(xs) / n if xs else 0.0, 0.0, 1.0)
    m = sum(xs) / n
    var = sum((x - m) ** 2 for x in xs) / (n - 1)
    if var <= 0:
        return (m, 0.0, 1.0 if m <= 0 else 0.0)
    t = m / math.sqrt(var / n)
    return (m, t, 0.5 * math.erfc(t / math.sqrt(2)))     # P(Z > t)


# --- calibration ------------------------------------------------------------
def reliability_curve(pairs: list[tuple[float, int]], bins: int = 10) -> list[dict]:
    """pairs = [(predicted_prob, outcome 0/1)]. Returns per-bin mean-pred vs realized."""
    out = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [(p, o) for p, o in pairs if (lo <= p < hi or (b == bins - 1 and p == 1.0))]
        if sel:
            out.append({"bin": [round(lo, 2), round(hi, 2)], "n": len(sel),
                        "mean_pred": round(sum(p for p, _ in sel) / len(sel), 4),
                        "realized": round(sum(o for _, o in sel) / len(sel), 4)})
    return out


def brier_decomposition(pairs: list[tuple[float, int]], bins: int = 10) -> dict:
    """Murphy decomposition: Brier = reliability - resolution + uncertainty."""
    n = len(pairs)
    if n == 0:
        return {}
    base = sum(o for _, o in pairs) / n
    brier = sum((p - o) ** 2 for p, o in pairs) / n
    rel = res = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [(p, o) for p, o in pairs if (lo <= p < hi or (b == bins - 1 and p == 1.0))]
        if not sel:
            continue
        nb = len(sel)
        pbar = sum(p for p, _ in sel) / nb
        obar = sum(o for _, o in sel) / nb
        rel += nb * (pbar - obar) ** 2
        res += nb * (obar - base) ** 2
    return {"brier": round(brier, 4), "reliability": round(rel / n, 4),
            "resolution": round(res / n, 4), "uncertainty": round(base * (1 - base), 4)}


# --- equity-curve metrics ---------------------------------------------------
def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _std(xs):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def equity_metrics(daily_returns: list[float], periods_per_year: int = 252) -> dict:
    """daily_returns = simple returns of the strategy. Returns CAGR/Sharpe/maxDD/Calmar/VaR95."""
    if not daily_returns:
        return {}
    eq = [1.0]
    for r in daily_returns:
        eq.append(eq[-1] * (1 + r))
    total = eq[-1] / eq[0] - 1
    yrs = len(daily_returns) / periods_per_year
    cagr = (eq[-1] / eq[0]) ** (1 / yrs) - 1 if yrs > 0 and eq[-1] > 0 else 0.0
    vol = _std(daily_returns) * math.sqrt(periods_per_year)
    sharpe = (_mean(daily_returns) * periods_per_year) / vol if vol > 0 else 0.0
    peak, mdd = eq[0], 0.0
    for v in eq:
        peak = max(peak, v)
        mdd = max(mdd, (peak - v) / peak)
    srt = sorted(daily_returns)
    var95 = srt[max(0, int(0.05 * len(srt)) - 1)] if srt else 0.0
    return {"total_return": round(total, 4), "cagr": round(cagr, 4), "vol_annual": round(vol, 4),
            "sharpe": round(sharpe, 3), "max_drawdown": round(mdd, 4),
            "calmar": round(cagr / mdd, 3) if mdd > 0 else None, "var_1d_95": round(var95, 4)}


def alpha_vs_benchmark(strat: list[float], bench: list[float], periods_per_year: int = 252) -> float:
    """Annualized return difference, strategy minus benchmark, over aligned daily returns."""
    n = min(len(strat), len(bench))
    if n == 0:
        return 0.0
    return round((_mean(strat[:n]) - _mean(bench[:n])) * periods_per_year, 4)
