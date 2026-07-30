#!/usr/bin/env python3
"""Invalidate a forecast whose point-in-time inputs were not valid.

This is intentionally different from grading it wrong: invalid data must not
affect Brier scores or mechanism posteriors.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))
OPEN_POSITION_STATES = ("opening", "open", "scaling", "trimming", "closing")
OPEN_INTENT_STATES = ("proposed", "critic_review", "risk_review", "approved", "submitted", "partially_filled")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def invalidate(
    conn: sqlite3.Connection,
    prediction_id: str,
    reason: str,
    *,
    retire_hypothesis: bool = False,
    execute: bool = False,
) -> dict:
    conn.row_factory = sqlite3.Row
    prediction = conn.execute(
        "SELECT * FROM predictions WHERE id=?", (prediction_id,)
    ).fetchone()
    if not prediction:
        raise ValueError(f"prediction not found: {prediction_id}")
    hypothesis_id = prediction["hypothesis_id"]
    hypothesis_state = conn.execute(
        "SELECT state FROM hypotheses WHERE id=?", (hypothesis_id,)
    ).fetchone()
    prior_invalidation = conn.execute(
        "SELECT 1 FROM audits WHERE entity_type='prediction' AND entity_id=? "
        "AND action='invalidate_bad_inputs' LIMIT 1",
        (prediction_id,),
    ).fetchone()
    if (
        prediction["realized_outcome"] == "inconclusive"
        and prior_invalidation
        and (not retire_hypothesis or (hypothesis_state and hypothesis_state[0] == "retired"))
    ):
        return {
            "prediction_id": prediction_id,
            "hypothesis_id": hypothesis_id,
            "reason": reason,
            "retire_hypothesis": retire_hypothesis,
            "execute": execute,
            "already_invalidated": True,
        }
    positions = conn.execute(
        f"SELECT COUNT(*) FROM positions WHERE hypothesis_id=? "
        f"AND state IN ({','.join('?' for _ in OPEN_POSITION_STATES)})",
        (hypothesis_id, *OPEN_POSITION_STATES),
    ).fetchone()[0]
    intents = conn.execute(
        f"SELECT COUNT(*) FROM trade_intents WHERE hypothesis_id=? "
        f"AND state IN ({','.join('?' for _ in OPEN_INTENT_STATES)})",
        (hypothesis_id, *OPEN_INTENT_STATES),
    ).fetchone()[0]
    observations = conn.execute(
        "SELECT COUNT(*) FROM mechanism_observations "
        "WHERE source_type='prediction' AND source_id=?",
        (prediction_id,),
    ).fetchone()[0]
    if observations:
        raise ValueError(
            f"prediction already has {observations} learning observations; "
            "refusing silent historical rewrite"
        )
    if retire_hypothesis and (positions or intents):
        raise ValueError(
            f"cannot retire hypothesis with open positions={positions} intents={intents}"
        )
    plan = {
        "prediction_id": prediction_id,
        "hypothesis_id": hypothesis_id,
        "reason": reason,
        "retire_hypothesis": retire_hypothesis,
        "open_positions": positions,
        "open_intents": intents,
        "execute": execute,
        "already_invalidated": False,
    }
    if not execute:
        return plan

    now = _now()
    with conn:
        conn.execute(
            "UPDATE predictions SET realized_outcome='inconclusive', "
            "realized_return_pct=NULL, realized_excess_pct=NULL, "
            "brier_component=NULL, resolved_at=? WHERE id=?",
            (now, prediction_id),
        )
        if retire_hypothesis:
            conn.execute(
                "UPDATE hypotheses SET state='retired' WHERE id=?",
                (hypothesis_id,),
            )
        conn.execute(
            "INSERT INTO audits(id,timestamp,actor,entity_type,entity_id,action,"
            "before_state,after_state,rationale_concise,experiment_id) "
            "VALUES(?,?,'developer','prediction',?,'invalidate_bad_inputs',"
            "?, 'inconclusive', ?, 'preproduction_hardening_20260730')",
            (
                "AUDIT-" + uuid.uuid4().hex,
                now,
                prediction_id,
                prediction["realized_outcome"] or "unresolved",
                reason[:500],
            ),
        )
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--prediction-id", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--retire-hypothesis", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    conn = sqlite3.connect(args.db, timeout=60)
    try:
        result = invalidate(
            conn,
            args.prediction_id,
            args.reason,
            retire_hypothesis=args.retire_hypothesis,
            execute=args.execute,
        )
    finally:
        conn.close()
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
