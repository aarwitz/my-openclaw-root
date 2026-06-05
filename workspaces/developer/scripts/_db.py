"""Shared helpers for Bessent watchdog scripts."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"DB missing at {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def audit(conn, *, actor: str = "developer", entity_type: str, entity_id: str,
          action: str, rationale: str | None = None,
          before_state: str | None = None, after_state: str | None = None,
          experiment_id: str | None = None) -> str:
    ts = now_iso()
    aid = "AUDIT-" + ts.replace(":", "").replace("-", "") + "-" + entity_id[:24]
    conn.execute(
        "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, "
        "before_state, after_state, rationale_concise, experiment_id) VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (aid, ts, actor, entity_type, entity_id, action, before_state, after_state,
         (rationale or "")[:500], experiment_id),
    )
    return aid


def emit(payload: dict) -> None:
    print(json.dumps(payload, indent=2, default=str))
