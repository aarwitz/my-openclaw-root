#!/usr/bin/env python3
"""Learning exam report card (D57) — the honest read on what grading taught us.

Run after grade_outcomes + calibrate (wired into the learning chain). Emits one
JSON with: resolution counts, hit rate, Brier vs coin-flip, per-mechanism
movement (posterior deltas from trade observations), and calibration buckets.
Prints a compact human summary line the chain can telegram.

  python3 exam_report.py [--db PATH] [--since ISO]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from datetime import datetime, timezone


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))
    ap.add_argument("--since", default=None, help="only count resolutions since this ISO date")
    a = ap.parse_args()
    conn = sqlite3.connect(a.db)
    conn.row_factory = sqlite3.Row
    since = a.since or "1970-01-01"

    res = conn.execute(
        "SELECT COUNT(*) n, AVG(brier_component) brier, "
        "SUM(CASE WHEN realized_outcome='correct' THEN 1 ELSE 0 END) hits "
        "FROM predictions WHERE resolved_at IS NOT NULL AND resolved_at >= ?", (since,)).fetchone()
    total_open = conn.execute("SELECT COUNT(*) FROM predictions WHERE resolved_at IS NULL").fetchone()[0]

    hyp = conn.execute(
        "SELECT resolved_state, COUNT(*) n FROM hypotheses "
        "WHERE resolved_state IS NOT NULL AND resolved_at >= ? GROUP BY resolved_state", (since,)).fetchall()

    # mechanism movement: trade-sourced observations + current posteriors
    movers = conn.execute(
        "SELECT m.id, m.posterior_mean, "
        "SUM(CASE WHEN o.outcome='hit' THEN 1 ELSE 0 END) hits, "
        "SUM(CASE WHEN o.outcome='miss' THEN 1 ELSE 0 END) misses "
        "FROM mechanism_observations o JOIN mechanisms m ON m.id = o.mechanism_id "
        "WHERE o.source_type='prediction' AND o.observed_at >= ? "
        "GROUP BY m.id ORDER BY (hits+misses) DESC LIMIT 12", (since,)).fetchall()

    # calibration buckets (meaningful once p_correct varies)
    buckets = conn.execute(
        "SELECT ROUND(p_correct, 1) p, COUNT(*) n, "
        "AVG(CASE WHEN realized_outcome='correct' THEN 1.0 ELSE 0.0 END) actual "
        "FROM predictions WHERE resolved_at IS NOT NULL AND resolved_at >= ? "
        "GROUP BY ROUND(p_correct, 1) ORDER BY p", (since,)).fetchall()

    n = res["n"] or 0
    report = {
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "since": since,
        "resolved": n,
        "still_open": total_open,
        "hit_rate": round((res["hits"] or 0) / n, 3) if n else None,
        "mean_brier": round(res["brier"], 4) if res["brier"] is not None else None,
        "coin_flip_brier": 0.25,
        "hypothesis_outcomes": {r["resolved_state"]: r["n"] for r in hyp},
        "mechanism_movement": [
            {"id": r["id"], "posterior": round(r["posterior_mean"], 3) if r["posterior_mean"] is not None else None,
             "trade_hits": r["hits"], "trade_misses": r["misses"]}
            for r in movers],
        "calibration_buckets": [
            {"p": r["p"], "n": r["n"], "actual": round(r["actual"], 3)} for r in buckets],
    }
    print(json.dumps(report, indent=2))
    if n:
        verdict = "beating" if (res["brier"] or 1) < 0.25 else "not beating"
        print(f"SUMMARY: {n} graded, hit rate {report['hit_rate']:.0%}, "
              f"Brier {report['mean_brier']} ({verdict} coin-flip), "
              f"{len(report['mechanism_movement'])} mechanisms taught", flush=True)
    else:
        print("SUMMARY: nothing graded in window", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
