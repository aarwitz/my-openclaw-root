#!/usr/bin/env python3
"""Promote backtest survivors into the CALIBRATED MECHANISM SET.

Reads features.sqlite::discovered_mechanisms (latest clean run), selects the FDR-significant,
net-positive-alpha mechanisms (net = after winsorization + liquidity filter + transaction costs),
and writes features.sqlite::calibrated_mechanisms — the learned library that actually survived
out-of-sample. Seeds, machine-generated, and cross-sectional all compete on equal footing.

This is the bootstrap source for the live world model. Installing it into the live
trading-intel.sqlite (reset + load + wire predict.py to net-alpha weights) is a separate GATED
step — this script only produces the calibrated artifact (non-destructive, in the analytics DB).
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timezone

DB = os.path.expanduser("~/.openclaw/state/features.sqlite")
HD = {"swing_5d": 5, "month_21d": 21, "quarter_63d": 63}


def source_of(mid: str) -> str:
    return "cross" if mid.startswith("xs_") else ("generated" if mid.startswith("gen_") else "seed")


def posterior(net_alpha_pct, horizon) -> float:
    """Provisional world-model reliability from net monthly-equivalent alpha (documented heuristic;
    refined at live-integration time). 0.5 = no edge; bounded to keep any single mechanism modest."""
    h = HD.get(horizon, 21)
    edge_monthly = (net_alpha_pct or 0.0) / (h / 21.0)
    return round(min(0.72, 0.5 + max(0.0, edge_monthly) * 0.10), 4)


def main():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM discovered_mechanisms WHERE fdr_sig=1 AND alpha_te_pct>0 "
        "ORDER BY alpha_te_pct DESC")]
    c.execute("DROP TABLE IF EXISTS calibrated_mechanisms")
    c.execute("""CREATE TABLE calibrated_mechanisms(
        id TEXT, horizon TEXT, direction TEXT, kind TEXT, source TEXT, conds_json TEXT, rationale TEXT,
        net_alpha_pct REAL, test_p REAL, bonf_sig INT, hit_te REAL, te_n INT, posterior_mean REAL,
        skew_edge INT, created_at TEXT)""")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for r in rows:
        skew = 1 if (r["hit_te"] is not None and r["hit_te"] < 0.5) else 0
        c.execute("INSERT INTO calibrated_mechanisms VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (r["id"], r["horizon"], r["direction"], r["kind"], source_of(r["id"]),
                   r["conds_json"], r["rationale"], r["alpha_te_pct"], r["test_p"], r["bonf_sig"],
                   r["hit_te"], r["te_n"], posterior(r["alpha_te_pct"], r["horizon"]), skew, now))
    c.commit()

    by = {}
    for r in rows:
        by[source_of(r["id"])] = by.get(source_of(r["id"]), 0) + 1
    print(f"CALIBRATED MECHANISM SET: {len(rows)} survivors (FDR-significant, net-positive alpha after costs)")
    print(f"  by source: {by}")
    print(f"  bonferroni-strong: {sum(1 for r in rows if r['bonf_sig'])}")
    print("  top by net alpha:")
    for r in rows[:18]:
        print(f"    {r['id']:28} {r['horizon']:11} {r['direction']:6} "
              f"net_alpha%={r['alpha_te_pct']:>6} p={r['test_p']:<8} hit={r['hit_te']} n={r['te_n']:>6} "
              f"-> posterior={posterior(r['alpha_te_pct'], r['horizon'])}")
    c.close()


if __name__ == "__main__":
    main()
