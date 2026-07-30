#!/usr/bin/env python3
"""Reopen forecasts graded before their own return window matured.

The retired hypothesis-level grader copied a hypothesis verdict onto every
forecast. A corrupt grade has ``resolved_at`` and a Brier component but no
realized return/excess. This repair removes only prediction-sourced mechanism
observations for those rows, clears the fabricated grade, and records an audit.

Explicit invalidations (``invalidate_bad_inputs``) remain resolved and are not
selected. Dry-run is the default; pass ``--apply`` to write.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("/home/aaron/.openclaw/state/trading-intel.sqlite")
EXPERIMENT_ID = "prediction_grade_repair_20260730"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def candidates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """
        SELECT p.id, p.hypothesis_id, p.predicted_at, p.horizon,
               p.realized_outcome, p.brier_component, p.resolved_at,
               (SELECT COUNT(*) FROM mechanism_observations mo
                WHERE mo.source_type='prediction' AND mo.source_id=p.id) AS observations
        FROM predictions p
        WHERE p.resolved_at IS NOT NULL
          AND p.realized_excess_pct IS NULL
          AND p.realized_return_pct IS NULL
          AND NOT EXISTS (
            SELECT 1 FROM audits a
            WHERE a.entity_type='prediction' AND a.entity_id=p.id
              AND a.action='invalidate_bad_inputs'
          )
        ORDER BY p.predicted_at, p.id
        """
    ).fetchall()


def repair(conn: sqlite3.Connection, *, apply: bool) -> dict:
    rows = candidates(conn)
    plan = [
        {
            "prediction_id": row["id"],
            "predicted_at": row["predicted_at"],
            "horizon": row["horizon"],
            "old_outcome": row["realized_outcome"],
            "observations_to_remove": row["observations"],
        }
        for row in rows
    ]
    if not apply:
        return {"dry_run": True, "reopened": 0, "plan": plan}

    now = _now()
    removed = 0
    with conn:
        for row in rows:
            removed += conn.execute(
                "DELETE FROM mechanism_observations "
                "WHERE source_type='prediction' AND source_id=?",
                (row["id"],),
            ).rowcount
            conn.execute(
                """
                UPDATE predictions
                SET realized_outcome=NULL,
                    realized_return_pct=NULL,
                    realized_excess_pct=NULL,
                    brier_component=NULL,
                    resolved_at=NULL
                WHERE id=?
                """,
                (row["id"],),
            )
            conn.execute(
                """
                INSERT INTO audits (
                  id, timestamp, actor, entity_type, entity_id, action,
                  before_state, after_state, rationale_concise, experiment_id
                ) VALUES (
                  ?, ?, 'developer', 'prediction', ?,
                  'reopen_premature_prediction',
                  ?, 'unresolved', ?, ?
                )
                """,
                (
                    "AUDIT-" + uuid.uuid4().hex,
                    now,
                    row["id"],
                    row["realized_outcome"] or "resolved_without_return",
                    (
                        f"retired hypothesis-level grader resolved {row['horizon']} "
                        f"forecast without realized return/excess; removed "
                        f"{row['observations']} contaminated learning observations"
                    ),
                    EXPERIMENT_ID,
                ),
            )
    return {
        "dry_run": False,
        "reopened": len(rows),
        "observations_removed": removed,
        "plan": plan,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    conn = sqlite3.connect(args.db, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        result = repair(conn, apply=args.apply)
    finally:
        conn.close()
    print(json.dumps({"ok": True, **result}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
