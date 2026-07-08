#!/usr/bin/env python3
"""
pipeline_status.py — emit a compact JSON snapshot of canonical DB
state, used by the overseer cron prompt to decide which agents to
spawn this pass.

Output (single line JSON):
{
  "now_utc": "...",
  "hypotheses_total": N,
  "hypotheses_by_state": {"raw": .., "scored": .., "challenged": .., "ready": .., "active": ..},
  "oldest_unscored_age_min": <int or null>,
  "last_researcher_pass_age_min": <int or null>,
  "intents_pending": N,
  "intents_ready_to_submit": N,
  "orders_open": N,
  "last_archivist_pass_age_min": <int or null>,
  "regime_age_min": <int or null>,
  "regime_current": "<bull|bear|neutral|risk_off|unknown>"
}

Reads from ~/.openclaw/state/trading-intel.sqlite. Read-only.
"""
from __future__ import annotations
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper

require_wrapper()

DB = Path("/home/aaron/.openclaw/state/trading-intel.sqlite")


def _age_min(iso: str | None) -> int | None:
    if not iso:
        return None
    try:
        # tolerate "Z" suffix and naive isoformat
        s = iso.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        delta = datetime.now(timezone.utc) - dt
        return max(0, int(delta.total_seconds() // 60))
    except Exception:
        return None


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    row = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def _col_exists(con: sqlite3.Connection, table: str, col: str) -> bool:
    if not _table_exists(con, table):
        return False
    rows = con.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == col for r in rows)


def main() -> int:
    if not DB.exists():
        print(json.dumps({"error": f"db missing: {DB}"}))
        return 1

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    out: dict = {
        "now_utc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        ),
        "hypotheses_total": 0,
        "hypotheses_by_state": {},
        "oldest_unscored_age_min": None,
        "last_researcher_pass_age_min": None,
        "intents_pending": 0,
        "intents_ready_to_submit": 0,
        "orders_open": 0,
        "last_archivist_pass_age_min": None,
        "regime_age_min": None,
        "regime_current": "unknown",
    }

    if _table_exists(con, "hypotheses"):
        rows = con.execute(
            "SELECT state, COUNT(*) AS n FROM hypotheses GROUP BY state"
        ).fetchall()
        by_state = {r["state"]: r["n"] for r in rows}
        out["hypotheses_by_state"] = by_state
        out["hypotheses_total"] = sum(by_state.values())

        # Oldest raw hypothesis age
        ts_col = "created_at" if _col_exists(con, "hypotheses", "created_at") else None
        if ts_col:
            row = con.execute(
                f"SELECT MIN({ts_col}) AS oldest FROM hypotheses WHERE state='raw'"
            ).fetchone()
            out["oldest_unscored_age_min"] = _age_min(row["oldest"] if row else None)

            row = con.execute(
                f"SELECT MAX({ts_col}) AS newest FROM hypotheses WHERE created_by='researcher'"
            ).fetchone()
            out["last_researcher_pass_age_min"] = _age_min(
                row["newest"] if row else None
            )

    if _table_exists(con, "trade_intents"):
        state_col = "status" if _col_exists(con, "trade_intents", "status") else (
            "state" if _col_exists(con, "trade_intents", "state") else None
        )
        if state_col:
            row = con.execute(
                f"SELECT COUNT(*) AS n FROM trade_intents WHERE {state_col} IN ('pending','draft','proposed')"
            ).fetchone()
            out["intents_pending"] = row["n"] if row else 0
            row = con.execute(
                f"SELECT COUNT(*) AS n FROM trade_intents WHERE {state_col} IN ('ready','approved','gated_green')"
            ).fetchone()
            out["intents_ready_to_submit"] = row["n"] if row else 0

    if _table_exists(con, "orders"):
        state_col = "status" if _col_exists(con, "orders", "status") else (
            "state" if _col_exists(con, "orders", "state") else None
        )
        if state_col:
            row = con.execute(
                f"SELECT COUNT(*) AS n FROM orders WHERE {state_col} IN ('open','accepted','partially_filled','pending_new')"
            ).fetchone()
            out["orders_open"] = row["n"] if row else 0

    if _table_exists(con, "audits"):
        actor_col = (
            "actor" if _col_exists(con, "audits", "actor") else
            ("agent_id" if _col_exists(con, "audits", "agent_id") else None)
        )
        # live schema names the column `timestamp` (was probing created_at/at
        # only, so archivist freshness reported null forever — pq-0fd8e006c5ef)
        ts_col = next(
            (c for c in ("timestamp", "created_at", "at") if _col_exists(con, "audits", c)),
            None,
        )
        if actor_col and ts_col:
            row = con.execute(
                f"SELECT MAX({ts_col}) AS t FROM audits WHERE {actor_col}='archivist'"
            ).fetchone()
            out["last_archivist_pass_age_min"] = _age_min(row["t"] if row else None)

    if _table_exists(con, "regime"):
        ts_col = (
            "determined_at" if _col_exists(con, "regime", "determined_at") else
            ("created_at" if _col_exists(con, "regime", "created_at") else None)
        )
        cur_col = "current" if _col_exists(con, "regime", "current") else (
            "regime" if _col_exists(con, "regime", "regime") else None
        )
        order_col = ts_col or "rowid"
        if cur_col:
            row = con.execute(
                f"SELECT {cur_col} AS current, "
                f"{ts_col if ts_col else 'NULL'} AS t "
                f"FROM regime ORDER BY {order_col} DESC LIMIT 1"
            ).fetchone()
            if row:
                out["regime_current"] = row["current"] or "unknown"
                out["regime_age_min"] = _age_min(row["t"])

    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
