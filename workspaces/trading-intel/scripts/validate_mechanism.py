#!/usr/bin/env python3
"""Validate a PROPOSED mechanism with the same rigor as discovery — the tool the LLM proposer (#4)
must pass before any hypothesis it invents can earn weight.

Backtests an arbitrary candidate (feature-trigger conds + direction) out-of-sample over a liquid
sample of the survivorship-safe universe, net of transaction costs, with the same non-overlapping
sampling + base-rate null + mean-alpha t-test used by mechanism_backtest. Reports per-horizon net
alpha, p-value, n, and a verdict. (FDR is applied across a batch; a single-candidate p<0.01 here is
the screening bar — promote only what also clears FDR in a full mechanism_backtest run.)

  python3 validate_mechanism.py --conds '[["rsi14","<",30],["dist_sma200",">",0.0]]' \
      --direction long --rationale "oversold but in a long-term uptrend" [--sample 400]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspaces/trading-intel/scripts"))))
import mechanism_backtest as mb   # noqa: E402
import feature_store as fs        # noqa: E402

FEAT = os.path.expanduser("~/.openclaw/state/features.sqlite")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--conds", required=True, help='JSON list of [feature, op, threshold]')
    ap.add_argument("--direction", default="long", choices=["long", "short"])
    ap.add_argument("--rationale", default="")
    ap.add_argument("--kind", default="state", choices=["state", "event"])
    ap.add_argument("--sample", type=int, default=400)
    ap.add_argument("--test-start", default="2020-06-18")
    a = ap.parse_args()
    conds = [tuple(c) for c in json.loads(a.conds)]
    evfeat = conds[0][0] if a.kind == "event" else None

    conn = sqlite3.connect(FEAT)
    universe = [r[0] for r in conn.execute(
        "SELECT symbol FROM universe WHERE status='active' AND market_cap IS NOT NULL "
        "ORDER BY market_cap DESC LIMIT ?", (a.sample,))]
    spx = fs._prices("SPY", 4000)
    spy = {"close": {b["t"]: b["c"] for b in spx}, "dk": [b["t"] for b in spx]}

    base = {h: [0, 0] for h in mb.HORIZONS}            # [hits, n]
    cell = {h: [0, 0, 0.0, 0.0] for h in mb.HORIZONS}  # [n, hits, sum_exc, sumsq_exc]  (test, net of cost)
    for t in universe:
        try:
            td = mb.load_ticker(conn, t)
        except Exception:
            continue
        for hn, H in mb.HORIZONS.items():
            for d, fwd, sp in mb.samples_for(td, spy, [], "state", H):
                base[hn][1] += 1; base[hn][0] += 1 if fwd > sp else 0
            cost = mb.COST_RT + (mb.SHORT_BORROW_PER_DAY * H if a.direction == "short" else 0.0)
            for d, fwd, sp in mb.samples_for(td, spy, conds, a.kind, H, evfeat):
                if d < a.test_start:
                    continue
                exc = ((fwd - sp) if a.direction == "long" else (sp - fwd)) - cost
                c = cell[hn]
                c[0] += 1; c[1] += int((fwd > sp) if a.direction == "long" else (fwd < sp))
                c[2] += exc; c[3] += exc * exc
    conn.close()

    print(f"\nVALIDATE candidate: conds={conds} dir={a.direction}  ({a.rationale})")
    print(f"  universe sample: {len(universe)} liquid names | test holdout >= {a.test_start}\n")
    print(f"  {'horizon':12} {'n_te':>6} {'net_alpha%':>10} {'p':>9} {'hit':>5} {'base':>5}  verdict")
    any_pass = False
    for hn in mb.HORIZONS:
        n, hits, s, ss = cell[hn]
        br = (base[hn][0] / base[hn][1]) if base[hn][1] else 0.5
        m, p = mb._ttest_moments(n, s, ss)
        ok = n >= 30 and m > 0 and p < 0.01
        any_pass = any_pass or ok
        verdict = "PASS (screen)" if ok else ("weak" if (n >= 30 and m > 0) else "insufficient/neg")
        print(f"  {hn:12} {n:>6} {100*m:>10.3f} {p:>9.5f} {hits/n if n else 0:>5.2f} {br:>5.2f}  {verdict}")
    print(f"\n  => {'CANDIDATE CLEARS the screening bar at >=1 horizon — run a full mechanism_backtest for FDR.' if any_pass else 'rejected: no horizon clears net-positive p<0.01.'}")


if __name__ == "__main__":
    main()
