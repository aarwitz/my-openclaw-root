#!/usr/bin/env python3
"""Bootstrap canonical seeds into the trading-intel SQLite store.

Currently seeds:
- regime_rules v1 from sql/seeds/regime_rules_v1.json

Idempotent: existing rows with the same id are left untouched.

Usage:
    python3 sql/seed_bootstrap.py [path/to/trading-intel.sqlite]

Defaults to ~/.openclaw/state/trading-intel.sqlite.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
DEFAULT_DB = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))
REGIME_SEED = HERE / "seeds" / "regime_rules_v1.json"


def seed_regime_rules(conn: sqlite3.Connection) -> str:
    row = json.loads(REGIME_SEED.read_text())
    cur = conn.execute("SELECT 1 FROM regime_rules WHERE id = ?", (row["id"],))
    if cur.fetchone():
        return f"skip (exists): {row['id']}"
    conn.execute(
        """
        INSERT INTO regime_rules
            (id, rule_version, effective_at, thresholds_json, notes, experiment_id)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            row["id"],
            row["rule_version"],
            row["effective_at"],
            json.dumps(row["thresholds_json"], separators=(",", ":")),
            row.get("notes"),
            row.get("experiment_id"),
        ),
    )
    return f"inserted: {row['id']}"


def main(argv: list[str]) -> int:
    db_path = Path(argv[1]) if len(argv) > 1 else DEFAULT_DB
    if not db_path.exists():
        print(f"db not found: {db_path}", file=sys.stderr)
        return 1
    conn = sqlite3.connect(db_path)
    try:
        print(seed_regime_rules(conn))
        conn.commit()
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
