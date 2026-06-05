#!/usr/bin/env python3
"""Bessent · audit_benchmark_drift.py

Roll up `attribution` rows into a current-period `benchmarks` row per horizon.
Surfaces alpha vs SPY by horizon. Fires `yellow` if alpha is negative across
two or more horizons; `red` if alpha < -300 bps in any single horizon.

Usage:
  python3 audit_benchmark_drift.py
  python3 audit_benchmark_drift.py --no-write
"""

from __future__ import annotations

import argparse
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _db import audit, connect, emit, now_iso  # noqa: E402

HORIZONS = ["intraday", "swing_1_5d", "position_1_4w", "trend_1_3m", "long_6m_plus"]
RED_THRESHOLD_BPS = -300


def rollup(conn) -> dict:
    out: dict[str, dict] = {}
    for h in HORIZONS:
        rows = conn.execute(
            "SELECT portfolio_return_pct, spy_return_pct, realized_edge_vs_spy_bps, "
            "opened_at, closed_at FROM attribution WHERE horizon=?", (h,)
        ).fetchall()
        if not rows:
            out[h] = {"n": 0, "alpha_pct": None, "portfolio_return_pct": None,
                      "spy_return_pct": None, "period_start": None, "period_end": None}
            continue
        port = [r["portfolio_return_pct"] for r in rows if r["portfolio_return_pct"] is not None]
        spy = [r["spy_return_pct"] for r in rows if r["spy_return_pct"] is not None]
        port_mean = round(statistics.fmean(port), 4) if port else None
        spy_mean = round(statistics.fmean(spy), 4) if spy else None
        alpha = (round(port_mean - spy_mean, 4) if (port_mean is not None and spy_mean is not None) else None)
        starts = [r["opened_at"] for r in rows if r["opened_at"]]
        ends = [r["closed_at"] for r in rows if r["closed_at"]]
        out[h] = {"n": len(rows), "alpha_pct": alpha,
                  "portfolio_return_pct": port_mean, "spy_return_pct": spy_mean,
                  "period_start": min(starts) if starts else None,
                  "period_end": max(ends) if ends else None}
    return out


def assess(rollups: dict) -> dict:
    negatives = [h for h, r in rollups.items() if r["alpha_pct"] is not None and r["alpha_pct"] < 0]
    deep_red = [h for h, r in rollups.items() if r["alpha_pct"] is not None and r["alpha_pct"] * 100.0 <= RED_THRESHOLD_BPS]
    color = "green"
    issues: list[dict] = []
    if deep_red:
        color = "red"
        for h in deep_red:
            issues.append({"severity": "red", "area": "alpha", "horizon": h,
                           "detail": f"alpha_pct={rollups[h]['alpha_pct']} <= {RED_THRESHOLD_BPS}bps"})
    elif len(negatives) >= 2:
        color = "yellow"
        issues.append({"severity": "yellow", "area": "alpha",
                       "detail": f"negative_alpha_in={negatives}"})
    return {"color": color, "issues": issues}


def write_benchmarks(conn, rollups: dict, source_run_id: str) -> None:
    ts = now_iso()
    for h, r in rollups.items():
        if r["n"] == 0:
            continue
        bid = f"BENCH-{ts.replace(':','').replace('-','')}-{h}"
        conn.execute(
            "INSERT INTO benchmarks (id, captured_at, horizon, period_start, period_end, "
            "portfolio_return_pct, spy_return_pct, alpha_pct, sharpe_estimate, turnover_pct, "
            "source_run_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (bid, ts, h, r["period_start"] or ts, r["period_end"] or ts,
             r["portfolio_return_pct"], r["spy_return_pct"], r["alpha_pct"],
             None, None, source_run_id),
        )
    conn.commit()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--no-write", action="store_true")
    args = p.parse_args(argv)
    conn = connect()
    rollups = rollup(conn)
    verdict = assess(rollups)
    run_id = "BENCHRUN-" + now_iso().replace(":", "").replace("-", "")
    payload = {"run_id": run_id, "checked_at": now_iso(),
               "rollups": rollups, **verdict}
    emit(payload)
    if not args.no_write:
        write_benchmarks(conn, rollups, run_id)
        audit(conn, actor="developer", entity_type="benchmark_run", entity_id=run_id,
              action="audit",
              rationale=f"color={verdict['color']} horizons_with_data={sum(1 for r in rollups.values() if r['n']>0)}")
        conn.commit()
    return 0 if verdict["color"] != "red" else 1


if __name__ == "__main__":
    sys.exit(main())
