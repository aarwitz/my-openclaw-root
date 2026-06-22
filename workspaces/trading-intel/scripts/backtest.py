#!/usr/bin/env python3
"""Walk-forward backtest / bootstrap belief-builder — offline, deterministic, no look-ahead.

Replays history one trading day at a time. At each `as_of` a signal fires using ONLY
bars up to that day (close-of-day decision, enter at that close); the outcome is graded
against strictly-future bars. It tests each signal's edge against the EMPIRICAL base
rate and against SPY (never vs 50%), splits a final holdout it never fits on, and
applies Benjamini-Hochberg across signals so we don't fool ourselves.

Reads split-adjusted Alpaca bars. Writes results to ~/.openclaw/state/backtest.sqlite.
**Never touches the live trading-intel.sqlite** — safe to run before the world-model reset.

  python3 backtest.py --days 2000 --test-months 6 [--universe AAPL,MSFT,...] [--equity-signal trend]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspaces/trading-intel/scripts"))))
from connectors import alpaca  # noqa: E402
import worldmodel as wm  # noqa: E402
import worldmodel_stats as st  # noqa: E402

OUT_DB = Path(os.path.expanduser("~/.openclaw/state/backtest.sqlite"))

# Liquid, still-listed universe (survivorship caveat: delisted names absent → edge is an upper bound).
DEFAULT_UNIVERSE = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "TSLA", "JPM",
                    "UNH", "XOM", "CAT", "WMT", "COST", "QQQ", "IWM", "XLK", "XLE", "XLF", "GLD"]
HORIZONS = {"swing_1_5d": 5, "position_1_4w": 21}
# signal id -> world-model mechanism label it stands in for
SIGNAL_MECH = {
    "trend": "trend-following: SMA50>SMA200 regime -> continuation (long)",
    "momentum_12_1": "cross-sectional momentum: 12-1m positive -> continuation (long)",
    "oversold": "mean-reversion: oversold in an uptrend -> bounce (long)",
    "breakout": "20-day breakout: new high -> continuation (long)",
}


def _sma(a, i, n):
    if i - n + 1 < 0:
        return None
    w = a[i - n + 1: i + 1]
    if any(x is None for x in w):
        return None
    return sum(w) / n


def fires(sig, c, h, i):
    """True if `sig` fires LONG at index i using only data <= i. None if undefined."""
    s50, s200 = _sma(c, i, 50), _sma(c, i, 200)
    if sig == "trend":
        return None if (s50 is None or s200 is None) else s50 > s200
    if sig == "momentum_12_1":
        if i - 252 < 0 or c[i - 252] is None or c[i - 21] is None:
            return None
        return (c[i - 21] / c[i - 252] - 1) > 0
    if sig == "oversold":
        s200p = _sma(c, i - 20, 200)
        if s50 is None or s200 is None or s200p is None or c[i] is None:
            return None
        return c[i] < s50 * 0.93 and s200 > s200p
    if sig == "breakout":
        if i - 20 < 0 or c[i] is None or any(x is None for x in h[i - 20:i]):
            return None
        return c[i] >= max(h[i - 20:i])
    return None


def load_aligned(universe, days, end=None):
    """Fetch split-adjusted bars; align every ticker onto SPY's trading calendar.
    `end` (YYYY-MM-DD) truncates the calendar to <= end — i.e. literally hides newer
    data, so a run with an earlier `end` can only see what was knowable then."""
    syms = ["SPY"] + [s for s in universe if s != "SPY"]
    raw = {}
    for s in syms:
        try:
            raw[s] = alpaca.daily_bars(s, days=days, adjustment="split")
            time.sleep(0.25)
        except Exception as e:
            print(f"  WARN: no bars for {s}: {str(e)[:80]}", file=sys.stderr)
    if "SPY" not in raw:
        raise SystemExit("cannot run without SPY bars")
    master = [b["t"][:10] for b in raw["SPY"]]
    if end:
        master = [d for d in master if d <= end]
    aligned = {}  # sym -> (close[], high[]) over master dates (None where missing)
    for s in raw:
        cmap = {b["t"][:10]: b for b in raw[s]}
        c = [cmap[d]["c"] if d in cmap else None for d in master]
        h = [cmap[d]["h"] if d in cmap else None for d in master]
        aligned[s] = (c, h)
    return master, aligned


def run(universe, days, test_months, dead_band, end=None):
    master, aligned = load_aligned(universe, days, end=end)
    spy_c = aligned["SPY"][0]
    n = len(master)
    maxH = max(HORIZONS.values())
    cutoff = (datetime.now(timezone.utc).date().toordinal() - int(test_months * 30.4))
    def is_test(d):  # d = 'YYYY-MM-DD'
        return date.fromisoformat(d).toordinal() >= cutoff

    samples = []          # fired-signal outcomes
    base_pts = {hn: [] for hn in HORIZONS}   # ALL (ticker,day) forward outcomes -> base rate
    start = 252           # need a year of history before any signal
    for sym in universe:
        if sym not in aligned:
            continue
        c, h = aligned[sym]
        for i in range(start, n - maxH):
            if c[i] is None:
                continue
            # base-rate points (every valid day, every horizon)
            for hn, H in HORIZONS.items():
                if c[i + H] is not None and spy_c[i] and spy_c[i + H]:
                    fwd = c[i + H] / c[i] - 1
                    spy_fwd = spy_c[i + H] / spy_c[i] - 1
                    base_pts[hn].append((fwd > dead_band, fwd > spy_fwd))
            # signals
            for sig in SIGNAL_MECH:
                if fires(sig, c, h, i):
                    for hn, H in HORIZONS.items():
                        if c[i + H] is None or not spy_c[i] or not spy_c[i + H]:
                            continue
                        # LOOK-AHEAD GUARD: decision uses <= i, outcome uses i+H only
                        assert i + H > i
                        fwd = c[i + H] / c[i] - 1
                        spy_fwd = spy_c[i + H] / spy_c[i] - 1
                        samples.append({"sig": sig, "ticker": sym, "as_of": master[i],
                                        "horizon": hn, "fwd": fwd, "spy_fwd": spy_fwd,
                                        "hit_abs": int(fwd > dead_band), "hit_rel": int(fwd > spy_fwd),
                                        "test": is_test(master[i])})

    base_rate = {hn: {"abs": sum(a for a, _ in pts) / len(pts) if pts else 0.5,
                      "rel": sum(r for _, r in pts) / len(pts) if pts else 0.5,
                      "n": len(pts)} for hn, pts in base_pts.items()}

    # per (signal,horizon) stats, split train/test
    results, test_pvals, test_keys = [], [], []
    for sig in SIGNAL_MECH:
        for hn in HORIZONS:
            ss = [s for s in samples if s["sig"] == sig and s["horizon"] == hn]
            tr = [s for s in ss if not s["test"]]
            te = [s for s in ss if s["test"]]
            br = base_rate[hn]
            def stat(group):
                nn = len(group)
                if nn == 0:
                    return {"n": 0}
                hr_abs = sum(s["hit_abs"] for s in group) / nn
                hr_rel = sum(s["hit_rel"] for s in group) / nn
                return {"n": nn, "hit_abs": round(hr_abs, 3), "hit_rel": round(hr_rel, 3),
                        "mean_fwd_pct": round(100 * sum(s["fwd"] for s in group) / nn, 3),
                        "mean_alpha_pct": round(100 * sum(s["fwd"] - s["spy_fwd"] for s in group) / nn, 3)}
            tr_s, te_s = stat(tr), stat(te)
            # significance on the HELD-OUT test set: did it beat SPY more than chance (base rel)?
            p_rel = st.binom_test(sum(s["hit_rel"] for s in te), len(te), br["rel"]) if te else 1.0
            # would-be mechanism weight: Beta from TRAIN market-relative hits (out-of-sample validated on test)
            rh = sum(s["hit_rel"] for s in tr); rm = len(tr) - rh
            a_, b_ = 1 + rh, 1 + rm
            wt_mean = wm.beta_mean(a_, b_); ci = wm.beta_ci(a_, b_)
            results.append({"signal": sig, "mechanism": SIGNAL_MECH[sig], "horizon": hn,
                            "base_rate_rel": round(br["rel"], 3), "base_rate_abs": round(br["abs"], 3),
                            "train": tr_s, "test": te_s, "test_p_vs_base_rel": round(p_rel, 4),
                            "train_weight_beta_mean": round(wt_mean, 3),
                            "train_weight_ci": [round(ci[0], 3), round(ci[1], 3)]})
            if te:
                test_pvals.append(p_rel); test_keys.append((sig, hn))
    keep = st.benjamini_hochberg(test_pvals, 0.05)
    bonf = st.bonferroni(test_pvals, 0.05)
    sig_map = {test_keys[i]: {"fdr": keep[i], "bonferroni": bonf[i]} for i in range(len(test_keys))}
    for r in results:
        r["significant_oos"] = sig_map.get((r["signal"], r["horizon"]), {"fdr": False, "bonferroni": False})

    return master, aligned, base_rate, samples, results


def equity_curve(master, aligned, universe, equity_signal):
    """Long/flat daily strategy: hold equal-weight names with `equity_signal` active at close[t-1]."""
    spy_c = aligned["SPY"][0]
    n = len(master)
    strat, bench = [], []
    for t in range(253, n):
        held = []
        for sym in universe:
            if sym not in aligned:
                continue
            c, h = aligned[sym]
            if c[t - 1] is None or c[t] is None:
                continue
            if fires(equity_signal, c, h, t - 1):   # decided yesterday, earn t-1 -> t
                held.append(c[t] / c[t - 1] - 1)
        strat.append(sum(held) / len(held) if held else 0.0)   # flat (cash) when nothing fires
        bench.append(spy_c[t] / spy_c[t - 1] - 1 if spy_c[t - 1] and spy_c[t] else 0.0)
    m = st.equity_metrics(strat); b = st.equity_metrics(bench)
    m["alpha_annual_vs_spy"] = st.alpha_vs_benchmark(strat, bench)
    return {"strategy": m, "spy_buy_hold": b, "equity_signal": equity_signal, "days": len(strat)}


def write_db(params, base_rate, results, equity):
    OUT_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(OUT_DB)
    conn.executescript("""
      CREATE TABLE IF NOT EXISTS backtest_runs(id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT,
        params_json TEXT, base_rate_json TEXT, equity_json TEXT, n_results INTEGER);
      CREATE TABLE IF NOT EXISTS backtest_results(run_id INTEGER, signal TEXT, mechanism TEXT, horizon TEXT,
        base_rate_rel REAL, train_json TEXT, test_json TEXT, test_p REAL, fdr_sig INTEGER, bonf_sig INTEGER,
        weight_mean REAL, weight_ci_json TEXT);
    """)
    cur = conn.execute("INSERT INTO backtest_runs(created_at,params_json,base_rate_json,equity_json,n_results) VALUES(?,?,?,?,?)",
                       (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                        json.dumps(params), json.dumps(base_rate), json.dumps(equity), len(results)))
    rid = cur.lastrowid
    for r in results:
        conn.execute("INSERT INTO backtest_results VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     (rid, r["signal"], r["mechanism"], r["horizon"], r["base_rate_rel"],
                      json.dumps(r["train"]), json.dumps(r["test"]), r["test_p_vs_base_rel"],
                      int(r["significant_oos"]["fdr"]), int(r["significant_oos"]["bonferroni"]),
                      r["train_weight_beta_mean"], json.dumps(r["train_weight_ci"])))
    conn.commit(); conn.close()
    return rid


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=2000)
    ap.add_argument("--test-months", type=float, default=6.0)
    ap.add_argument("--dead-band", type=float, default=0.0)
    ap.add_argument("--universe", default=",".join(DEFAULT_UNIVERSE))
    ap.add_argument("--equity-signal", default="trend", choices=list(SIGNAL_MECH))
    ap.add_argument("--end-date", default=None, help="hide data after this YYYY-MM-DD (walk-forward / look-ahead test)")
    ap.add_argument("--dump-samples", action="store_true", help="print every graded sample as JSONL and exit (for the look-ahead diff)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    universe = [s.strip().upper() for s in args.universe.split(",") if s.strip()]

    master, aligned, base_rate, samples, results = run(universe, args.days, args.test_months, args.dead_band, end=args.end_date)
    if args.dump_samples:
        for s in sorted(samples, key=lambda x: (x["as_of"], x["sig"], x["horizon"], x["ticker"])):
            print(f"{s['as_of']}|{s['sig']}|{s['horizon']}|{s['ticker']}|{s['fwd']:.6f}|{s['hit_rel']}")
        return 0
    equity = equity_curve(master, aligned, universe, args.equity_signal)
    params = {"days": args.days, "test_months": args.test_months, "dead_band": args.dead_band,
              "universe": universe, "history": f"{master[0]}..{master[-1]}", "n_days": len(master),
              "n_samples": len(samples)}
    rid = write_db(params, base_rate, results, equity)

    if args.json:
        print(json.dumps({"run_id": rid, "params": params, "base_rate": base_rate,
                          "results": results, "equity": equity}, indent=2))
        return 0

    print(f"\n=== BACKTEST run #{rid} ===  history {params['history']}  ({params['n_days']} trading days, {len(universe)} names)")
    print(f"samples (fired signals): {len(samples):,}   |  test holdout = last {args.test_months:g} months")
    print("\nBase rate (random entry):")
    for hn, br in base_rate.items():
        print(f"  {hn:14} P(up)={br['abs']:.3f}  P(beat SPY)={br['rel']:.3f}  (n={br['n']:,})")
    print("\nPer-signal edge — TEST holdout (out-of-sample). 'beat SPY' vs base; * = survives FDR, ** = Bonferroni:")
    print(f"  {'signal':14} {'horizon':13} {'n_te':>5} {'hitRel_te':>9} {'baseRel':>7} {'alpha%_te':>9} {'p':>7} {'sig':>4} | {'wt(train)':>9}")
    for r in sorted(results, key=lambda x: x["test_p_vs_base_rel"]):
        te = r["test"]; mark = "**" if r["significant_oos"]["bonferroni"] else ("*" if r["significant_oos"]["fdr"] else "")
        if not te.get("n"):
            continue
        print(f"  {r['signal']:14} {r['horizon']:13} {te['n']:>5} {te['hit_rel']:>9.3f} {r['base_rate_rel']:>7.3f} "
              f"{te.get('mean_alpha_pct',0):>9.3f} {r['test_p_vs_base_rel']:>7.4f} {mark:>4} | "
              f"{r['train_weight_beta_mean']:.3f} {r['train_weight_ci']}")
    eq = equity["strategy"]; sp = equity["spy_buy_hold"]
    print(f"\nEquity (long/flat on '{equity['equity_signal']}', equal-weight) vs SPY buy-hold:")
    print(f"  strategy : CAGR {eq.get('cagr')}  Sharpe {eq.get('sharpe')}  maxDD {eq.get('max_drawdown')}  alpha/yr {eq.get('alpha_annual_vs_spy')}")
    print(f"  SPY B&H  : CAGR {sp.get('cagr')}  Sharpe {sp.get('sharpe')}  maxDD {sp.get('max_drawdown')}")
    print(f"\nwrote results -> {OUT_DB}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
