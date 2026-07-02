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
  * graded MARKET-RELATIVE (beat SPY), tested vs the EMPIRICAL base rate (not 0.5).
  * candidate thresholds are percentiles computed from the TRAIN period only (no test leakage).
  * train/test split by date; significance reported on the TEST holdout; Benjamini-Hochberg FDR +
    Bonferroni across every (mechanism x horizon).
  * survivorship-aware: include delisted/failed names; their prices come from FMP.

Reads state/features.sqlite (built by feature_store.py) + FMP prices. Writes survivors to
state/features.sqlite::discovered_mechanisms.  python3 mechanism_backtest.py --universe ... --test-start 2020-06-18
"""

from __future__ import annotations

import argparse
import bisect
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
HORIZONS = {"swing_5d": 5, "month_21d": 21, "quarter_63d": 63}
# Data-quality + tradability controls (the fix for penny-stock outlier contamination):
PRICE_FLOOR = 5.0                          # min entry close — exclude penny stocks
DV_FLOOR = 5_000_000                        # min entry dollar-volume — institutional tradability
WINSOR = {5: 0.25, 21: 0.50, 63: 1.00}     # cap |single-name return| per horizon (outlier control)
COST_RT = 0.002                            # round-trip transaction cost (20 bps) per trade
SHORT_BORROW_PER_DAY = 0.0001              # ~2.5%/yr borrow cost applied to short trades

# Economic seed mechanisms (the hypotheses). conds: list of (feature, op, threshold). op in >,<.
SEEDS = [
    ("earnings_beat",    "positive EPS surprise -> post-earnings drift up", [("eps_surprise_pct", ">", 0.05)], "long",  "event"),
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
                "filing_delta"]


_MACRO: dict = {}


def _macro_series():
    """Global point-in-time macro features from FRED (daily, non-revised market data → no look-ahead).
    The transmission signal for jobs/CPI/Fed surprises is the RATE MOVE (the surprise is already priced
    into rates). Same value for all tickers on a date → merged into each ticker's feature dict."""
    if _MACRO:
        return _MACRO
    from connectors import fred

    def chg(series_id, win):
        try:
            s = fred.fetch_series(series_id)
        except Exception:
            return []
        return sorted((s[i][0], s[i][1] - s[i - win][1]) for i in range(win, len(s)))

    def level(series_id):
        try:
            return sorted(fred.fetch_series(series_id))
        except Exception:
            return []
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
    # price series from FMP (deep, split-adjusted, works for delisted)
    px = fs._prices(ticker, 4000)
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


def samples_for(td, spy, conds, kind, H, event_feat=None):
    """Non-overlapping graded samples for a mechanism on one ticker at horizon H."""
    dates = td["dates"]
    n = len(dates)
    # candidate fire dates
    if kind == "event" and event_feat:
        fire = [a for a in td["fkeys"].get(event_feat, []) if a in td["close"]]
    else:
        fire = dates[::5]  # weekly cadence reduces overlap before the spacing filter
    out, last_idx = [], -10 ** 9
    didx = {d: k for k, d in enumerate(dates)}
    for d in fire:
        if not holds(td, conds, d):
            continue
        # entry = next trading day strictly after d
        k = bisect.bisect_right(dates, d)
        if k >= n:
            continue
        ent = k
        if ent - last_idx < H:          # enforce non-overlap (spacing >= H)
            continue
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
        last_idx = ent
        out.append((d_ent, fwd, sp))
    return out


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
# economically motivated (hypothesis-alignment), not a blind C(9,2) sweep. side: hi=>p80, lo=>p20.
MULTI_PAIRS = [
    ("pe_ttm", "lo", "mom_12_1", "hi", "value + momentum (cheap and rising)"),
    ("pe_ttm", "lo", "net_margin_ttm", "hi", "value + quality (cheap and profitable)"),
    ("drawdown_252", "lo", "dist_sma200", "hi", "oversold within a long uptrend"),
    ("revenue_growth_yoy", "hi", "mom_12_1", "hi", "growth + momentum"),
    ("vol_20d_annual", "lo", "pe_ttm", "lo", "low-vol + value (quality value)"),
    ("net_margin_ttm", "hi", "revenue_growth_yoy", "hi", "quality + growth (compounders)"),
    ("rsi14", "lo", "pe_ttm", "lo", "oversold + cheap"),
    ("mom_12_1", "hi", "vol_20d_annual", "lo", "momentum + low volatility"),
    ("sector_rel_63d", "hi", "mom_12_1", "hi", "sector tailwind + name momentum (supercycle)"),
    ("insider_net_180d", "hi", "pe_ttm", "lo", "insider buying + cheap"),
    ("rating_net_90d", "hi", "revenue_growth_yoy", "hi", "analyst upgrades + growth"),
    ("sector_rel_63d", "hi", "drawdown_252", "lo", "pullback in a hot sector"),
    ("news_sent_7d", "lo", "pe_ttm", "lo", "bearish news + cheap = contrarian overreaction (GOOG/MSFT)"),
    ("news_vol_z", "hi", "news_sent_7d", "hi", "positive news spike = catalyst drift"),
    ("news_sent_30d", "hi", "mom_12_1", "hi", "improving sentiment + momentum"),
    ("rate_10y_chg_63d", "lo", "pe_ttm", "hi", "rates falling + high-duration growth -> long"),
    ("rate_10y_chg_63d", "hi", "pe_ttm", "hi", "rates rising + high-duration tech -> short (duration repricing)"),
    ("credit_spread_chg_63d", "hi", "vol_20d_annual", "hi", "credit stress + high-beta -> short"),
    ("vix_level", "hi", "drawdown_252", "lo", "high VIX + deep drawdown -> capitulation bounce"),
    ("days_to_cover", "hi", "drawdown_252", "lo", "crowded short + deep drawdown -> squeeze bounce"),
    ("short_int_chg_2m", "hi", "mom_12_1", "lo", "rising shorts + weak momentum -> underperform"),
]


def gen_multi(pools):
    """2-feature conjunction mechanisms from TRAIN-derived quintiles (no test leakage)."""
    q = {}
    for f, vals in pools.items():
        if len(vals) >= 200:
            vals.sort()
            q[f] = (vals[int(0.20 * len(vals))], vals[int(0.80 * len(vals))])
    out = []
    for fa, sa, fb, sb, label in MULTI_PAIRS:
        if fa not in q or fb not in q:
            continue
        ca = (fa, ">", round(q[fa][1], 4)) if sa == "hi" else (fa, "<", round(q[fa][0], 4))
        cb = (fb, ">", round(q[fb][1], 4)) if sb == "hi" else (fb, "<", round(q[fb][0], 4))
        out.append((f"multi_{fa}_{sa}_{fb}_{sb}", f"generated 2-feature: {label}", [ca, cb], "long", "state"))
    return out


def run(universe, spy, test_start):
    """Two streaming passes over tickers (one ticker in memory at a time → scales to thousands).
    Pass 1: base rate + gen-candidate pools + cross-sectional buckets. Pass 2: trigger moments."""
    REBAL = spy["dk"][252::21]                       # ~monthly grid for cross-sectional factors
    conn = sqlite3.connect(FEAT_DB, timeout=60.0)

    # ---- PASS 1 ----
    base = {h: [0, 0] for h in HORIZONS}             # [hits, n]
    pools = {f: [] for f in GEN_FEATURES}
    cross = {f: {} for f in GEN_FEATURES}            # f -> {rebal_date: [(val, fwd, spy_fwd)]}
    nseen = 0
    for t in universe:
        try:
            td = load_ticker(conn, t)
        except Exception:
            continue
        nseen += 1
        for hn, H in HORIZONS.items():
            for d, fwd, sp in samples_for(td, spy, [], "state", H):
                base[hn][1] += 1; base[hn][0] += 1 if fwd > sp else 0
        for f in GEN_FEATURES:
            pools[f] += [v for a, v in td["feats"].get(f, []) if a < test_start and v is not None][::3]
        dates = td["dates"]; n = len(dates)
        for rd in REBAL:
            k = bisect.bisect_right(dates, rd)
            if k >= n or k + 21 >= n:
                continue
            c_ent, c_ex = td["close"][dates[k]], td["close"][dates[k + 21]]
            sp = spy_ret(spy, dates[k], dates[k + 21])
            if not c_ent or sp is None:
                continue
            if c_ent < PRICE_FLOOR or td["dvol"].get(dates[k], 0) < DV_FLOOR:
                continue
            fwd = c_ex / c_ent - 1
            cap = WINSOR[21]
            fwd = max(-cap, min(cap, fwd))
            for f in GEN_FEATURES:
                v = fval(td, f, rd)
                if v is not None:
                    cross[f].setdefault(rd, []).append((v, fwd, sp))
    base_long = {h: (base[h][0] / base[h][1] if base[h][1] else 0.5) for h in HORIZONS}

    mechs = list(SEEDS) + gen_candidates(pools) + gen_multi(pools)
    cellmeta = {(m[0], hn): m for m in mechs for hn in HORIZONS}
    cells = {k: [0, 0, 0, 0, 0.0, 0.0] for k in cellmeta}   # [n_tr,h_tr,n_te,h_te,s_te,ss_te]

    # ---- PASS 2: trigger moments ----
    for t in universe:
        try:
            td = load_ticker(conn, t)
        except Exception:
            continue
        for (mid, rationale, conds, direction, kind) in mechs:
            evfeat = conds[0][0] if kind == "event" else None
            for hn, H in HORIZONS.items():
                c = cells[(mid, hn)]
                cost = COST_RT + (SHORT_BORROW_PER_DAY * H if direction == "short" else 0.0)
                for d, fwd, sp in samples_for(td, spy, conds, kind, H, evfeat):
                    win = (fwd > sp) if direction == "long" else (fwd < sp)
                    exc = ((fwd - sp) if direction == "long" else (sp - fwd)) - cost   # NET of costs
                    if d < test_start:
                        c[0] += 1; c[1] += int(win)
                    else:
                        c[2] += 1; c[3] += int(win); c[4] += exc; c[5] += exc * exc
    conn.close()

    results, tp, keys = [], [], []
    for (mid, hn), (_, rationale, conds, direction, kind) in cellmeta.items():
        n_tr, h_tr, n_te, h_te, s, ss = cells[(mid, hn)]
        base_dir = base_long[hn] if direction == "long" else 1 - base_long[hn]
        m_exc, p_mean = _ttest_moments(n_te, s, ss)
        p_hit = st.binom_test(h_te, n_te, base_dir) if n_te else 1.0
        a_, b_ = 1 + h_tr, 1 + (n_tr - h_tr)
        results.append({"id": mid, "rationale": rationale, "horizon": hn, "direction": direction,
                        "conds": conds, "kind": kind, "base": round(base_dir, 3), "tr_n": n_tr, "te_n": n_te,
                        "hit_te": round(h_te / n_te, 3) if n_te else None,
                        "alpha_te_pct": round(100 * m_exc, 3) if n_te else None,
                        "test_p": round(p_mean, 5), "hit_p": round(p_hit, 5),
                        "weight_mean": round(wm.beta_mean(a_, b_), 3)})
        if n_te >= 30:
            tp.append(p_mean); keys.append((mid, hn, "trig"))

    # ---- cross-sectional factor results ----
    for f, buckets in cross.items():
        for variant, dirn in (("hi", "long"), ("lo", "long"), ("ls", "long_short")):
            series = []
            for rd in sorted(buckets):
                if rd < test_start:
                    continue
                rows = sorted(buckets[rd], key=lambda x: x[0])
                if len(rows) < 20:
                    continue
                k = max(2, int(0.2 * len(rows)))
                if variant == "hi":
                    series.append(sum(fw - sp for _, fw, sp in rows[-k:]) / k - COST_RT)
                elif variant == "lo":
                    series.append(sum(fw - sp for _, fw, sp in rows[:k]) / k - COST_RT)
                else:
                    series.append(sum(fw for _, fw, _ in rows[-k:]) / k - sum(fw for _, fw, _ in rows[:k]) / k - 2 * COST_RT)
            if len(series) < 8:
                continue
            m = sum(series) / len(series)
            _mm, p = _ttest_moments(len(series), sum(series), sum(x * x for x in series))
            mid = f"xs_{f}_{variant}"
            results.append({"id": mid, "rationale": f"cross-sectional {f} {variant} quintile, monthly 21d",
                            "horizon": "month_21d", "direction": dirn, "conds": [[f, variant, 0.2]], "kind": "cross",
                            "base": 0.5, "tr_n": 0, "te_n": len(series), "hit_te": None,
                            "alpha_te_pct": round(100 * m, 3), "test_p": round(p, 5), "hit_p": None,
                            "weight_mean": None})
            tp.append(p); keys.append((mid, "month_21d", "cross"))

    keep = st.benjamini_hochberg(tp, 0.05); bonf = st.bonferroni(tp, 0.05)
    sig = {(keys[i][0], keys[i][1]): {"fdr": keep[i], "bonf": bonf[i]} for i in range(len(keys))}
    for r in results:
        r["sig"] = sig.get((r["id"], r["horizon"]), {"fdr": False, "bonf": False})
    return results, base_long, mechs, nseen


def persist(results):
    conn = sqlite3.connect(FEAT_DB, timeout=60.0)
    conn.execute("DROP TABLE IF EXISTS discovered_mechanisms")
    conn.execute("""CREATE TABLE discovered_mechanisms(
        id TEXT, horizon TEXT, direction TEXT, rationale TEXT, conds_json TEXT, kind TEXT,
        base REAL, tr_n INT, te_n INT, hit_te REAL, alpha_te_pct REAL, test_p REAL,
        fdr_sig INT, bonf_sig INT, weight_mean REAL, created_at TEXT)""")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for r in results:
        conn.execute("INSERT INTO discovered_mechanisms VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (r["id"], r["horizon"], r["direction"], r["rationale"], json.dumps(r["conds"]),
                      r["kind"], r["base"], r["tr_n"], r["te_n"], r["hit_te"], r["alpha_te_pct"],
                      r["test_p"], int(r["sig"]["fdr"]), int(r["sig"]["bonf"]), r["weight_mean"], now))
    conn.commit(); conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--universe", required=True, help="comma list, or ALL = every built ticker")
    ap.add_argument("--test-start", default="2020-06-18", help="OOS holdout starts here (train < this)")
    a = ap.parse_args()
    if a.universe.strip().upper() == "ALL":
        c = sqlite3.connect(FEAT_DB)
        universe = [r[0] for r in c.execute("SELECT DISTINCT ticker FROM features WHERE source='price'")]
        c.close()
    else:
        universe = [s.strip().upper() for s in a.universe.split(",") if s.strip()]
    spx = fs._prices("SPY", 4000)
    spy = {"close": {b["t"]: b["c"] for b in spx}, "dk": [b["t"] for b in spx]}

    results, base, mechs, nseen = run(universe, spy, a.test_start)
    persist(results)

    ntrig = sum(1 for r in results if r["kind"] != "cross")
    ncross = sum(1 for r in results if r["kind"] == "cross")
    print(f"\n=== REAL-MECHANISM BACKTEST ===  {nseen} names (incl. delisted)  test holdout >= {a.test_start}")
    print(f"tested: {ntrig} trigger cells ({len(SEEDS)} seeds + {len(mechs)-len(SEEDS)} machine-generated, x{len(HORIZONS)} horizons) + {ncross} cross-sectional factors")
    print("base rate P(beat SPY): " + "  ".join(f"{h}={base[h]:.3f}" for h in HORIZONS))
    surv = sorted([r for r in results if r["sig"]["fdr"]], key=lambda r: r["test_p"])
    print("\nSURVIVORS (FDR-significant positive OOS mean-alpha):  ** = also Bonferroni")
    print(f"  {'mechanism':24} {'horizon':10} {'dir':10} {'n_te':>5} {'alpha%':>7} {'p':>8} {'hit':>5} {'wt':>5}")
    if not surv:
        print("   (none survived — the honest result)")
    for r in surv:
        mark = "**" if r["sig"]["bonf"] else "*"
        hit = f"{r['hit_te']:.2f}" if r["hit_te"] is not None else "  - "
        wt = f"{r['weight_mean']:.2f}" if r["weight_mean"] is not None else "  - "
        print(f"  {r['id']:24} {r['horizon']:10} {r['direction']:10} {r['te_n']:>5} {r['alpha_te_pct']:>7.3f} {r['test_p']:>8.5f} {hit:>5} {wt:>5} {mark}")
    print(f"\n(persisted all {len(results)} rows -> features.sqlite::discovered_mechanisms)")
    print("\nTop 12 by OOS mean-alpha (context, pre-correction):")
    for r in sorted([x for x in results if x["alpha_te_pct"] is not None], key=lambda r: -r["alpha_te_pct"])[:12]:
        flag = "FDR" if r["sig"]["fdr"] else "   "
        print(f"  {flag} {r['id']:24} {r['horizon']:10} {r['direction']:10} n_te={r['te_n']:>5} alpha%={r['alpha_te_pct']:>6} p={r['test_p']}")


if __name__ == "__main__":
    main()
