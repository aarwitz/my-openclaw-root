#!/usr/bin/env python3
"""Capture a deterministic portfolio snapshot (equity + SPY close).

Writes one row into `portfolio_snapshots` so the product app can render
historical equity and compare against SPY without relying on LLM output.

Usage:
  python3 capture_portfolio_snapshot.py
  python3 capture_portfolio_snapshot.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _db import audit, connect, emit, now_iso  # noqa: E402
from connectors.marketdata import ConnectorError, daily_bars  # noqa: E402
sys.path.insert(0, "/home/aaron/.openclaw/workspaces/executor/scripts")
from broker import get_account  # noqa: E402  (adapter, D52)


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
          id                 TEXT PRIMARY KEY,
          captured_at        TEXT NOT NULL,
          equity             REAL NOT NULL,
          last_equity        REAL,
          day_pl             REAL,
          cash               REAL,
          buying_power       REAL,
          spy_close          REAL,
          spy_as_of          TEXT,
          account_status     TEXT,
          source             TEXT NOT NULL DEFAULT 'alpaca_paper'
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_captured_at ON portfolio_snapshots(captured_at)"
    )


def _f(value, default=0.0):
    try:
        if value is None or value == "":
            return float(default)
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _latest_spy_close() -> tuple[float | None, str | None]:
    bars = daily_bars("SPY", days=10)
    if not bars:
        return None, None
    last = bars[-1]
    return _f(last.get("c"), None), last.get("t")


def capture(conn: sqlite3.Connection) -> dict:
    acct = get_account()
    spy_close = None
    spy_as_of = None
    try:
        spy_close, spy_as_of = _latest_spy_close()
    except ConnectorError:
        pass

    row = {
        "id": f"PSNAP-{uuid.uuid4().hex[:12]}",
        "captured_at": now_iso(),
        "equity": _f(acct.get("equity")),
        "last_equity": _f(acct.get("last_equity"), None),
        "day_pl": _f(acct.get("equity"), 0.0) - _f(acct.get("last_equity"), 0.0),
        "cash": _f(acct.get("cash"), None),
        "buying_power": _f(acct.get("buying_power"), None),
        "spy_close": spy_close,
        "spy_as_of": spy_as_of,
        "account_status": acct.get("status"),
    }

    conn.execute(
        "INSERT INTO portfolio_snapshots (id, captured_at, equity, last_equity, day_pl, cash, buying_power, spy_close, spy_as_of, account_status, source) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'alpaca_paper')",
        (
            row["id"],
            row["captured_at"],
            row["equity"],
            row["last_equity"],
            row["day_pl"],
            row["cash"],
            row["buying_power"],
            row["spy_close"],
            row["spy_as_of"],
            row["account_status"],
        ),
    )
    audit(
        conn,
        actor="developer",
        entity_type="portfolio_snapshot",
        entity_id=row["id"],
        action="capture",
        rationale=f"equity={row['equity']} spy_close={row['spy_close']}",
    )
    conn.commit()
    return row


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    conn = connect()
    _ensure_table(conn)
    if args.dry_run:
        try:
            acct = get_account()
            spy_close, spy_as_of = _latest_spy_close()
            emit(
                {
                    "dry_run": True,
                    "captured_at": now_iso(),
                    "equity": _f(acct.get("equity")),
                    "last_equity": _f(acct.get("last_equity"), None),
                    "cash": _f(acct.get("cash"), None),
                    "buying_power": _f(acct.get("buying_power"), None),
                    "spy_close": spy_close,
                    "spy_as_of": spy_as_of,
                }
            )
            return 0
        except ConnectorError as exc:
            emit({"dry_run": True, "ok": False, "error": str(exc)})
            return 2

    try:
        row = capture(conn)
    except ConnectorError as exc:
        emit({"ok": False, "error": str(exc)})
        return 2

    emit({"ok": True, "captured": row})
    return 0


if __name__ == "__main__":
    sys.exit(main())
