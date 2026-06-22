"""Shared DB helpers for executor scripts."""

from __future__ import annotations

import os
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"trading-intel DB missing at {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def audit(
    conn: sqlite3.Connection,
    *,
    actor: str,
    entity_type: str,
    entity_id: str,
    action: str,
    rationale: str | None = None,
    before_state: str | None = None,
    after_state: str | None = None,
    experiment_id: str | None = None,
) -> str:
    ts = now_iso()
    # `audits.id` must stay unique under bursty writes and concurrent scripts.
    # We include nanosecond clock + random suffix and retry on conflict.
    for _ in range(5):
        aid = f"AUDIT-{time.time_ns()}-{uuid.uuid4().hex[:8]}-{entity_id[:24]}"
        try:
            conn.execute(
                "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, "
                "before_state, after_state, rationale_concise, experiment_id) VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (aid, ts, actor, entity_type, entity_id, action, before_state, after_state,
                 (rationale or "")[:500], experiment_id),
            )
            return aid
        except sqlite3.IntegrityError:
            continue

    raise RuntimeError("failed to allocate unique audits.id after retries")
