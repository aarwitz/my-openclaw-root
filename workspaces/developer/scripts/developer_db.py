"""Uniquely named shared DB helpers for Developer and trading-intel scripts.

Do not import these helpers as the generic module name ``_db``. Executor has a
different ``_db.py``; shared-process test discovery can otherwise bind the
wrong helper from ``sys.modules`` based on import order.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"DB missing at {DB_PATH}")
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def audit(conn, *, actor: str = "developer", entity_type: str, entity_id: str,
          action: str, rationale: str | None = None,
          before_state: str | None = None, after_state: str | None = None,
          experiment_id: str | None = None) -> str:
    ts = now_iso()
    aid = f"AUDIT-{ts.replace(':', '').replace('-', '')}-{uuid.uuid4().hex[:12]}"
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
