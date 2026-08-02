#!/usr/bin/env python3
"""Read-only retrospective replay of probabilities frozen on prediction rows.

This is a regression/diagnostic harness, never promotion evidence.  It compares
the recorded production probability with fixed, preregistered-style transforms
that require no fitting: a direction-conditional base rate and 75% shrinkage
toward that base.  Because it uses only values frozen at forecast time, it does
not confuse today's mechanism status or today's evidence with historical state.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path("/home/aaron/.openclaw")
DB_PATH = ROOT / "state/trading-intel.sqlite"
sys.path.insert(0, str(ROOT / "workspaces/trading-intel/scripts"))
import worldmodel as wm  # noqa: E402

BASE_RATE = {"long": 0.466, "short": 0.534}
VARIANTS = ("recorded_champion", "direction_base_rate", "shrink75_to_base")


def _clamp(probability: float) -> float:
    return min(max(float(probability), 1e-6), 1.0 - 1e-6)


def _pearson(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 3:
        return None
    xs, ys = zip(*pairs)
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    denominator = math.sqrt(
        sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)
    )
    if denominator == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in pairs) / denominator


def _cluster_ci(differences: list[tuple[str, float]], seed: str) -> list[float] | None:
    clusters: dict[str, list[float]] = defaultdict(list)
    for forecast_date, difference in differences:
        clusters[forecast_date].append(difference)
    means = [statistics.fmean(values) for values in clusters.values()]
    if len(means) < 5:
        return None
    rng = random.Random(seed)
    draws = sorted(
        statistics.fmean(rng.choice(means) for _ in means)
        for _ in range(5000)
    )
    return [round(draws[124], 6), round(draws[4874], 6)]


def _probabilities(row: dict) -> dict[str, float]:
    champion = _clamp(row["p_correct"])
    base = BASE_RATE[row["direction"]]
    return {
        "recorded_champion": champion,
        "direction_base_rate": base,
        "shrink75_to_base": base + 0.25 * (champion - base),
    }


def _variant_metrics(rows: list[dict]) -> dict[str, dict]:
    metrics: dict[str, dict] = {}
    for variant in VARIANTS:
        probabilities = [_probabilities(row)[variant] for row in rows]
        briers = [
            (probability - row["outcome_bit"]) ** 2
            for probability, row in zip(probabilities, rows)
        ]
        losses = [
            -(
                row["outcome_bit"] * math.log(probability)
                + (1.0 - row["outcome_bit"]) * math.log(1.0 - probability)
            )
            for probability, row in zip(probabilities, rows)
        ]
        correlations = [
            (probability, row["directional_excess_pct"])
            for probability, row in zip(probabilities, rows)
        ]
        differences: list[tuple[str, float]] = []
        distinct = 0
        if variant != "recorded_champion":
            for row, brier in zip(rows, briers):
                champion = _probabilities(row)["recorded_champion"]
                champion_brier = (champion - row["outcome_bit"]) ** 2
                differences.append((row["forecast_date"], brier - champion_brier))
                if abs(_probabilities(row)[variant] - champion) > 1e-12:
                    distinct += 1
        corr = _pearson(correlations)
        metrics[variant] = {
            "n": len(rows),
            "unique_probability_levels": len({round(value, 9) for value in probabilities}),
            "mean_brier": None if not briers else round(statistics.fmean(briers), 6),
            "mean_log_loss": None if not losses else round(statistics.fmean(losses), 6),
            "corr_p_directional_excess": None if corr is None else round(corr, 4),
            "paired_distinct_probability_n": distinct,
            "paired_brier_delta_vs_recorded": (
                None if not differences
                else round(statistics.fmean(value for _, value in differences), 6)
            ),
            "forecast_date_cluster_bootstrap_95ci": (
                _cluster_ci(differences, "retrospective-v1-" + variant)
                if differences else None
            ),
        }
    return metrics


def _prepare_rows(conn: sqlite3.Connection) -> tuple[list[dict], float]:
    source = conn.execute(
        "SELECT p.id,p.predicted_at,p.resolved_at,p.horizon,p.p_correct,"
        "p.realized_outcome,p.realized_excess_pct,p.brier_component,"
        "p.mechanism_ids_json,p.thesis_direction,p.prediction_policy_version,"
        "p.prediction_policy_hash,h.thesis_summary "
        "FROM predictions p JOIN hypotheses h ON h.id=p.hypothesis_id "
        "WHERE p.resolved_at IS NOT NULL "
        "AND p.realized_outcome IN ('correct','incorrect') "
        "AND p.p_correct IS NOT NULL AND p.realized_excess_pct IS NOT NULL "
        "ORDER BY p.predicted_at,p.id"
    ).fetchall()
    rows: list[dict] = []
    max_brier_error = 0.0
    for source_row in source:
        row = dict(source_row)
        direction = row["thesis_direction"] or wm.thesis_direction(row["thesis_summary"])
        outcome_bit = 1.0 if row["realized_outcome"] == "correct" else 0.0
        recomputed = (_clamp(row["p_correct"]) - outcome_bit) ** 2
        if row["brier_component"] is not None:
            max_brier_error = max(
                max_brier_error,
                abs(float(row["brier_component"]) - recomputed),
            )
        rows.append({
            **row,
            "direction": direction,
            "outcome_bit": outcome_bit,
            "directional_excess_pct": wm.directional_excess_pct(
                row["realized_excess_pct"], direction
            ),
            "forecast_date": str(row["predicted_at"])[:10],
            "has_mechanism_links": bool(
                str(row["mechanism_ids_json"] or "").strip() not in ("", "[]")
            ),
        })
    return rows, max_brier_error


def _cohort_summary(rows: list[dict]) -> dict:
    return {
        "n": len(rows),
        "forecast_date_clusters": len({row["forecast_date"] for row in rows}),
        "first_predicted_at": rows[0]["predicted_at"] if rows else None,
        "last_resolved_at": max((row["resolved_at"] for row in rows), default=None),
        "directions": dict(sorted(Counter(row["direction"] for row in rows).items())),
        "horizons": dict(sorted(Counter(row["horizon"] for row in rows).items())),
        "with_mechanism_links": sum(row["has_mechanism_links"] for row in rows),
        "with_frozen_policy_hash": sum(bool(row["prediction_policy_hash"]) for row in rows),
        "policy_versions": dict(sorted(Counter(
            row["prediction_policy_version"] or "(missing)" for row in rows
        ).items())),
        "recorded_exactly_0_50": sum(
            abs(float(row["p_correct"]) - 0.5) < 1e-9 for row in rows
        ),
        "realized_correct_rate": (
            None if not rows else round(statistics.fmean(row["outcome_bit"] for row in rows), 6)
        ),
    }


def build_report(conn: sqlite3.Connection) -> dict:
    rows, max_brier_error = _prepare_rows(conn)
    midpoint = len(rows) // 2
    early, late = rows[:midpoint], rows[midpoint:]
    metrics = _variant_metrics(rows)
    base_delta = metrics["direction_base_rate"]["paired_brier_delta_vs_recorded"]
    warnings = [
        "Retrospective results are development diagnostics only and cannot promote a model.",
        "Recorded champion rows can span code/model versions; this replay estimates outcomes, not causal attribution.",
    ]
    if rows and sum(row["direction"] == "short" for row in rows) / len(rows) < 0.1:
        warnings.append("Short forecasts are under 10% of the cohort; direction comparisons are underpowered.")
    if len({row["horizon"] for row in rows}) < 2:
        warnings.append("Only one forecast horizon is represented; horizon generalization is untested.")
    frozen = [
        {
            key: row[key]
            for key in (
                "id", "predicted_at", "resolved_at", "horizon", "p_correct",
                "realized_outcome", "realized_excess_pct", "direction",
                "prediction_policy_version", "prediction_policy_hash",
            )
        }
        for row in rows
    ]
    source_hash = hashlib.sha256(
        json.dumps(frozen, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return {
        "ok": bool(rows) and max_brier_error <= 1.1e-6,
        "diagnostic": "retrospective_fixed_probability_replay_v1",
        "retrospective_diagnostic_only": True,
        "promotion_authority": "none",
        "database_mutations": False,
        "source_row_sha256": source_hash,
        "stored_brier_max_abs_recompute_error": round(max_brier_error, 10),
        "cohort": _cohort_summary(rows),
        "variants": metrics,
        "chronological_halves": {
            "early": {"cohort": _cohort_summary(early), "variants": _variant_metrics(early)},
            "late": {"cohort": _cohort_summary(late), "variants": _variant_metrics(late)},
        },
        "retrospective_finding": (
            "insufficient_data" if base_delta is None
            else "direction_base_rate_better" if base_delta < 0
            else "recorded_champion_better_or_equal"
        ),
        "warnings": warnings,
    }


def _connect_read_only(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args(argv)
    conn = _connect_read_only(Path(args.db))
    try:
        report = build_report(conn)
    finally:
        conn.close()
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
