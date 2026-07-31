#!/usr/bin/env python3
"""Preregister, mirror, and grade shadow probability challengers.

This lane has no trading authority. It records paired probabilities for every
forward champion prediction and grades them only after the canonical prediction
resolver writes a conclusive outcome.
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
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path("/home/aaron/.openclaw")
CONFIG = ROOT / "config/prediction-challenger-v1.json"
sys.path.insert(0, str(ROOT / "workspaces/trading-intel/scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from connectors import marketdata  # noqa: E402
from developer_db import audit, connect, now_iso  # noqa: E402

BASE_RATE = {"long": 0.466, "short": 0.534}


def protocol(path: Path = CONFIG) -> dict:
    data = json.loads(path.read_text())
    required = {
        "experiment_id", "protocol_version", "start_at", "minimum_resolved_predictions",
        "minimum_trading_sessions", "variants", "primary_metric", "promotion_rule",
        "trading_authority",
    }
    missing = sorted(required - set(data))
    if missing:
        raise ValueError("protocol missing: " + ",".join(missing))
    if data["trading_authority"] != "none":
        raise ValueError("shadow challenger must have trading_authority=none")
    return data


def register(conn: sqlite3.Connection, cfg: dict) -> dict:
    existing = conn.execute("SELECT * FROM experiments WHERE id=?", (cfg["experiment_id"],)).fetchone()
    if existing:
        return {"registered": False, "experiment_id": cfg["experiment_id"], "reason": "exists"}
    conn.execute(
        "INSERT INTO experiments(id,started_at,scope,hypothesis,outcome_json,decided_by) "
        "VALUES(?,?, 'prediction_calibration_shadow', ?, NULL, 'developer')",
        (cfg["experiment_id"], cfg["start_at"], json.dumps(cfg, sort_keys=True)),
    )
    audit(
        conn, actor="developer", entity_type="experiment", entity_id=cfg["experiment_id"],
        action="preregister", rationale=(
            "Shadow champion/challenger probability calibration; no trading authority; "
            f"start={cfg['start_at']} minimum_n={cfg['minimum_resolved_predictions']} "
            f"minimum_sessions={cfg['minimum_trading_sessions']}"
        ), experiment_id=cfg["experiment_id"],
    )
    conn.commit()
    return {"registered": True, "experiment_id": cfg["experiment_id"]}


def _direction(thesis: str | None) -> str:
    text = str(thesis or "").strip().lower()
    return "short" if text.startswith("short") or text.startswith("bearish") else "long"


def _entry_date(predicted_at: str) -> str:
    parsed = datetime.fromisoformat(predicted_at.replace("Z", "+00:00")).astimezone(marketdata._et())
    day = parsed.date()
    if marketdata._is_session(day):
        close = datetime.combine(day, marketdata._close_time(day), marketdata._et())
        if parsed <= close:
            return day.isoformat()
    return marketdata._next_session(day).isoformat()


def _stable_id(experiment_id: str, prediction_id: str, variant: str) -> str:
    return "pch-" + hashlib.sha256(
        f"{experiment_id}|{prediction_id}|{variant}".encode()
    ).hexdigest()[:24]


def record(conn: sqlite3.Connection, cfg: dict) -> dict:
    registered = conn.execute(
        "SELECT started_at,scope FROM experiments WHERE id=?",
        (cfg["experiment_id"],),
    ).fetchone()
    if not registered or registered["started_at"] != cfg["start_at"]:
        raise RuntimeError("prediction challenger protocol is not preregistered exactly")
    rows = conn.execute(
        "SELECT p.id,p.hypothesis_id,p.predicted_at,p.p_correct,h.thesis_summary "
        "FROM predictions p JOIN hypotheses h ON h.id=p.hypothesis_id "
        "WHERE p.predicted_at>=? ORDER BY p.predicted_at,p.id",
        (cfg["start_at"],),
    ).fetchall()
    written = 0
    for row in rows:
        champion = min(max(float(row["p_correct"]), 1e-6), 1 - 1e-6)
        base = BASE_RATE[_direction(row["thesis_summary"])]
        variants = {
            "champion_v1": champion,
            "base_rate": base,
            "shrink75_to_base": base + 0.25 * (champion - base),
        }
        unknown = sorted(set(variants) - set(cfg["variants"]))
        if unknown or set(cfg["variants"]) != set(variants):
            raise ValueError("code/config challenger variants disagree")
        for variant, probability in variants.items():
            cursor = conn.execute(
                "INSERT OR IGNORE INTO prediction_challengers("
                "id,experiment_id,protocol_version,prediction_id,hypothesis_id,variant,"
                "p_correct,predicted_at,entry_date) VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    _stable_id(cfg["experiment_id"], row["id"], variant), cfg["experiment_id"],
                    cfg["protocol_version"], row["id"], row["hypothesis_id"], variant,
                    round(probability, 6), row["predicted_at"], _entry_date(row["predicted_at"]),
                ),
            )
            written += cursor.rowcount
    conn.commit()
    return {"eligible_predictions": len(rows), "written": written}


def grade(conn: sqlite3.Connection, cfg: dict) -> dict:
    rows = conn.execute(
        "SELECT pc.id,pc.p_correct,p.realized_outcome,p.realized_excess_pct,p.resolved_at "
        "FROM prediction_challengers pc JOIN predictions p ON p.id=pc.prediction_id "
        "WHERE pc.experiment_id=? AND pc.resolved_at IS NULL "
        "AND p.realized_outcome IN ('correct','incorrect') AND p.resolved_at IS NOT NULL",
        (cfg["experiment_id"],),
    ).fetchall()
    for row in rows:
        probability = min(max(float(row["p_correct"]), 1e-6), 1 - 1e-6)
        outcome = 1.0 if row["realized_outcome"] == "correct" else 0.0
        brier = (probability - outcome) ** 2
        log_loss = -(outcome * math.log(probability) + (1 - outcome) * math.log(1 - probability))
        conn.execute(
            "UPDATE prediction_challengers SET realized_outcome=?,realized_excess_pct=?,"
            "brier_score=?,log_loss=?,resolved_at=? WHERE id=? AND resolved_at IS NULL",
            (
                row["realized_outcome"], row["realized_excess_pct"], round(brier, 8),
                round(log_loss, 8), row["resolved_at"], row["id"],
            ),
        )
    conn.commit()
    return {"graded": len(rows)}


def _pearson(pairs: list[tuple[float, float]]) -> float | None:
    if len(pairs) < 3:
        return None
    xs, ys = zip(*pairs)
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    numerator = sum((x - mx) * (y - my) for x, y in pairs)
    denominator = math.sqrt(
        sum((x - mx) ** 2 for x in xs) * sum((y - my) ** 2 for y in ys)
    )
    return None if denominator == 0 else numerator / denominator


def _cluster_ci(differences: list[tuple[str, float]], seed: str) -> list[float] | None:
    clusters: dict[str, list[float]] = defaultdict(list)
    for entry_date, difference in differences:
        clusters[entry_date].append(difference)
    cluster_means = [statistics.fmean(values) for values in clusters.values()]
    if len(cluster_means) < 5:
        return None
    rng = random.Random(seed)
    draws = sorted(
        statistics.fmean(rng.choice(cluster_means) for _ in cluster_means)
        for _ in range(5000)
    )
    return [round(draws[124], 6), round(draws[4874], 6)]


def _trading_sessions_since(start_at: str) -> int:
    start = datetime.fromisoformat(start_at.replace("Z", "+00:00")).date()
    today = datetime.now(timezone.utc).astimezone(marketdata._et()).date()
    return sum(marketdata._is_session(date.fromordinal(day)) for day in range(start.toordinal(), today.toordinal() + 1))


def report(conn: sqlite3.Connection, cfg: dict) -> dict:
    rows = conn.execute(
        "SELECT prediction_id,variant,p_correct,entry_date,brier_score,log_loss,"
        "realized_excess_pct FROM prediction_challengers "
        "WHERE experiment_id=? AND resolved_at IS NOT NULL ORDER BY prediction_id,variant",
        (cfg["experiment_id"],),
    ).fetchall()
    by_prediction: dict[str, dict[str, sqlite3.Row]] = defaultdict(dict)
    for row in rows:
        by_prediction[row["prediction_id"]][row["variant"]] = row
    variants: dict[str, dict] = {}
    champion = "champion_v1"
    for variant in cfg["variants"]:
        variant_rows = [group[variant] for group in by_prediction.values() if variant in group]
        briers = [float(row["brier_score"]) for row in variant_rows]
        losses = [float(row["log_loss"]) for row in variant_rows]
        correlations = [
            (float(row["p_correct"]), float(row["realized_excess_pct"]))
            for row in variant_rows if row["realized_excess_pct"] is not None
        ]
        differences: list[tuple[str, float]] = []
        if variant != champion:
            for group in by_prediction.values():
                if variant in group and champion in group:
                    differences.append((
                        str(group[variant]["entry_date"]),
                        float(group[variant]["brier_score"]) - float(group[champion]["brier_score"]),
                    ))
        variants[variant] = {
            "n": len(variant_rows),
            "mean_brier": None if not briers else round(statistics.fmean(briers), 6),
            "mean_log_loss": None if not losses else round(statistics.fmean(losses), 6),
            "corr_p_excess": None if not correlations else (
                None if _pearson(correlations) is None else round(_pearson(correlations), 4)
            ),
            "paired_brier_delta_vs_champion": (
                None if not differences else round(statistics.fmean(value for _, value in differences), 6)
            ),
            "entry_date_cluster_bootstrap_95ci": _cluster_ci(
                differences, cfg["experiment_id"] + variant
            ) if differences else None,
        }
    sessions = _trading_sessions_since(cfg["start_at"])
    champion_n = variants[champion]["n"]
    return {
        "experiment_id": cfg["experiment_id"],
        "start_at": cfg["start_at"],
        "trading_sessions_elapsed": sessions,
        "minimum_trading_sessions": cfg["minimum_trading_sessions"],
        "resolved_predictions": champion_n,
        "minimum_resolved_predictions": cfg["minimum_resolved_predictions"],
        "eligible_for_decision": (
            sessions >= int(cfg["minimum_trading_sessions"])
            and champion_n >= int(cfg["minimum_resolved_predictions"])
        ),
        "variants": variants,
        "promotion_rule": cfg["promotion_rule"],
        "trading_authority": cfg["trading_authority"],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("register", "record", "grade", "report"))
    args = parser.parse_args(argv)
    cfg = protocol()
    conn = connect()
    if args.command == "register":
        result = register(conn, cfg)
    elif args.command == "record":
        result = record(conn, cfg)
    elif args.command == "grade":
        result = grade(conn, cfg)
    else:
        result = report(conn, cfg)
    conn.close()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
