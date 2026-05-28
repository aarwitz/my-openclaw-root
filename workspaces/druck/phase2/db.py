from __future__ import annotations

import sqlite3
from pathlib import Path

SQLITE_TIMEOUT_SEC = 30.0
SQLITE_BUSY_TIMEOUT_MS = 30_000


def connect(db_path: Path, *, write: bool = False) -> sqlite3.Connection:
    conn = sqlite3.connect(
        db_path,
        timeout=SQLITE_TIMEOUT_SEC,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    if write:
        conn.execute("BEGIN IMMEDIATE")
    return conn
