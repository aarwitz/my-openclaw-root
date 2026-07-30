#!/usr/bin/env python3
"""Return baseline-only ready hypotheses to scored for substantive review."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def candidates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT h.id, h.tickers FROM hypotheses h "
        "WHERE h.state='ready' "
        "AND (SELECT c.reviewed_by FROM critic_reviews c WHERE c.target_id=h.id "
        "ORDER BY c.reviewed_at DESC LIMIT 1)='critic_baseline' "
        "AND NOT EXISTS (SELECT 1 FROM positions p WHERE p.hypothesis_id=h.id "
        "AND p.state IN ('opening','open','scaling','trimming','closing')) "
        "AND NOT EXISTS (SELECT 1 FROM trade_intents ti WHERE ti.hypothesis_id=h.id "
        "AND ti.state IN ('proposed','critic_review','risk_review','approved',"
        "'submitted','partially_filled'))"
    ).fetchall()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    conn = sqlite3.connect(args.db, timeout=60)
    conn.row_factory = sqlite3.Row
    rows = candidates(conn)
    if args.execute:
        now = _now()
        with conn:
            for row in rows:
                conn.execute(
                    "UPDATE hypotheses SET state='scored' WHERE id=?",
                    (row["id"],),
                )
                conn.execute(
                    "INSERT INTO audits(id,timestamp,actor,entity_type,entity_id,"
                    "action,before_state,after_state,rationale_concise,experiment_id) "
                    "VALUES(?,?,'developer','hypothesis',?,'requeue_substantive_critic',"
                    "'ready','scored',?,'preproduction_hardening_20260730')",
                    (
                        "AUDIT-" + uuid.uuid4().hex,
                        now,
                        row["id"],
                        "Baseline checklist review is not a substantive adversarial review; "
                        "requeued before intent authoring.",
                    ),
                )
    print(json.dumps({
        "execute": args.execute,
        "requeued": len(rows) if args.execute else 0,
        "candidates": [dict(row) for row in rows],
    }, indent=2))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
