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
MIN_PROBABILITY_N = 30
PROBABILITY_PRIOR_N = 20.0


def source_of(mid: str) -> str:
    return (
        "cross"
        if mid.startswith("xs_")
        else ("generated" if mid.startswith(("gen_", "multi_")) else "seed")
    )


def posterior(hit_te, effective_n) -> float | None:
    """Smoothed out-of-sample directional reliability.

    Alpha and hit probability are different quantities. Only mechanisms with a
    measured OOS hit rate can seed ``p_correct``; expectancy-only cross-sectional
    spreads remain research artifacts until they have per-name outcome labels.
    The small symmetric prior prevents huge, dependent backtest samples from
    creating false precision at bootstrap time.
    """
    if hit_te is None or effective_n is None or int(effective_n) < MIN_PROBABILITY_N:
        return None
    n = float(effective_n)
    hits = max(0.0, min(1.0, float(hit_te))) * n
    mean = (hits + 0.5 * PROBABILITY_PRIOR_N) / (n + PROBABILITY_PRIOR_N)
    return round(max(0.35, min(0.65, mean)), 4)


def main():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    discovered_columns = {
        row[1] for row in c.execute("PRAGMA table_info(discovered_mechanisms)")
    }
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM discovered_mechanisms WHERE fdr_sig=1 AND alpha_te_pct>0 "
        "ORDER BY alpha_te_pct DESC")]
    for row in rows:
        row["cluster_n"] = (
            row.get("cluster_n") if "cluster_n" in discovered_columns else row["te_n"]
        )
    c.execute("DROP TABLE IF EXISTS calibrated_mechanisms")
    c.execute("""CREATE TABLE calibrated_mechanisms(
        id TEXT, horizon TEXT, direction TEXT, kind TEXT, source TEXT, conds_json TEXT, rationale TEXT,
        net_alpha_pct REAL, test_p REAL, bonf_sig INT, hit_te REAL, te_n INT, cluster_n INT,
        posterior_mean REAL,
        skew_edge INT, created_at TEXT)""")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for r in rows:
        skew = 1 if (r["hit_te"] is not None and r["hit_te"] < 0.5) else 0
        c.execute("INSERT INTO calibrated_mechanisms VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (r["id"], r["horizon"], r["direction"], r["kind"], source_of(r["id"]),
                   r["conds_json"], r["rationale"], r["alpha_te_pct"], r["test_p"], r["bonf_sig"],
                   r["hit_te"], r["te_n"], r["cluster_n"],
                   posterior(r["hit_te"], r["cluster_n"]), skew, now))
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
              f"dates={r['cluster_n']:>5} -> posterior={posterior(r['hit_te'], r['cluster_n'])}")
    c.close()


if __name__ == "__main__":
    main()
