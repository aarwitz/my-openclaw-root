#!/usr/bin/env python3
"""Regime-conditional mechanism performance (#3) — does each calibrated mechanism's edge actually hold
in the CURRENT macro regime? Computes per-mechanism OOS market-relative alpha bucketed by regime
(VIX high/low; rates rising/falling/flat) over a 150-name panel, and writes `mechanism_regime`.
`signal_scan` reads it to up/down-weight each mechanism by the live regime (from regime_brief), so the
quant layer — not just the LLM — weights mechanisms by regime. Read-only on existing tables; CREATEs
only `mechanism_regime`. stdlib + math only.

  python3 mechanism_regime.py            # build the table + print regime-dependence report
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspaces/trading-intel/scripts"))))
import feature_store as fs           # noqa: E402
import mechanism_backtest as mb      # noqa: E402

FEAT = os.path.expanduser("~/.openclaw/state/features.sqlite")
HORIZ = {"swing_5d": 5, "month_21d": 21, "quarter_63d": 63}
MIN_N = 25


def regimes_at(td, d):
    """Regime buckets in effect at date d (plus 'ALL')."""
    out = ["ALL"]
    vix = mb.fval(td, "vix_level", d)
    if vix is not None:
        out.append("vix_hi" if vix > 22 else "vix_lo")
    rc = mb.fval(td, "rate_10y_chg_63d", d)
    if rc is not None:
        out.append("rate_up" if rc > 0.2 else ("rate_dn" if rc < -0.2 else "rate_flat"))
    return out


def main():
    conn = sqlite3.connect(FEAT)
    conn.row_factory = sqlite3.Row
    mechs = [dict(r) for r in conn.execute(
        "SELECT id, horizon, direction, conds_json FROM calibrated_mechanisms")]
    for m in mechs:
        m["conds"] = json.loads(m["conds_json"])
        m["H"] = HORIZ.get(m["horizon"], 21)
    spy_px = {b["t"]: b["c"] for b in fs._prices("SPY", 4000) if b.get("c")}
    tickers = [r[0] for r in conn.execute(
        "SELECT symbol FROM universe WHERE status='active' AND market_cap IS NOT NULL "
        "ORDER BY market_cap DESC LIMIT 150")]

    acc = {}   # (id, horizon, regime) -> [sum_alpha, n]
    for ti, t in enumerate(tickers):
        try:
            td = mb.load_ticker(conn, t)
        except Exception:
            continue
        dates, close, n = td["dates"], td["close"], len(td["dates"])
        for i in range(0, n, 5):                          # weekly grid (non-overlapping enough)
            d = dates[i]
            if d < "2021-01-01":
                continue
            regs = None
            for m in mechs:
                H = m["H"]
                if i + H >= n:
                    continue
                d_exit = dates[i + H]
                if d not in spy_px or d_exit not in spy_px:
                    continue
                if not mb.holds(td, m["conds"], d):
                    continue
                r = (close[d_exit] / close[d] - 1) - (spy_px[d_exit] / spy_px[d] - 1)
                if m["direction"] == "short":
                    r = -r
                if regs is None:
                    regs = regimes_at(td, d)
                for rg in regs:
                    a = acc.setdefault((m["id"], m["horizon"], rg), [0.0, 0])
                    a[0] += r
                    a[1] += 1
        if (ti + 1) % 30 == 0:
            print(f"  {ti+1}/{len(tickers)} tickers", flush=True)

    conn.execute("DROP TABLE IF EXISTS mechanism_regime")
    conn.execute("CREATE TABLE mechanism_regime(mechanism_id TEXT, horizon TEXT, regime TEXT, "
                 "alpha_pct REAL, n INT, created_at TEXT)")
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [(mid, hz, rg, round(100 * s / k, 4), k, now)
            for (mid, hz, rg), (s, k) in acc.items() if k >= MIN_N]
    conn.executemany("INSERT INTO mechanism_regime VALUES(?,?,?,?,?,?)", rows)
    conn.commit()
    print(f"\nwrote {len(rows)} regime rows -> mechanism_regime")

    # report the most regime-DEPENDENT mechanisms (biggest gap between best/worst regime vs ALL)
    by = {}
    for mid, hz, rg, a in conn.execute("SELECT mechanism_id,horizon,regime,alpha_pct FROM mechanism_regime"):
        by.setdefault((mid, hz), {})[rg] = a
    flips = []
    for (mid, hz), rgs in by.items():
        base = rgs.get("ALL")
        if base is None:
            continue
        for dim in (("vix_hi", "vix_lo"), ("rate_up", "rate_dn")):
            a, b = rgs.get(dim[0]), rgs.get(dim[1])
            if a is not None and b is not None:
                flips.append((abs(a - b), f"{mid} [{hz}]: {dim[0]}={a:+.2f}% vs {dim[1]}={b:+.2f}% (ALL={base:+.2f}%)"))
    flips.sort(reverse=True)
    print("\nMOST REGIME-DEPENDENT mechanisms (alpha swings by regime):")
    for _, line in flips[:14]:
        print("  ", line)
    conn.close()


if __name__ == "__main__":
    main()
