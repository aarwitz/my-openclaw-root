#!/usr/bin/env python3
"""Real-mechanism backtest — mechanisms as declarative feature-triggers over the point-in-time
feature store, validated walk-forward with multiple-testing control.

A "mechanism" is data, not code:  {id, rationale, conds:[(feature, op, threshold)], direction, kind}.
The system evaluates ECONOMIC SEEDS *and* machine-GENERATED candidates under ONE FDR umbrella, so
new mechanisms it invents are held to the same out-of-sample bar — and only survivors earn weight.
This is the AlphaAgent discipline (arXiv 2502.16789): generate freely, but regularize + OOS-validate
so you don't curve-fit noise. The 17 hand-authored mechanisms are just one seed input; the surviving
set replaces them.

Rigor:
  * point-in-time: feature read = latest as_of <= decision date; entry = NEXT trading day's close.
  * NON-OVERLAPPING samples per ticker (spaced >= horizon) so the binomial test isn't inflated by
    autocorrelated overlapping windows.
  * graded against both SPY and a trailing-beta-adjusted SPY benchmark; a cell
    must survive the more conservative of the two tests, so high-beta rebound
    exposure cannot masquerade as alpha.
  * candidate thresholds are percentiles computed from the TRAIN period only (no test leakage).
  * bounded train/test split by date; training labels that cross the boundary are purged and test
    labels must mature before the exclusive test end. Significance is reported on the TEST
    holdout; Benjamini-Hochberg FDR + Bonferroni span every (mechanism x horizon).
  * trigger inference clusters same-date stocks into one portfolio observation
    and uses a Newey-West/HAC standard error across entry dates. Raw ticker
    samples remain descriptive; they are not treated as independent trials.
  * survivorship-aware: the frozen broad universe retains locally built delisted/failed names.

Reads state/features.sqlite (built by feature_store.py) + frozen price caches. Writes survivors to
state/features.sqlite::discovered_mechanisms unless ``--no-persist`` is set. Historical folds should
normally be non-persisting; the weekly single-split artifact is explicitly non-promotable.
"""

from __future__ import annotations

import argparse
import bisect
import glob
import json
import math
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspaces/trading-intel/scripts"))))
from connectors import fmp  # noqa: E402
import feature_store as fs  # noqa: E402
import worldmodel as wm  # noqa: E402
import worldmodel_stats as st  # noqa: E402

FEAT_DB = Path(os.path.expanduser("~/.openclaw/state/features.sqlite"))
CACHE_DIR = Path(os.path.expanduser("~/.openclaw/state/market-data-cache"))
ALLOW_NETWORK = False
HORIZONS = {"swing_5d": 5, "month_21d": 21, "quarter_63d": 63}
# Data-quality + tradability controls (the fix for penny-stock outlier contamination):
PRICE_FLOOR = 5.0                          # min entry close — exclude penny stocks
DV_FLOOR = 5_000_000                        # min entry dollar-volume — institutional tradability
WINSOR = {5: 0.25, 21: 0.50, 63: 1.00}     # cap |single-name return| per horizon (outlier control)
COST_RT = 0.002                            # round-trip transaction cost (20 bps) per trade
SHORT_BORROW_PER_DAY = 0.0001              # ~2.5%/yr borrow cost applied to short trades
BETA_LOOKBACK = 252
BETA_MIN_OBS = 126
BETA_FLOOR = 0.0
BETA_CAP = 3.0

# Economic seed mechanisms (the hypotheses). conds: list of (feature, op, threshold). op in >,<.
SEEDS = [
    ("earnings_beat",    "positive EPS surprise -> post-earnings drift up", [("eps_surprise_pct", ">", 0.05)], "long",  "event"),
    ("event_drift_up",   "big 1-day up-move -> post-event continuation",    [("ret_1d", ">", 4.0)], "long",  "event"),
    ("event_drift_dn",   "big 1-day down-move -> continuation down",        [("ret_1d", "<", -4.0)], "short", "event"),
    ("earnings_miss",    "negative EPS surprise -> drift down",             [("eps_surprise_pct", "<", -0.05)], "short", "event"),
    ("oversold_uptrend", "buy the dip within an uptrend",        [("dist_sma50", "<", -0.07), ("dist_sma200", ">", 0.0)], "long", "state"),
    ("momentum_12_1",    "12-1m momentum continuation",                    [("mom_12_1", ">", 0.20)], "long",  "state"),
    ("quality_margin",   "high net margin -> quality outperformance",      [("net_margin_ttm", ">", 0.20)], "long", "state"),
    ("growth_strong",    "strong revenue growth continuation",             [("revenue_growth_yoy", ">", 0.20)], "long", "state"),
    ("deep_drawdown",    "deep drawdown -> mean reversion",                [("drawdown_252", "<", -0.30)], "long", "state"),
    ("overbought_rsi",   "RSI overbought -> short-term reversal",          [("rsi14", ">", 75.0)], "short", "state"),
    ("cheap_pe",         "low trailing P/E -> value",                      [("pe_ttm", "<", 12.0)], "long",  "state"),
    ("expensive_pe",     "rich trailing P/E -> reversion",                 [("pe_ttm", ">", 50.0)], "short", "state"),
    ("insider_buying",   "open-market insider buying -> long",             [("insider_net_180d", ">", 0.3)], "long", "state"),
    ("insider_selling",  "heavy insider selling -> underperformance",      [("insider_net_180d", "<", -0.8)], "short", "state"),
    ("rating_upgrades",  "net analyst upgrades -> drift up",               [("rating_net_90d", ">", 0.3)], "long", "state"),
    ("sector_tailwind",  "strong sector relative strength -> long",        [("sector_rel_63d", ">", 0.10)], "long", "state"),
    ("positive_sentiment", "improving news sentiment -> drift up",         [("news_sent_30d", ">", 0.15)], "long", "state"),
    ("negative_sentiment", "deteriorating news sentiment -> underperformance", [("news_sent_30d", "<", -0.10)], "short", "state"),
    # macro-causal chains (rate move = transmission of jobs/CPI/Fed surprises)
    ("rates_up_duration", "rates rising -> long-duration/high-multiple tech underperforms (jobs->rates->lower PV)",
     [("rate_10y_chg_63d", ">", 0.30), ("pe_ttm", ">", 30.0)], "short", "state"),
    ("rates_down_duration", "rates falling -> high-multiple growth re-rates up",
     [("rate_10y_chg_63d", "<", -0.30), ("pe_ttm", ">", 30.0)], "long", "state"),
    ("credit_stress_riskoff", "credit spreads widening -> risk-off, high-beta underperforms",
     [("credit_spread_chg_63d", ">", 1.0), ("vol_20d_annual", ">", 0.40)], "short", "state"),
    # short interest (Massive/FINRA, point-in-time +8bday dissemination lag)
    ("crowded_short_rising", "rising short interest -> continued underperformance",
     [("short_int_chg_2m", ">", 0.20)], "short", "state"),
    ("high_short_squeeze", "very high days-to-cover -> upside squeeze risk",
     [("days_to_cover", ">", 7.0)], "long", "state"),
]
# Features the candidate generator is allowed to use (single-feature, complexity-capped).
GEN_FEATURES = ["rsi14", "dist_sma50", "dist_sma200", "mom_12_1", "drawdown_252",
                "vol_20d_annual", "net_margin_ttm", "revenue_growth_yoy", "pe_ttm",
                "insider_net_180d", "rating_net_90d", "sector_rel_63d",
                "news_sent_7d", "news_sent_30d", "news_vol_z",
                "rate_10y_chg_63d", "real_yield_chg_63d", "credit_spread_chg_63d", "vix_level",
                "yield_curve_10y2y", "rate_2y_level", "rate_10y_level", "rate_2y_chg_63d",
                "real_yield_10y_level", "curve_10y3m", "ig_spread_level", "ig_spread_chg_63d",
                "hy_spread_level", "vix_chg_21d", "dollar_chg_63d", "oil_chg_63d", "fedfunds_level",
                "days_to_cover", "short_int_chg_2m",
                # X attention spike (2024+ history, backfilling to 600 names) — the crowding/
                # consensus signal; earns weight only if it survives OOS+FDR like the rest
                "x_mention_vol_z",
                # LLM feature factory (P3, rubric news-v1): frontier-model-typed news events,
                # cached per batch, point-in-time at article date. Same bar as everything else.
                "llm_news_dir", "llm_news_material_ct", "llm_news_neg_mat_ct",
                # "Lazy Prices" (Cohen-Malloy-Nguyen): 10-K/Q language change vs prior same-form
                # filing (MinHash Jaccard). Paper sign: negative — changers underperform.
                "filing_delta",
                # Economic-link momentum (Cohen-Frazzini): peers' trailing 21d SPY-relative
                # return propagates to the name with a lag. Paper sign: positive.
                "peer_mom_21d",
                # Analyst price targets (FMP price-target-news, PIT by publishedDate,
                # added 2026-07-03): consensus upside, target revision momentum, and
                # coverage attention. Brav-Lehavy: target revisions carry drift.
                "pt_upside", "pt_rev_60d", "pt_count_90d"]
# dollar_vol_63d_log (computed in load_ticker) is deliberately NOT in GEN_FEATURES:
# the 2026-07-03 v6 eval showed it fattens decile L/S (+12.5→+19.1%/yr net) purely
# via a short-small-illiquid tilt while WORSENING rank IC/t/ICIR (0.0428/1.75/1.12
# → 0.039/1.57/1.01) and leaving the top-decile long side unchanged — a cost-
# understated spread, not signal. Keep the series for future tier experiments.


_MACRO: dict = {}


def _cached_massive_prices(symbol: str) -> list[dict]:
    """Load the newest complete local daily-bar snapshot without network I/O.

    Backtests must be reproducible and must not turn a missing/stale cache into
    thousands of serial provider calls. Prefer the widest 2015-present cache;
    fall back to the newest locally cached Massive range.
    """
    escaped = glob.escape(symbol.lower())
    wide = sorted(CACHE_DIR.glob(f"massive_{escaped}_1d_2015-01-01_*.json"), reverse=True)
    candidates = wide or sorted(CACHE_DIR.glob(f"massive_{escaped}_1d_*.json"), reverse=True)
    for path in candidates:
        try:
            bars = json.loads(path.read_text()).get("bars") or []
        except (OSError, json.JSONDecodeError):
            continue
        usable = [
            b for b in bars
            if b.get("t") and b.get("c") is not None and float(b["c"]) > 0
        ]
        if usable:
            return sorted(usable, key=lambda b: b["t"])
    return []


def _backtest_prices(symbol: str) -> list[dict]:
    cached = _cached_massive_prices(symbol)
    if cached or not ALLOW_NETWORK:
        return cached
    return fs._prices(symbol, 4000)


def _fred_series(series_id: str) -> list[tuple[str, float]]:
    """Read the frozen local FRED series unless network use is explicit."""
    if not ALLOW_NETWORK:
        path = CACHE_DIR / f"fred_{series_id.lower()}.json"
        try:
            rows = json.loads(path.read_text()).get("series") or []
            return [(str(d), float(v)) for d, v in rows]
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return []
    from connectors import fred
    try:
        return fred.fetch_series(series_id)
    except Exception:
        return []


def _macro_series():
    """Global point-in-time macro features from FRED (daily, non-revised market data → no look-ahead).
    The transmission signal for jobs/CPI/Fed surprises is the RATE MOVE (the surprise is already priced
    into rates). Same value for all tickers on a date → merged into each ticker's feature dict."""
    if _MACRO:
        return _MACRO
    def chg(series_id, win):
        s = _fred_series(series_id)
        return sorted((s[i][0], s[i][1] - s[i - win][1]) for i in range(win, len(s)))

    def level(series_id):
        return sorted(_fred_series(series_id))
    _MACRO.update({
        "rate_10y_chg_63d": chg("DGS10", 63),            # 10y nominal yield, 3-mo change (rates rising/falling)
        "real_yield_chg_63d": chg("DFII10", 63),         # 10y real yield 3-mo change
        "credit_spread_chg_63d": chg("BAMLH0A0HYM2", 63),  # HY OAS 3-mo change (credit stress)
        "yield_curve_10y2y": level("T10Y2Y"),            # curve level (inversion)
        "vix_level": level("VIXCLS"),
        # --- expanded macro (all DAILY market series → knowable same-day, no release-date/revision leak) ---
        "rate_2y_level": level("DGS2"),                  # short rate (Fed path)
        "rate_10y_level": level("DGS10"),                # long rate level
        "rate_2y_chg_63d": chg("DGS2", 63),              # front-end repricing (3-mo)
        "real_yield_10y_level": level("DFII10"),         # 10y real yield level (financial conditions)
        "curve_10y3m": level("T10Y3M"),                  # 10y-3m slope (recession signal)
        "ig_spread_level": level("BAMLC0A0CM"),          # IG OAS level (credit risk appetite)
        "ig_spread_chg_63d": chg("BAMLC0A0CM", 63),      # IG OAS 3-mo change
        "hy_spread_level": level("BAMLH0A0HYM2"),        # HY OAS level (credit stress level)
        "vix_chg_21d": chg("VIXCLS", 21),                # 1-mo vol momentum (regime turns)
        "dollar_chg_63d": chg("DTWEXBGS", 63),           # broad USD 3-mo change (global flows / risk)
        "oil_chg_63d": chg("DCOILWTICO", 63),            # WTI 3-mo change (growth + inflation impulse)
        "fedfunds_level": level("DFF"),                  # effective fed funds (policy stance)
    })
    return _MACRO


def load_ticker(conn, ticker):
    rows = conn.execute("SELECT name, as_of, value FROM features WHERE ticker=? ORDER BY as_of", (ticker,)).fetchall()
    feats: dict[str, list] = {}
    for name, as_of, val in rows:
        feats.setdefault(name, []).append((as_of, val))
    # Frozen local Massive snapshot by default; explicit --allow-network is
    # required to refresh missing data.
    px = _backtest_prices(ticker)
    dates = [b["t"] for b in px]
    close = {b["t"]: b["c"] for b in px}
    dvol = {b["t"]: b["c"] * b.get("v", 0) for b in px}    # dollar-volume for liquidity floor
    # derive pe_ttm per trading day = close / eps_ttm(as-of)
    eps = feats.get("eps_ttm", [])
    if eps:
        eps_dates = [e[0] for e in eps]
        pe = []
        for d in dates:
            j = bisect.bisect_right(eps_dates, d) - 1
            if j >= 0 and eps[j][1] and eps[j][1] > 0:
                pe.append((d, close[d] / eps[j][1]))
        if pe:
            feats["pe_ttm"] = pe
    # trailing-63d average dollar volume, log10 — the liquidity/size characteristic.
    # Point-in-time by construction (price bars only). Lets the ranker condition
    # signals on tier: X attention is worth ~0.056 IC in mega-caps but ~0.034
    # pooled across 600 names (2026-07-02 finding) — the tree needs a size axis
    # to exploit that instead of averaging it away.
    if dates:
        vals = [dvol.get(d) or 0.0 for d in dates]
        prefix = [0.0]
        for v in vals:
            prefix.append(prefix[-1] + v)
        dv_series = []
        for i, d in enumerate(dates):
            j = max(0, i - 62)
            avg = (prefix[i + 1] - prefix[j]) / (i - j + 1)
            if avg > 0:
                dv_series.append((d, math.log10(avg)))
        if dv_series:
            feats["dollar_vol_63d_log"] = dv_series
    # 1-day close-to-close return (%) — the big-move event trigger. Corpus finding
    # 2026-07-23/24: +2.75%/10td average continuation after |1d| >= 4% moves on the tracked
    # universe (walk-forward, post-cutoff). Formalized here for the 20-year FDR test.
    # Point-in-time: value for date d is knowable at d's close; the engine enters at the
    # NEXT trading day's close, so there is no look-ahead.
    if len(dates) >= 2:
        r1 = []
        for i in range(1, len(dates)):
            p0 = close[dates[i - 1]]
            if p0:
                r1.append((dates[i], (close[dates[i]] / p0 - 1.0) * 100.0))
        if r1:
            feats["ret_1d"] = r1

    # pre-sort feature as_of lists for bisect
    fkeys = {n: [a for a, _ in v] for n, v in feats.items()}
    for k, series in _macro_series().items():     # merge global macro features (same series for all tickers)
        if series:
            feats[k] = series
            fkeys[k] = [a for a, _ in series]
    return {"dates": dates, "close": close, "dvol": dvol, "feats": feats, "fkeys": fkeys}


def fval(td, name, d):
    """Point-in-time feature value for ticker-data `td`, feature `name`, as of date `d`."""
    v = td["feats"].get(name)
    if not v:
        return None
    j = bisect.bisect_right(td["fkeys"][name], d) - 1
    return v[j][1] if j >= 0 else None


def holds(td, conds, d):
    for name, op, thr in conds:
        x = fval(td, name, d)
        if x is None:
            return False
        if op == ">" and not x > thr:
            return False
        if op == "<" and not x < thr:
            return False
    return True


def spy_ret(spy, d_entry, d_exit):
    dk = spy["dk"]
    i = bisect.bisect_right(dk, d_entry) - 1
    j = bisect.bisect_right(dk, d_exit) - 1
    if i < 0 or j < 0:
        return None
    ci, cj = spy["close"][dk[i]], spy["close"][dk[j]]
    return cj / ci - 1 if ci else None


def rolling_beta(td, spy, decision_date):
    """Estimate trailing market beta using information available at decision time.

    The estimate uses at most 252 prior close-to-close observations, requires
    126 aligned observations, and is clipped to a conservative long-only range.
    Samples without enough history are ineligible for the beta-robust test
    instead of silently receiving beta=1.
    """
    cache = td.setdefault("_beta_cache", {})
    if decision_date in cache:
        return cache[decision_date]
    dates = td["dates"]
    end = bisect.bisect_right(dates, decision_date)
    start = max(1, end - BETA_LOOKBACK)
    xs, ys = [], []
    for i in range(start, end):
        d0, d1 = dates[i - 1], dates[i]
        c0, c1 = td["close"].get(d0), td["close"].get(d1)
        market = spy_ret(spy, d0, d1)
        if not c0 or c1 is None or market is None:
            continue
        stock = c1 / c0 - 1.0
        if not math.isfinite(stock) or not math.isfinite(market):
            continue
        xs.append(market)
        ys.append(stock)
    if len(xs) < BETA_MIN_OBS:
        cache[decision_date] = None
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    variance = sum((x - mx) ** 2 for x in xs)
    if variance <= 0:
        cache[decision_date] = None
        return None
    beta = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / variance
    beta = max(BETA_FLOOR, min(BETA_CAP, beta))
    cache[decision_date] = beta
    return beta


def _sample_candidates(td, spy, conds, kind, H, event_feat=None):
    """Yield valid samples before evaluation-window filtering and spacing."""
    dates = td["dates"]
    n = len(dates)
    # candidate fire dates
    if kind == "event" and event_feat:
        fire = [a for a in td["fkeys"].get(event_feat, []) if a in td["close"]]
    else:
        fire = dates[::5]  # weekly cadence reduces overlap before the spacing filter
    for d in fire:
        if not holds(td, conds, d):
            continue
        # entry = next trading day strictly after d
        k = bisect.bisect_right(dates, d)
        if k >= n:
            continue
        ent = k
        ex = ent + H
        if ex >= n:
            continue
        d_ent, d_ex = dates[ent], dates[ex]
        c_ent, c_ex = td["close"][d_ent], td["close"][d_ex]
        sp = spy_ret(spy, d_ent, d_ex)
        if not c_ent or sp is None:
            continue
        if c_ent < PRICE_FLOOR or td["dvol"].get(d_ent, 0) < DV_FLOOR:   # liquidity / penny filter
            continue
        fwd = c_ex / c_ent - 1
        cap = WINSOR.get(H, 1.0)                                          # winsorize outlier returns
        fwd = max(-cap, min(cap, fwd))
        yield ent, d_ent, d_ex, fwd, sp, rolling_beta(td, spy, d)


def samples_for(
    td,
    spy,
    conds,
    kind,
    H,
    event_feat=None,
    *,
    entry_start=None,
    entry_end=None,
    exit_before=None,
    include_exit=False,
    include_beta=False,
):
    """Return non-overlapping, point-in-time samples for one ticker.

    ``entry_start`` is inclusive; ``entry_end`` and ``exit_before`` are
    exclusive. Filtering happens before the spacing state advances, so a
    sample outside a hidden fold cannot suppress a sample inside it.
    ``include_exit`` is opt-in to preserve the historical 3-tuple API used by
    diagnostic scripts.
    """
    out, last_idx = [], -10 ** 9
    for ent, d_ent, d_ex, fwd, sp, beta in _sample_candidates(
        td, spy, conds, kind, H, event_feat
    ):
        if entry_start is not None and d_ent < entry_start:
            continue
        if entry_end is not None and d_ent >= entry_end:
            continue
        if exit_before is not None and d_ex >= exit_before:
            continue
        if ent - last_idx < H:          # enforce non-overlap inside this evaluation window
            continue
        last_idx = ent
        if include_exit and include_beta:
            out.append((d_ent, d_ex, fwd, sp, beta))
        elif include_exit:
            out.append((d_ent, d_ex, fwd, sp))
        elif include_beta:
            out.append((d_ent, fwd, sp, beta))
        else:
            out.append((d_ent, fwd, sp))
    return out


def split_samples_for(
    td, spy, conds, kind, H, event_feat, *, train_start, test_start, test_end,
    include_beta=False,
):
    """Build purged train and bounded test cohorts in one candidate scan.

    Train and test keep independent non-overlap cursors. This preserves the
    boundary semantics of two ``samples_for`` calls without evaluating every
    mechanism twice over every ticker.
    """
    cohorts = {"train": [], "test": []}
    last = {"train": -10 ** 9, "test": -10 ** 9}
    for ent, d_ent, d_ex, fwd, sp, beta in _sample_candidates(
        td, spy, conds, kind, H, event_feat
    ):
        cohort = None
        if (
            (train_start is None or d_ent >= train_start)
            and d_ent < test_start
            and d_ex < test_start
        ):
            cohort = "train"
        elif (
            d_ent >= test_start
            and (test_end is None or (d_ent < test_end and d_ex < test_end))
        ):
            cohort = "test"
        if cohort is None or ent - last[cohort] < H:
            continue
        last[cohort] = ent
        row = (d_ent, fwd, sp, beta) if include_beta else (d_ent, fwd, sp)
        cohorts[cohort].append(row)
    return cohorts["train"], cohorts["test"]


def _ttest_moments(n, s, ss):
    """One-sided t-test (mean>0) from streaming moments: n, sum, sum-of-squares."""
    if n < 3:
        return (s / n if n else 0.0, 1.0)
    m = s / n
    var = (ss - n * m * m) / (n - 1)
    if var <= 0:
        return (m, 1.0 if m <= 0 else 0.0)
    t = m / math.sqrt(var / n)
    return (m, 0.5 * math.erfc(t / math.sqrt(2)))


def _hac_mean_p(series: list[float], max_lag: int) -> tuple[float, float]:
    """One-sided mean>0 test with Newey-West autocorrelation correction."""
    n = len(series)
    if n < 3:
        return (sum(series) / n if n else 0.0, 1.0)
    mean = sum(series) / n
    residual = [x - mean for x in series]
    lag = min(max(0, int(max_lag)), n - 1)
    long_run_var = sum(x * x for x in residual) / n
    for k in range(1, lag + 1):
        gamma = sum(
            residual[i] * residual[i - k] for i in range(k, n)
        ) / n
        long_run_var += 2.0 * (1.0 - k / (lag + 1.0)) * gamma
    if long_run_var <= 0:
        return (mean, 1.0 if mean <= 0 else 0.0)
    standard_error = math.sqrt(long_run_var / n)
    if standard_error <= 0:
        return (mean, 1.0 if mean <= 0 else 0.0)
    z = mean / standard_error
    return mean, 0.5 * math.erfc(z / math.sqrt(2))


def gen_candidates(pools):
    """The system CREATES mechanisms: single-feature triggers at TRAIN-derived quintiles, both
    directions (no test leakage). The AlphaAgent originality/complexity guard = single-feature, deduped."""
    out = []
    for f, vals in pools.items():
        if len(vals) < 200:
            continue
        vals.sort()
        p20, p80 = vals[int(0.20 * len(vals))], vals[int(0.80 * len(vals))]
        out += [
            (f"gen_{f}_hi_long",  f"generated: {f} top quintile -> long",   [(f, ">", round(p80, 4))], "long",  "state"),
            (f"gen_{f}_lo_long",  f"generated: {f} bottom quintile -> long", [(f, "<", round(p20, 4))], "long",  "state"),
            (f"gen_{f}_hi_short", f"generated: {f} top quintile -> short",   [(f, ">", round(p80, 4))], "short", "state"),
            (f"gen_{f}_lo_short", f"generated: {f} bottom quintile -> short", [(f, "<", round(p20, 4))], "short", "state"),
        ]
    return out


# AlphaAgent-style multi-feature hypotheses: complexity-capped at 2 features, each pair
# economically motivated (hypothesis-alignment), not a blind C(9,2) sweep.
# Feature side is hi=>p80 / lo=>p20; trade direction is explicit and never
# inferred from prose.
MULTI_PAIRS = [
    ("pe_ttm", "lo", "mom_12_1", "hi", "long", "value + momentum (cheap and rising)"),
    ("pe_ttm", "lo", "net_margin_ttm", "hi", "long", "value + quality (cheap and profitable)"),
    ("drawdown_252", "lo", "dist_sma200", "hi", "long", "oversold within a long uptrend"),
    ("revenue_growth_yoy", "hi", "mom_12_1", "hi", "long", "growth + momentum"),
    ("vol_20d_annual", "lo", "pe_ttm", "lo", "long", "low-vol + value (quality value)"),
    ("net_margin_ttm", "hi", "revenue_growth_yoy", "hi", "long", "quality + growth (compounders)"),
    ("rsi14", "lo", "pe_ttm", "lo", "long", "oversold + cheap"),
    ("mom_12_1", "hi", "vol_20d_annual", "lo", "long", "momentum + low volatility"),
    ("sector_rel_63d", "hi", "mom_12_1", "hi", "long", "sector tailwind + name momentum (supercycle)"),
    ("insider_net_180d", "hi", "pe_ttm", "lo", "long", "insider buying + cheap"),
    ("rating_net_90d", "hi", "revenue_growth_yoy", "hi", "long", "analyst upgrades + growth"),
    ("sector_rel_63d", "hi", "drawdown_252", "lo", "long", "pullback in a hot sector"),
    ("news_sent_7d", "lo", "pe_ttm", "lo", "long", "bearish news + cheap = contrarian overreaction (GOOG/MSFT)"),
    ("news_vol_z", "hi", "news_sent_7d", "hi", "long", "positive news spike = catalyst drift"),
    ("news_sent_30d", "hi", "mom_12_1", "hi", "long", "improving sentiment + momentum"),
    ("rate_10y_chg_63d", "lo", "pe_ttm", "hi", "long", "rates falling + high-duration growth -> long"),
    ("rate_10y_chg_63d", "hi", "pe_ttm", "hi", "short", "rates rising + high-duration tech -> short (duration repricing)"),
    ("credit_spread_chg_63d", "hi", "vol_20d_annual", "hi", "short", "credit stress + high-beta -> short"),
    ("vix_level", "hi", "drawdown_252", "lo", "long", "high VIX + deep drawdown -> capitulation bounce"),
    ("days_to_cover", "hi", "drawdown_252", "lo", "long", "crowded short + deep drawdown -> squeeze bounce"),
    ("short_int_chg_2m", "hi", "mom_12_1", "lo", "short", "rising shorts + weak momentum -> underperform"),
]


def gen_multi(pools):
    """2-feature conjunction mechanisms from TRAIN-derived quintiles (no test leakage)."""
    q = {}
    for f, vals in pools.items():
        if len(vals) >= 200:
            vals.sort()
            q[f] = (vals[int(0.20 * len(vals))], vals[int(0.80 * len(vals))])
    out = []
    for fa, sa, fb, sb, direction, label in MULTI_PAIRS:
        if fa not in q or fb not in q:
            continue
        ca = (fa, ">", round(q[fa][1], 4)) if sa == "hi" else (fa, "<", round(q[fa][0], 4))
        cb = (fb, ">", round(q[fb][1], 4)) if sb == "hi" else (fb, "<", round(q[fb][0], 4))
        out.append((
            f"multi_{fa}_{sa}_{fb}_{sb}", f"generated 2-feature: {label}",
            [ca, cb], direction, "state",
        ))
    return out


def _validate_window(train_start, test_start, test_end):
    if train_start is not None and train_start >= test_start:
        raise ValueError("train_start must be earlier than test_start")
    if test_end is not None and test_start >= test_end:
        raise ValueError("test_end must be later than test_start")


def run(
    universe,
    spy,
    test_start,
    test_end=None,
    train_start=None,
    excluded_features=None,
    coverage_report=None,
):
    """Run one purged historical fold while streaming one ticker at a time.

    The test interval is ``[test_start, test_end)``. Training entries are
    restricted to ``[train_start, test_start)`` and their label exits must also
    precede ``test_start``. Test labels must mature before ``test_end``. This
    makes the boundaries real data masks rather than reporting labels.
    """
    _validate_window(train_start, test_start, test_end)
    excluded = set(excluded_features or ())
    active_features = [feature for feature in GEN_FEATURES if feature not in excluded]
    REBAL = [
        d for d in spy["dk"][252::21]
        if d >= test_start and (test_end is None or d < test_end)
    ]                                               # ~monthly grid for test factors
    conn = sqlite3.connect(FEAT_DB, timeout=60.0)
    conn.execute("BEGIN")  # one immutable SQLite snapshot for both streaming passes

    # ---- PASS 1 ----
    base = {h: [0, 0] for h in HORIZONS}             # [hits, n]
    pools = {f: [] for f in active_features}
    cross = {f: {} for f in active_features}         # f -> {rebal_date: [(val, fwd, spy_fwd)]}
    nseen = 0
    pass1_loaded, pass1_empty, pass1_errors = [], [], {}
    for t in universe:
        try:
            td = load_ticker(conn, t)
        except Exception as exc:
            pass1_errors[t] = {
                "type": type(exc).__name__,
                "message": str(exc)[:300],
            }
            continue
        if not td["dates"]:
            pass1_empty.append(t)
            continue
        nseen += 1
        pass1_loaded.append(t)
        for hn, H in HORIZONS.items():
            for d, fwd, sp, beta in samples_for(
                td, spy, [], "state", H,
                entry_start=train_start,
                entry_end=test_start,
                exit_before=test_start,
                include_beta=True,
            ):
                if beta is None:
                    continue
                base[hn][1] += 1; base[hn][0] += 1 if fwd > beta * sp else 0
        for f in active_features:
            pools[f] += [
                v for a, v in td["feats"].get(f, [])
                if (train_start is None or a >= train_start) and a < test_start and v is not None
            ][::3]
        dates = td["dates"]; n = len(dates)
        for rd in REBAL:
            k = bisect.bisect_right(dates, rd)
            if k >= n or k + 21 >= n:
                continue
            if test_end is not None and dates[k + 21] >= test_end:
                continue
            c_ent, c_ex = td["close"][dates[k]], td["close"][dates[k + 21]]
            sp = spy_ret(spy, dates[k], dates[k + 21])
            if not c_ent or sp is None:
                continue
            if c_ent < PRICE_FLOOR or td["dvol"].get(dates[k], 0) < DV_FLOOR:
                continue
            fwd = c_ex / c_ent - 1
            beta = rolling_beta(td, spy, rd)
            if beta is None:
                continue
            cap = WINSOR[21]
            fwd = max(-cap, min(cap, fwd))
            for f in active_features:
                v = fval(td, f, rd)
                if v is not None:
                    cross[f].setdefault(rd, []).append((v, fwd, sp, beta))
    base_long = {h: (base[h][0] / base[h][1] if base[h][1] else 0.5) for h in HORIZONS}

    seeds = [
        mechanism for mechanism in SEEDS
        if all(condition[0] not in excluded for condition in mechanism[2])
    ]
    mechs = seeds + gen_candidates(pools) + gen_multi(pools)
    cellmeta = {(m[0], hn): m for m in mechs for hn in HORIZONS}
    cells = {k: [0, 0, 0, 0] for k in cellmeta}   # [n_tr,h_tr,n_te,h_te]
    # Same-date names share market/sector shocks. Collapse their test excess
    # returns to one equal-weight portfolio observation before inference.
    test_date_clusters = {k: {} for k in cellmeta}  # {(mid,horizon): {entry_date: [sum,n]}}
    beta_date_clusters = {k: {} for k in cellmeta}
    test_tickers = {k: set() for k in cellmeta}
    pass2_loaded, pass2_empty, pass2_errors = [], [], {}

    # ---- PASS 2: trigger moments ----
    for t in universe:
        try:
            td = load_ticker(conn, t)
        except Exception as exc:
            pass2_errors[t] = {
                "type": type(exc).__name__,
                "message": str(exc)[:300],
            }
            continue
        if not td["dates"]:
            pass2_empty.append(t)
            continue
        pass2_loaded.append(t)
        for (mid, rationale, conds, direction, kind) in mechs:
            evfeat = conds[0][0] if kind == "event" else None
            for hn, H in HORIZONS.items():
                c = cells[(mid, hn)]
                cost = COST_RT + (SHORT_BORROW_PER_DAY * H if direction == "short" else 0.0)
                train_samples, test_samples = split_samples_for(
                    td, spy, conds, kind, H, evfeat,
                    train_start=train_start, test_start=test_start, test_end=test_end,
                    include_beta=True,
                )
                for d, fwd, sp, beta in train_samples:
                    if beta is None:
                        continue
                    benchmark = beta * sp
                    win = (fwd > benchmark) if direction == "long" else (fwd < benchmark)
                    c[0] += 1; c[1] += int(win)
                for d, fwd, sp, beta in test_samples:
                    if beta is None:
                        continue
                    benchmark = beta * sp
                    win = (fwd > benchmark) if direction == "long" else (fwd < benchmark)
                    exc = ((fwd - sp) if direction == "long" else (sp - fwd)) - cost
                    beta_exc = (
                        (fwd - benchmark) if direction == "long"
                        else (benchmark - fwd)
                    ) - cost
                    c[2] += 1; c[3] += int(win)
                    bucket = test_date_clusters[(mid, hn)].setdefault(d, [0.0, 0])
                    bucket[0] += exc
                    bucket[1] += 1
                    beta_bucket = beta_date_clusters[(mid, hn)].setdefault(d, [0.0, 0])
                    beta_bucket[0] += beta_exc
                    beta_bucket[1] += 1
                    test_tickers[(mid, hn)].add(t)
    conn.close()

    if coverage_report is not None:
        coverage_report.clear()
        coverage_report.update({
            "universe_n": len(universe),
            "loaded_n": len(pass1_loaded),
            "loaded_symbols": pass1_loaded,
            "empty_symbols": pass1_empty,
            "load_errors": pass1_errors,
            "pass2_loaded_n": len(pass2_loaded),
            "pass2_empty_symbols": pass2_empty,
            "pass2_load_errors": pass2_errors,
            "pass_mismatch": sorted(set(pass1_loaded) ^ set(pass2_loaded)),
        })

    results, tp, keys = [], [], []
    for (mid, hn), (_, rationale, conds, direction, kind) in cellmeta.items():
        H = HORIZONS[hn]
        n_tr, h_tr, n_te, h_te = cells[(mid, hn)]
        base_dir = base_long[hn] if direction == "long" else 1 - base_long[hn]
        date_series = [
            total / count
            for _, (total, count) in sorted(test_date_clusters[(mid, hn)].items())
            if count
        ]
        beta_date_series = [
            total / count
            for _, (total, count) in sorted(beta_date_clusters[(mid, hn)].items())
            if count
        ]
        cluster_n = len(date_series)
        ticker_n = len(test_tickers[(mid, hn)])
        # Entry-date portfolios can still overlap for H sessions. HAC with up
        # to H date lags is conservative for event mechanisms and state scans.
        m_exc, p_mean = _hac_mean_p(date_series, H)
        beta_m_exc, beta_p_mean = _hac_mean_p(beta_date_series, H)
        robust_p = max(p_mean, beta_p_mean)
        p_hit = None  # raw-name binomial independence is not defensible
        a_, b_ = 1 + h_tr, 1 + (n_tr - h_tr)
        results.append({"id": mid, "rationale": rationale, "horizon": hn, "direction": direction,
                        "conds": conds, "kind": kind, "base": round(base_dir, 3), "tr_n": n_tr, "te_n": n_te,
                        "cluster_n": cluster_n,
                        "ticker_n": ticker_n,
                        "hit_te": round(h_te / n_te, 3) if n_te else None,
                        "alpha_te_pct": round(100 * m_exc, 3) if cluster_n else None,
                        "beta_neutral_alpha_te_pct": round(100 * beta_m_exc, 3) if cluster_n else None,
                        "spy_test_p_raw": p_mean,
                        "beta_neutral_test_p_raw": beta_p_mean,
                        "test_p": round(robust_p, 5), "test_p_raw": robust_p, "hit_p": p_hit,
                        "weight_mean": round(wm.beta_mean(a_, b_), 3)})
        # A time-series effect in one or two stocks is not a portable market
        # mechanism. Require both independent entry-date clusters and
        # cross-sectional breadth before a hypothesis enters multiplicity
        # correction or becomes promotion-eligible.
        if cluster_n >= 30 and ticker_n >= 20:
            tp.append(robust_p); keys.append((mid, hn, "trig"))

    # ---- cross-sectional factor results ----
    for f, buckets in cross.items():
        for variant, dirn in (("hi", "long"), ("lo", "long"), ("ls", "long_short")):
            series, beta_series = [], []
            for rd in sorted(buckets):
                rows = sorted(buckets[rd], key=lambda x: x[0])
                if len(rows) < 20:
                    continue
                k = max(2, int(0.2 * len(rows)))
                if variant == "hi":
                    series.append(sum(fw - sp for _, fw, sp, _ in rows[-k:]) / k - COST_RT)
                    beta_series.append(sum(fw - beta * sp for _, fw, sp, beta in rows[-k:]) / k - COST_RT)
                elif variant == "lo":
                    series.append(sum(fw - sp for _, fw, sp, _ in rows[:k]) / k - COST_RT)
                    beta_series.append(sum(fw - beta * sp for _, fw, sp, beta in rows[:k]) / k - COST_RT)
                else:
                    series.append(sum(fw for _, fw, _, _ in rows[-k:]) / k - sum(fw for _, fw, _, _ in rows[:k]) / k - 2 * COST_RT)
                    beta_series.append(
                        sum(fw - beta * sp for _, fw, sp, beta in rows[-k:]) / k
                        - sum(fw - beta * sp for _, fw, sp, beta in rows[:k]) / k
                        - 2 * COST_RT
                    )
            if len(series) < 8:
                continue
            # Monthly cross-sectional portfolios do not overlap, but adjacent
            # portfolio returns can still be serially correlated. Use the
            # same clustered/HAC inference contract instead of an iid t-test.
            m, p = _hac_mean_p(series, 1)
            beta_m, beta_p = _hac_mean_p(beta_series, 1)
            robust_p = max(p, beta_p)
            mid = f"xs_{f}_{variant}"
            results.append({"id": mid, "rationale": f"cross-sectional {f} {variant} quintile, monthly 21d",
                            "horizon": "month_21d", "direction": dirn, "conds": [[f, variant, 0.2]], "kind": "cross",
                            "base": 0.5, "tr_n": 0, "te_n": len(series),
                            "cluster_n": len(series), "hit_te": None,
                            "ticker_n": max(
                                (len(rows) for rows in buckets.values()),
                                default=0,
                            ),
                            "alpha_te_pct": round(100 * m, 3),
                            "beta_neutral_alpha_te_pct": round(100 * beta_m, 3),
                            "spy_test_p_raw": p,
                            "beta_neutral_test_p_raw": beta_p,
                            "test_p": round(robust_p, 5),
                            "test_p_raw": robust_p, "hit_p": None,
                            "weight_mean": None})
            if results[-1]["cluster_n"] >= 30 and results[-1]["ticker_n"] >= 20:
                tp.append(robust_p); keys.append((mid, "month_21d", "cross"))

    keep = st.benjamini_hochberg(tp, 0.05); bonf = st.bonferroni(tp, 0.05)
    sig = {(keys[i][0], keys[i][1]): {"fdr": keep[i], "bonf": bonf[i]} for i in range(len(keys))}
    for r in results:
        r["sig"] = sig.get((r["id"], r["horizon"]), {"fdr": False, "bonf": False})
    return results, base_long, mechs, nseen


def persist(
    results,
    test_start,
    evaluation_label,
    price_data_cutoff,
    universe_n,
    *,
    test_end=None,
    train_start=None,
):
    conn = sqlite3.connect(FEAT_DB, timeout=60.0)
    conn.execute("DROP TABLE IF EXISTS discovered_mechanisms")
    conn.execute("""CREATE TABLE discovered_mechanisms(
        id TEXT, horizon TEXT, direction TEXT, rationale TEXT, conds_json TEXT, kind TEXT,
        base REAL, tr_n INT, te_n INT, cluster_n INT, ticker_n INT, hit_te REAL, alpha_te_pct REAL, test_p REAL,
        fdr_sig INT, bonf_sig INT, weight_mean REAL, evaluation_label TEXT, test_start TEXT,
        test_end TEXT, train_start TEXT, price_data_cutoff TEXT, universe_n INT, created_at TEXT)""")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for r in results:
        conn.execute("INSERT INTO discovered_mechanisms VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (r["id"], r["horizon"], r["direction"], r["rationale"], json.dumps(r["conds"]),
                      r["kind"], r["base"], r["tr_n"], r["te_n"], r["cluster_n"], r["ticker_n"],
                      r["hit_te"], r["alpha_te_pct"], r["test_p"], int(r["sig"]["fdr"]),
                      int(r["sig"]["bonf"]), r["weight_mean"], evaluation_label,
                      test_start, test_end, train_start, price_data_cutoff, universe_n, now))
    conn.commit(); conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", required=True, help="comma list, or ALL = every built ticker")
    ap.add_argument("--test-start", default="2020-06-18", help="OOS holdout starts here (train < this)")
    ap.add_argument("--test-end", default=None,
                    help="exclusive OOS end; labels maturing on/after this date stay hidden")
    ap.add_argument("--train-start", default=None,
                    help="optional inclusive training-entry start (expanding history by default)")
    ap.add_argument("--evaluation-label", default="development_reused_holdout",
                    help="provenance label; the default holdout has been reused for development "
                         "and is not clean final evidence")
    ap.add_argument("--allow-network", action="store_true",
                    help="refresh missing price/macro series from providers; default is frozen local cache")
    ap.add_argument("--no-persist", action="store_true",
                    help="do not replace the weekly discovered_mechanisms development artifact")
    ap.add_argument("--exclude-feature", action="append", default=[],
                    help="exclude a feature family from seeds, generated candidates, and cross factors")
    a = ap.parse_args()
    global ALLOW_NETWORK
    ALLOW_NETWORK = bool(a.allow_network)
    if a.universe.strip().upper() == "ALL":
        c = sqlite3.connect(FEAT_DB)
        universe = [r[0] for r in c.execute("SELECT DISTINCT ticker FROM features WHERE source='price'")]
        c.close()
    else:
        universe = [s.strip().upper() for s in a.universe.split(",") if s.strip()]
    spx = _backtest_prices("SPY")
    if not spx:
        raise RuntimeError("no cached SPY bars; refresh market data or use --allow-network")
    spy = {"close": {b["t"]: b["c"] for b in spx}, "dk": [b["t"] for b in spx]}

    results, base, mechs, nseen = run(
        universe, spy, a.test_start, test_end=a.test_end, train_start=a.train_start,
        excluded_features=a.exclude_feature,
    )
    if not a.no_persist:
        persist(
            results, a.test_start, a.evaluation_label, spx[-1]["t"], len(universe),
            test_end=a.test_end, train_start=a.train_start,
        )

    ntrig = sum(1 for r in results if r["kind"] != "cross")
    ncross = sum(1 for r in results if r["kind"] == "cross")
    print(f"\n=== REAL-MECHANISM BACKTEST ===  {nseen}/{len(universe)} names with cached bars "
          f"(incl. delisted)  test=[{a.test_start},{a.test_end or 'latest'})  "
          f"train_start={a.train_start or 'earliest'}  price cutoff={spx[-1]['t']}")
    if a.evaluation_label == "development_reused_holdout":
        print("WARNING: this holdout has informed prior iterations; treat it as development "
              "validation, not untouched production-edge evidence.")
    seed_ids = {mechanism[0] for mechanism in SEEDS}
    nseed = sum(mechanism[0] in seed_ids for mechanism in mechs)
    print(f"tested: {ntrig} trigger cells ({nseed} seeds + {len(mechs)-nseed} machine-generated, x{len(HORIZONS)} horizons) + {ncross} cross-sectional factors")
    print("base rate P(beat SPY): " + "  ".join(f"{h}={base[h]:.3f}" for h in HORIZONS))
    surv = sorted([r for r in results if r["sig"]["fdr"]], key=lambda r: r["test_p"])
    print("\nSURVIVORS (FDR-significant positive OOS mean-alpha):  ** = also Bonferroni")
    print(f"  {'mechanism':24} {'horizon':10} {'dir':10} {'n_te':>5} {'dates':>5} "
          f"{'names':>5} {'alpha%':>7} {'p':>8} {'hit':>5} {'wt':>5}")
    if not surv:
        print("   (none survived — the honest result)")
    for r in surv:
        mark = "**" if r["sig"]["bonf"] else "*"
        hit = f"{r['hit_te']:.2f}" if r["hit_te"] is not None else "  - "
        wt = f"{r['weight_mean']:.2f}" if r["weight_mean"] is not None else "  - "
        print(f"  {r['id']:24} {r['horizon']:10} {r['direction']:10} {r['te_n']:>5} "
              f"{r['cluster_n']:>5} {r['ticker_n']:>5} {r['alpha_te_pct']:>7.3f} {r['test_p']:>8.5f} "
              f"{hit:>5} {wt:>5} {mark}")
    if a.no_persist:
        print(f"\n(not persisted; evaluated {len(results)} development rows)")
    else:
        print(f"\n(persisted all {len(results)} rows -> features.sqlite::discovered_mechanisms)")
    print("\nTop 12 by OOS mean-alpha (context, pre-correction):")
    for r in sorted([x for x in results if x["alpha_te_pct"] is not None], key=lambda r: -r["alpha_te_pct"])[:12]:
        flag = "FDR" if r["sig"]["fdr"] else "   "
        print(f"  {flag} {r['id']:24} {r['horizon']:10} {r['direction']:10} "
              f"n_te={r['te_n']:>5} dates={r['cluster_n']:>4} names={r['ticker_n']:>4} "
              f"alpha%={r['alpha_te_pct']:>6} p={r['test_p']}")


if __name__ == "__main__":
    main()
