#!/usr/bin/env python3
"""ml_ranker.py — walk-forward cross-sectional GBM ranker over the point-in-time feature store.

WHY: the calibrated mechanisms are 1-2 feature threshold rules. The empirical asset-pricing
literature (Gu/Kelly/Xiu 2020 and everything since) says tree ensembles over the FULL feature
matrix dominate rule sets because interactions carry most of the cross-sectional signal. This
script answers, on OUR data under OUR discipline, whether that holds here — before anything
touches live trading.

Discipline (inherited from mechanism_backtest, same harness semantics):
  - point-in-time feature reads (fval: latest as_of <= D)
  - real split-adjusted prices incl. delisted; $5 price / $5M dollar-volume floors
  - label = forward H-trading-day return MINUS SPY (market-relative), winsorized
  - strict walk-forward: model for test year Y trains only on samples whose LABEL WINDOW
    closed before Y began (30-day embargo on top) — no leakage, ever
  - costs: decile portfolios charged COST_RT round-trip per rebalance (100% turnover assumed
    — conservative upper bound on trading friction)

Output: per-year and aggregate rank IC (Spearman), ICIR, decile long-short and long-only
net alphas, and per-feature marginal ICs for reference. JSON to stdout + saved to
state/ml_ranker_eval.json. OFFLINE ONLY — nothing here writes to the live pipeline.

  python3 ml_ranker.py --top-n 600 --start 2016-01-01 --test-start 2020-01-01
"""
from __future__ import annotations

import argparse, bisect, json, math, os, sqlite3, sys
from pathlib import Path

sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspaces/trading-intel/scripts"))))
import mechanism_backtest as mb          # noqa: E402  (load_ticker, fval, spy_ret, COST_RT)
import feature_store as fs               # noqa: E402

import numpy as np                        # noqa: E402
from scipy import stats as sstats         # noqa: E402  (scipy ships with sklearn stack)
from sklearn.ensemble import HistGradientBoostingRegressor  # noqa: E402

FEAT_DB = os.path.expanduser("~/.openclaw/state/features.sqlite")
OUT_JSON = os.path.expanduser("~/.openclaw/state/ml_ranker_eval.json")

H = 21                     # forward horizon, trading days (matches position_1_4w grading)
REBAL_EVERY = 21           # one rebalance per ~month
EMBARGO_DAYS = 30          # extra calendar gap between train-label close and test start
PRICE_FLOOR = 5.0
DVOL_FLOOR = 5e6
WINSOR = 0.30              # clamp |label| at 30% — same spirit as the mechanism harness


def _universe(conn, top_n):
    return [r[0] for r in conn.execute(
        "SELECT symbol FROM universe WHERE status='active' AND market_cap IS NOT NULL "
        "ORDER BY market_cap DESC LIMIT ?", (top_n,))]


def _spy():
    px = fs._prices("SPY", 4000)
    return {"dk": [b["t"] for b in px], "close": {b["t"]: b["c"] for b in px}}


def _rebalance_dates(spy, start, end):
    dk = [d for d in spy["dk"] if start <= d <= end]
    return dk[::REBAL_EVERY]


def build_panel(names, spy, dates):
    """Rows: (date, ticker, x-vector, label). Streams ticker-by-ticker like the backtester."""
    feats = mb.GEN_FEATURES
    conn = sqlite3.connect(f"file:{FEAT_DB}?mode=ro", uri=True)
    X, y, meta = [], [], []
    skipped = 0
    for i, t in enumerate(names):
        try:
            td = mb.load_ticker(conn, t)
        except Exception:
            skipped += 1
            continue
        dk = td["dates"]
        if len(dk) < H + 5:
            continue
        for D in dates:
            j = bisect.bisect_right(dk, D) - 1
            if j < 0 or j + H >= len(dk):
                continue
            d0 = dk[j]
            # only accept a price within 7 calendar days of the rebalance date (listed & trading)
            if abs((_ord(D) - _ord(d0))) > 7:
                continue
            c0, cH = td["close"][d0], td["close"][dk[j + H]]
            if not c0 or c0 < PRICE_FLOOR or td["dvol"].get(d0, 0) < DVOL_FLOOR:
                continue
            sr = mb.spy_ret(spy, d0, dk[j + H])
            if sr is None:
                continue
            lab = (cH / c0 - 1.0) - sr
            lab = max(-WINSOR, min(WINSOR, lab))
            X.append([_f(mb.fval(td, f, D)) for f in feats])
            y.append(lab)
            meta.append((D, t))
        if (i + 1) % 100 == 0:
            print(f"  panel: {i+1}/{len(names)} tickers, {len(y)} samples", file=sys.stderr, flush=True)
    conn.close()
    return np.array(X, dtype=float), np.array(y, dtype=float), meta, feats


def _f(v):
    return float(v) if v is not None else np.nan


def _ord(iso):
    from datetime import date
    return date.fromisoformat(iso[:10]).toordinal()


def rank_normalize(X, y, meta):
    """Per-rebalance-date cross-sectional rank transform of features AND labels to [-0.5, 0.5].
    Standard for GKX-style cross-sectional models: kills scale/regime drift (a P/E of 30 means
    different things in 2016 vs 2023; its cross-sectional RANK means the same thing), which is
    what broke the raw-value model in the 2023 narrative-regime shift. Macro features are
    constant per date and become 0 — their information re-enters only via interactions learned
    across time, which is the correct role for them in a cross-sectional ranker."""
    from collections import defaultdict
    groups = defaultdict(list)
    for i, (D, _t) in enumerate(meta):
        groups[D].append(i)
    Xr, yr = X.copy(), y.copy()
    for D, idx in groups.items():
        idx = np.array(idx)
        for j in range(X.shape[1]):
            col = X[idx, j]
            ok = ~np.isnan(col)
            if ok.sum() > 1:
                r = sstats.rankdata(col[ok]) / ok.sum() - 0.5
                Xr[idx[ok], j] = r
        yr[idx] = sstats.rankdata(y[idx]) / len(idx) - 0.5
    return Xr, yr


def walk_forward(X, y, meta, test_start):
    """Retrain per test year on all samples whose label window fully closed before that year."""
    years = sorted({m[0][:4] for m in meta if m[0] >= test_start})
    dates_arr = np.array([m[0] for m in meta])
    preds = np.full(len(y), np.nan)
    from datetime import date, timedelta
    for yr in years:
        test_mask = (dates_arr >= f"{yr}-01-01") & (dates_arr <= f"{yr}-12-31") & (dates_arr >= test_start)
        # train cutoff: label window (H td ≈ 31 cal days) + embargo before the year starts
        cutoff = (date(int(yr), 1, 1) - timedelta(days=31 + EMBARGO_DAYS)).isoformat()
        train_mask = dates_arr <= cutoff
        if train_mask.sum() < 2000 or test_mask.sum() == 0:
            continue
        model = HistGradientBoostingRegressor(
            max_iter=300, max_depth=6, learning_rate=0.05,
            min_samples_leaf=50, l2_regularization=1.0, random_state=7)
        model.fit(X[train_mask], y[train_mask])
        preds[test_mask] = model.predict(X[test_mask])
        print(f"  {yr}: trained on {int(train_mask.sum())} → predicted {int(test_mask.sum())}",
              file=sys.stderr, flush=True)
    return preds


def evaluate(preds, y, meta):
    by_date: dict[str, list[int]] = {}
    for i, (D, _t) in enumerate(meta):
        if not math.isnan(preds[i]):
            by_date.setdefault(D, []).append(i)
    ics, ls_net, lo_net = [], [], []
    per_year: dict[str, list[float]] = {}
    for D, idx in sorted(by_date.items()):
        if len(idx) < 40:
            continue
        p = preds[idx]; r = y[idx]
        ic = sstats.spearmanr(p, r).correlation
        if ic is None or math.isnan(ic):
            continue
        ics.append(ic)
        per_year.setdefault(D[:4], []).append(ic)
        k = max(5, len(idx) // 10)
        order = np.argsort(p)
        top, bot = r[order[-k:]], r[order[:k]]
        ls_net.append(float(top.mean() - bot.mean() - 2 * mb.COST_RT))
        lo_net.append(float(top.mean() - mb.COST_RT))
    ics_a = np.array(ics)
    n = len(ics_a)
    out = {
        "rebalances": n,
        "mean_rank_ic": round(float(ics_a.mean()), 4),
        "ic_tstat": round(float(ics_a.mean() / (ics_a.std(ddof=1) / math.sqrt(n))), 2) if n > 2 else None,
        "icir_annualized": round(float(ics_a.mean() / ics_a.std(ddof=1) * math.sqrt(12)), 2) if n > 2 else None,
        "decile_LS_net_per_rebalance_pct": round(100 * float(np.mean(ls_net)), 3),
        "decile_LS_net_annualized_pct": round(100 * (float((1 + np.mean(ls_net)) ** 12) - 1), 1),
        "top_decile_long_only_net_alpha_per_rebalance_pct": round(100 * float(np.mean(lo_net)), 3),
        "pct_positive_ic_months": round(100 * float((ics_a > 0).mean()), 1),
        "per_year_mean_ic": {k: round(float(np.mean(v)), 4) for k, v in sorted(per_year.items())},
    }
    return out


def feature_ics(X, y, meta, feats, test_start):
    """Marginal Spearman IC per feature over the test period — the 'rule-set ceiling' reference."""
    dates_arr = np.array([m[0] for m in meta])
    mask = dates_arr >= test_start
    out = {}
    for j, f in enumerate(feats):
        col = X[mask][:, j]; lab = y[mask]
        ok = ~np.isnan(col)
        if ok.sum() < 500:
            continue
        ic = sstats.spearmanr(col[ok], lab[ok]).correlation
        if ic is not None and not math.isnan(ic):
            out[f] = round(float(ic), 4)
    return dict(sorted(out.items(), key=lambda kv: -abs(kv[1]))[:15])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-n", type=int, default=600)
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--test-start", default="2020-01-01")
    ap.add_argument("--rank-normalize", action="store_true",
                    help="per-date cross-sectional rank transform of X and y (GKX-style)")
    a = ap.parse_args()
    conn = sqlite3.connect(f"file:{FEAT_DB}?mode=ro", uri=True)
    names = _universe(conn, a.top_n)
    conn.close()
    spy = _spy()
    from datetime import date, timedelta
    end = (date.today() - timedelta(days=45)).isoformat()   # labels must be resolvable
    dates = _rebalance_dates(spy, a.start, end)
    print(f"universe={len(names)} rebalances={len(dates)} ({dates[0]}..{dates[-1]})",
          file=sys.stderr, flush=True)
    X, y, meta, feats = build_panel(names, spy, dates)
    print(f"panel: {len(y)} samples x {len(feats)} features", file=sys.stderr, flush=True)
    if a.rank_normalize:
        Xt, yt = rank_normalize(X, y, meta)
        print("rank-normalized per date", file=sys.stderr, flush=True)
    else:
        Xt, yt = X, y
    # train on (possibly rank-transformed) features/labels; ALWAYS evaluate decile
    # returns and ICs against the raw market-relative labels.
    preds = walk_forward(Xt, yt, meta, a.test_start)
    result = {
        "config": {"top_n": a.top_n, "H": H, "test_start": a.test_start,
                   "rank_normalize": bool(a.rank_normalize),
                   "cost_rt": mb.COST_RT, "n_samples": len(y), "n_features": len(feats)},
        "gbm_walk_forward": evaluate(preds, y, meta),
        "single_feature_ics_test_period": feature_ics(X, y, meta, feats, a.test_start),
    }
    with open(OUT_JSON, "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
