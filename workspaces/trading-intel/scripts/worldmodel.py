#!/usr/bin/env python3
"""Shared world-model math (pure stdlib, no third-party deps).

Used by predict.py (quant), calibrate.py (archivist), and market_debrief.py.

Everything here is deterministic. The LLM turns only supply judgment inputs
(which mechanisms apply, the lesson text); the numbers below are computed by
these functions so they are reproducible and auditable.

Core pieces:
  * Beta(alpha, beta) posterior helpers (mean + credible interval) via a
    pure-python regularized incomplete beta function.
  * Half-life time decay for mechanism observations (crowding / regime drift).
  * Log-odds combination of mechanism posteriors into a hypothesis p_correct.
  * A probabilistic return band (P10/P50/P90) per horizon.
  * Fractional-Kelly position sizing (suggestion only; Risk caps it).
"""

from __future__ import annotations

import math

# ---------------------------------------------------------------------------
# Beta distribution (regularized incomplete beta, continued-fraction form)
# ---------------------------------------------------------------------------


def _betacf(a: float, b: float, x: float) -> float:
    MAXIT = 300
    EPS = 3.0e-12
    FPMIN = 1.0e-300
    qab = a + b
    qap = a + 1.0
    qam = a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < EPS:
            break
    return h


def betai(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b) = P(Beta(a,b) <= x)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a) + math.lgamma(b) - math.lgamma(a + b)
    bt = math.exp(a * math.log(x) + b * math.log(1.0 - x) - lbeta)
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def beta_ppf(q: float, a: float, b: float) -> float:
    """Inverse CDF (quantile) of Beta(a,b) via bisection on betai."""
    if q <= 0.0:
        return 0.0
    if q >= 1.0:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(100):
        mid = 0.5 * (lo + hi)
        if betai(a, b, mid) < q:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def beta_mean(a: float, b: float) -> float:
    return a / (a + b)


def beta_ci(a: float, b: float, lo: float = 0.05, hi: float = 0.95) -> tuple[float, float]:
    return beta_ppf(lo, a, b), beta_ppf(hi, a, b)


# ---------------------------------------------------------------------------
# Time decay (crowding / regime drift): an observation `age_days` old counts as
# 0.5 ** (age_days / half_life_days) of a fresh observation.
# ---------------------------------------------------------------------------


def decay_weight(age_days: float, half_life_days: float) -> float:
    if half_life_days <= 0:
        return 1.0
    if age_days <= 0:
        return 1.0
    return 0.5 ** (age_days / half_life_days)


# ---------------------------------------------------------------------------
# Log-odds combination of mechanism posteriors -> hypothesis p_correct
# ---------------------------------------------------------------------------


def logit(p: float) -> float:
    p = min(max(p, 1e-6), 1.0 - 1e-6)
    return math.log(p / (1.0 - p))


def inv_logit(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def combine_p(
    base_rate: float,
    mechanism_terms: list[tuple[float, float]],
    evidence_quality: float,
) -> tuple[float, float]:
    """Combine mechanism posteriors into a calibrated p_correct.

    base_rate         prior probability a thesis resolves correct with no edge.
    mechanism_terms   list of (posterior_mean, weight) for each linked mechanism;
                      weight in [0,1] reflects mechanism confidence (narrow CI =>
                      high weight) and alignment with the thesis direction.
    evidence_quality  [0,1] multiplier that attenuates the combined edge toward
                      the base rate when evidence is thin/stale.

    Returns (p_correct, combined_log_odds).
    """
    base_lo = logit(base_rate)
    edge = 0.0
    for posterior, weight in mechanism_terms:
        # Each mechanism contributes its deviation from 0.5 in log-odds space,
        # scaled by its confidence weight.
        edge += weight * (logit(posterior) - logit(0.5))
    combined = base_lo + evidence_quality * edge
    return inv_logit(combined), combined


def confidence_weight(ci_low: float, ci_high: float) -> float:
    """Map a Beta credible-interval width to a [0.1, 1.0] confidence weight.

    A tight interval (well-evidenced mechanism) approaches 1.0; a wide interval
    (a fresh `candidate`) is heavily discounted toward 0.1.
    """
    width = max(0.0, min(1.0, ci_high - ci_low))
    return max(0.1, 1.0 - width)


# ---------------------------------------------------------------------------
# Probabilistic return band (P10/P50/P90) per horizon
# ---------------------------------------------------------------------------

# Per-horizon expected reward scale and idiosyncratic vol (in % return terms).
# Reward scale maps the directional edge (2*p - 1) onto an expected median
# return; vol sets the width of the P10/P90 band. Tunable via rule_proposals.
HORIZON_PROFILE = {
    "intraday":      {"reward": 1.2,  "vol": 1.5},
    "swing_1_5d":    {"reward": 3.0,  "vol": 4.0},
    "position_1_4w": {"reward": 6.0,  "vol": 8.0},
    "trend_1_3m":    {"reward": 12.0, "vol": 14.0},
    "long_6m_plus":  {"reward": 22.0, "vol": 26.0},
}

_Z90 = 1.2815515655446004  # standard normal 90th percentile


def return_band(p_correct: float, horizon: str) -> tuple[float, float, float]:
    """Return (P10, P50, P90) expected % return for the position over `horizon`."""
    prof = HORIZON_PROFILE.get(horizon, HORIZON_PROFILE["swing_1_5d"])
    edge = 2.0 * p_correct - 1.0  # in [-1, 1]
    p50 = edge * prof["reward"]
    spread = _Z90 * prof["vol"]
    return p50 - spread, p50, p50 + spread


# Trading days per horizon (for scaling annualized vol to the horizon).
HORIZON_DAYS = {
    "intraday": 1, "swing_1_5d": 3, "position_1_4w": 15,
    "trend_1_3m": 45, "long_6m_plus": 180,
}
# How much of a valuation gap (margin of safety) we expect to mean-revert over the
# horizon. Tiny at short horizons (valuation barely matters day-to-day), larger as
# the horizon lengthens. Confidence-scaled and hard-capped in return_band_v2.
HORIZON_REVERSION = {
    "intraday": 0.0, "swing_1_5d": 0.02, "position_1_4w": 0.06,
    "trend_1_3m": 0.15, "long_6m_plus": 0.30,
}


def return_band_v2(
    p_correct: float,
    horizon: str,
    realized_vol_annual: float | None = None,
    margin_of_safety: float | None = None,
    val_confidence: float = 0.0,
) -> tuple[float, float, float]:
    """Name-aware (P10, P50, P90) % return band.

    Upgrades the generic `return_band` two ways, both degrading to the old
    behavior when inputs are absent:
      * **Band width** uses the name's realized (annualized) volatility scaled to
        the horizon instead of a per-horizon constant — a 3x ETF and a utility no
        longer get the same band.
      * **P50** is nudged toward fair value by the margin of safety, scaled by how
        much reverts over the horizon and by valuation confidence, then hard-capped
        to one reward unit so a noisy DCF can never dominate the causal edge.
    """
    prof = HORIZON_PROFILE.get(horizon, HORIZON_PROFILE["swing_1_5d"])
    edge = 2.0 * p_correct - 1.0
    causal_p50 = edge * prof["reward"]

    val_pull = 0.0
    if margin_of_safety is not None:
        frac = HORIZON_REVERSION.get(horizon, 0.05)
        conf = max(0.0, min(1.0, val_confidence))
        val_pull = (margin_of_safety * 100.0) * frac * conf
        val_pull = max(-prof["reward"], min(prof["reward"], val_pull))  # cap to one reward unit
    p50 = causal_p50 + val_pull

    if realized_vol_annual and realized_vol_annual > 0:
        days = HORIZON_DAYS.get(horizon, 3)
        vol = realized_vol_annual * math.sqrt(days / 252.0) * 100.0
    else:
        vol = prof["vol"]
    spread = _Z90 * vol
    return p50 - spread, p50, p50 + spread


# ---------------------------------------------------------------------------
# Fractional-Kelly sizing (SUGGESTION ONLY — the Risk agent enforces the cap)
# ---------------------------------------------------------------------------


def kelly_fraction(
    p: float,
    up_return_pct: float,
    down_return_pct: float,
    kelly_scale: float = 0.25,
    cap: float = 0.10,
) -> float:
    """Fractional-Kelly fraction of equity to allocate.

    p                 probability the thesis resolves correct.
    up_return_pct     expected gain if correct (e.g., P90, % > 0).
    down_return_pct   expected loss if wrong   (e.g., |P10|, % > 0).
    kelly_scale       fraction of full Kelly to use (0.25 = quarter-Kelly).
    cap               hard upper bound on the suggested fraction.

    Returns a fraction in [0, cap]. Always clamped to >= 0 (no shorting via
    negative Kelly here; direction is encoded in the intent itself).
    """
    b = max(0.01, up_return_pct) / max(0.01, down_return_pct)
    q = 1.0 - p
    full = (p * b - q) / b
    frac = kelly_scale * full
    if frac <= 0:
        return 0.0
    return min(cap, frac)
