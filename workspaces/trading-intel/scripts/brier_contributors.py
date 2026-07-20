#!/usr/bin/env python3
"""Deterministic 30d Brier contributor report.

Breaks down resolved-prediction Brier by mechanism, regime, and horizon, ranks
contributors by total Brier mass, and surfaces both:
  * the overall worst bucket (including mechanism='(none)' coverage debt), and
  * the worst linked mechanism bucket (the largest actionable named mechanism).

It can also replay the resolved cohort under the pre-fix family selector and
the post-fix horizon-preferred selector to measure the targeted delta.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path("/home/aaron/.openclaw")
DB_PATH = ROOT / "state/trading-intel.sqlite"
sys.path.insert(0, str(ROOT / "workspaces/quant/scripts"))
import predict  # noqa: E402


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _parse_mechanism_ids(raw: str | None) -> list[str]:
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    out = []
    for item in data:
        if isinstance(item, dict) and item.get("id"):
            out.append(item["id"])
        elif isinstance(item, str):
            out.append(item)
    return out


def load_resolved_rows(conn: sqlite3.Connection, days: int) -> list[dict]:
    rows = conn.execute(
        """
        SELECT p.id AS prediction_id, p.hypothesis_id, p.predicted_at, p.resolved_at,
               p.horizon, p.p_correct, p.brier_component, p.realized_outcome,
               p.regime_at_prediction, p.mechanism_ids_json, p.evidence_quality,
               h.created_at AS hypothesis_created_at, h.thesis_summary, h.time_horizon,
               h.state AS hypothesis_state, h.tickers
        FROM predictions p
        JOIN hypotheses h ON h.id = p.hypothesis_id
        WHERE p.resolved_at > datetime('now', ?) AND p.brier_component IS NOT NULL
        ORDER BY p.resolved_at DESC, p.id DESC
        """,
        (f"-{days} days",),
    ).fetchall()
    out = []
    for row in rows:
        item = dict(row)
        item["mechanism_ids"] = _parse_mechanism_ids(row["mechanism_ids_json"])
        out.append(item)
    return out


def rank_contributors(rows: list[dict]) -> list[dict]:
    buckets: dict[tuple[str, str, str], dict] = {}
    for row in rows:
        mechanism_ids = row["mechanism_ids"] or ["(none)"]
        for mechanism in mechanism_ids:
            key = (mechanism, row["regime_at_prediction"] or "(none)", row["horizon"] or "(none)")
            bucket = buckets.setdefault(
                key,
                {
                    "mechanism": mechanism,
                    "regime": key[1],
                    "horizon": key[2],
                    "count": 0,
                    "total_brier": 0.0,
                },
            )
            bucket["count"] += 1
            bucket["total_brier"] += float(row["brier_component"] or 0.0)
    ranked = []
    for bucket in buckets.values():
        bucket["mean_brier"] = round(bucket["total_brier"] / bucket["count"], 6)
        bucket["total_brier"] = round(bucket["total_brier"], 6)
        ranked.append(bucket)
    ranked.sort(
        key=lambda item: (
            item["total_brier"],
            item["count"],
            item["mean_brier"],
            item["mechanism"],
            item["regime"],
            item["horizon"],
        ),
        reverse=True,
    )
    return ranked


def dimension_breakdown(rows: list[dict]) -> dict[str, list[dict]]:
    dims = {
        "mechanism": defaultdict(lambda: {"count": 0, "total_brier": 0.0}),
        "regime": defaultdict(lambda: {"count": 0, "total_brier": 0.0}),
        "horizon": defaultdict(lambda: {"count": 0, "total_brier": 0.0}),
    }
    for row in rows:
        dims["regime"][row["regime_at_prediction"] or "(none)"]["count"] += 1
        dims["regime"][row["regime_at_prediction"] or "(none)"]["total_brier"] += float(row["brier_component"] or 0.0)
        dims["horizon"][row["horizon"] or "(none)"]["count"] += 1
        dims["horizon"][row["horizon"] or "(none)"]["total_brier"] += float(row["brier_component"] or 0.0)
        for mechanism in row["mechanism_ids"] or ["(none)"]:
            dims["mechanism"][mechanism]["count"] += 1
            dims["mechanism"][mechanism]["total_brier"] += float(row["brier_component"] or 0.0)
    out: dict[str, list[dict]] = {}
    for dim, agg in dims.items():
        items = []
        for key, value in agg.items():
            items.append(
                {
                    dim: key,
                    "count": value["count"],
                    "total_brier": round(value["total_brier"], 6),
                    "mean_brier": round(value["total_brier"] / value["count"], 6),
                }
            )
        items.sort(key=lambda item: (item["total_brier"], item["count"], item["mean_brier"], str(item[dim])), reverse=True)
        out[dim] = items
    return out


def replay_mean_brier(
    conn: sqlite3.Connection,
    rows: list[dict],
    mechs: dict[str, dict],
    *,
    prefer_horizon: bool,
) -> dict:
    total = 0.0
    bucket_totals: dict[str, list[float]] = defaultdict(list)
    for row in rows:
        hyp = (
            row["hypothesis_id"],
            row["hypothesis_created_at"],
            row["thesis_summary"],
            row["time_horizon"],
            row["hypothesis_state"],
            row["tickers"],
        )
        pred = predict.build_prediction(
            conn,
            hyp,
            row["mechanism_ids"],
            mechs,
            row["regime_at_prediction"],
            prefer_horizon=prefer_horizon,
        )
        bit = 1.0 if row["realized_outcome"] == "correct" else 0.0
        brier = (pred["p_correct"] - bit) ** 2
        total += brier
        for mechanism in row["mechanism_ids"] or ["(none)"]:
            bucket_totals[mechanism].append(brier)
    mean = total / len(rows) if rows else None
    return {
        "mean_brier": None if mean is None else round(mean, 6),
        "mechanism_means": {
            mechanism: round(sum(values) / len(values), 6)
            for mechanism, values in bucket_totals.items()
        },
    }


def build_report(conn: sqlite3.Connection, days: int) -> dict:
    rows = load_resolved_rows(conn, days)
    ranked = rank_contributors(rows)
    breakdown = dimension_breakdown(rows)
    mechs = predict.load_mechanisms(conn)
    baseline = replay_mean_brier(conn, rows, mechs, prefer_horizon=False) if rows else {"mean_brier": None, "mechanism_means": {}}
    fixed = replay_mean_brier(conn, rows, mechs, prefer_horizon=True) if rows else {"mean_brier": None, "mechanism_means": {}}

    worst_overall = ranked[0] if ranked else None
    worst_linked = next((item for item in ranked if item["mechanism"] != "(none)"), None)
    selected = worst_linked or worst_overall
    selected_mech = selected["mechanism"] if selected else None
    selection_reason = None
    if worst_overall and worst_overall["mechanism"] == "(none)" and worst_linked:
        selection_reason = (
            "Overall worst bucket is unlinked mechanism coverage debt; selected the largest named "
            "mechanism bucket for the deterministic predictor fix."
        )
    elif selected:
        selection_reason = "Selected the highest total-Brier contributor by mechanism/regime/horizon."

    actual_mean = round(sum(float(row["brier_component"]) for row in rows) / len(rows), 6) if rows else None
    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "window_days": days,
        "resolved_predictions": len(rows),
        "actual_mean_brier": actual_mean,
        "contributors": ranked,
        "breakdown": breakdown,
        "worst_overall": worst_overall,
        "worst_linked": worst_linked,
        "selected_contributor": selected,
        "selection_reason": selection_reason,
        "replay": {
            "baseline_family_most_observed": baseline,
            "fixed_family_horizon_preferred": fixed,
            "delta_mean_brier": (
                None
                if baseline["mean_brier"] is None or fixed["mean_brier"] is None
                else round(baseline["mean_brier"] - fixed["mean_brier"], 6)
            ),
            "selected_contributor_delta": (
                None
                if not selected_mech
                else round(
                    baseline["mechanism_means"].get(selected_mech, 0.0)
                    - fixed["mechanism_means"].get(selected_mech, 0.0),
                    6,
                )
            ),
        },
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args(argv)
    conn = _connect(args.db)
    try:
        report = build_report(conn, args.days)
    finally:
        conn.close()
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
