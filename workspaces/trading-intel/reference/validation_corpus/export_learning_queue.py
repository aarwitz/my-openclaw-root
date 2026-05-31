#!/usr/bin/env python3
"""Export resolved thesis outcomes into a review queue for case creation.

This does not modify DB state. It builds a human-review queue file from existing
trading-intel SQLite data so new validation cases can be approved incrementally.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUT = BASE / "tmp" / "learning_queue.jsonl"
DEFAULT_DB = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))


def to_text(value: str | None) -> str:
    return value.strip() if isinstance(value, str) else ""


def main() -> int:
    db_path = DEFAULT_DB
    if not db_path.exists():
        print(f"db not found: {db_path}")
        return 1

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    query = """
    SELECT
      h.id AS hypothesis_id,
      h.state,
      h.resolved_at,
      h.resolved_state,
      h.archivist_grade,
      h.thesis_summary,
      h.tickers,
      p.grade AS postmortem_grade,
      p.external_mechanism_check_json,
      p.experiment_id AS postmortem_experiment_id
    FROM hypotheses h
    LEFT JOIN postmortems p ON p.hypothesis_id = h.id
    WHERE h.resolved_at IS NOT NULL
      AND h.state IN ('resolved','retired')
    ORDER BY h.resolved_at DESC
    LIMIT 200
    """

    rows = conn.execute(query).fetchall()
    conn.close()

    OUT.parent.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc).isoformat()
    with OUT.open("w") as f:
        for row in rows:
            thesis = to_text(row["thesis_summary"])
            if not thesis:
                continue
            item = {
                "queue_generated_at": now,
                "status": "pending_review",
                "hypothesis_id": row["hypothesis_id"],
                "resolved_at": row["resolved_at"],
                "resolved_state": row["resolved_state"],
                "archivist_grade": row["archivist_grade"],
                "postmortem_grade": row["postmortem_grade"],
                "tickers": row["tickers"],
                "thesis_summary": thesis,
                "mechanism_check_raw": row["external_mechanism_check_json"],
                "suggested_case_class": "post_cutoff",
                "suggested_direction": "none",
                "notes": "review and convert to raw_case JSON if this is a strong, externally checkable example",
                "experiment_id": row["postmortem_experiment_id"] or "validation_corpus_live",
            }
            f.write(json.dumps(item) + "\n")

    print(f"wrote: {OUT}")
    print(f"rows: {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
