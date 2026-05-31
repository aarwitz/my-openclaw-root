#!/usr/bin/env python3

"""Generate a clean trader summary from trading-intel SQLite state.

Designed for Telegram-facing /summary responses. No tool-trace output.
"""

import sys
sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper
require_wrapper()


import os
import sqlite3
from pathlib import Path

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))


def q1(conn: sqlite3.Connection, sql: str, params: tuple = ()):
    cur = conn.execute(sql, params)
    return cur.fetchone()


def qall(conn: sqlite3.Connection, sql: str, params: tuple = ()):
    cur = conn.execute(sql, params)
    return cur.fetchall()


def fmt_money(value):
    if value is None:
        return "n/a"
    try:
        return f"${float(value):,.2f}"
    except Exception:
        return str(value)


def main() -> int:
    if not DB_PATH.exists():
        print("DRUCK_SUMMARY")
        print("status: unavailable")
        print(f"reason: db missing at {DB_PATH}")
        return 0

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    hypo_active = q1(conn, "SELECT COUNT(*) AS n FROM hypotheses WHERE state IN ('ready','active')")["n"]
    intents_open = q1(
        conn,
        "SELECT COUNT(*) AS n FROM trade_intents WHERE state IN ('proposed','critic_review','approved','submitted','partial')",
    )["n"]
    positions_open = q1(conn, "SELECT COUNT(*) AS n FROM positions WHERE state IN ('opening','open','scaling','trimming','closing')")["n"]

    regime = q1(
        conn,
        "SELECT current, determined_at FROM regime ORDER BY determined_at DESC LIMIT 1",
    )

    pauses = qall(
        conn,
        "SELECT scope, reason, started_at FROM system_pauses WHERE ended_at IS NULL ORDER BY started_at DESC LIMIT 3",
    )

    top_positions = qall(
        conn,
        """
        SELECT ticker, qty, current_value, pnl_slippage_adjusted, state
        FROM positions
        WHERE state IN ('opening','open','scaling','trimming','closing')
        ORDER BY ABS(COALESCE(current_value,0)) DESC
        LIMIT 5
        """,
    )

    recent_intents = qall(
        conn,
        """
        SELECT id, action, ticker, state, created_at
        FROM trade_intents
        ORDER BY created_at DESC
        LIMIT 5
        """,
    )

    conn.close()

    print("DRUCK_SUMMARY")
    print("status: ok")
    print(f"active_hypotheses: {hypo_active}")
    print(f"open_intents: {intents_open}")
    print(f"open_positions: {positions_open}")

    if regime:
        print(f"regime: {regime['current']} ({regime['determined_at']})")
    else:
        print("regime: n/a")

    if pauses:
        print("active_pauses:")
        for p in pauses:
            print(f"- {p['scope']}: {p['reason']} ({p['started_at']})")
    else:
        print("active_pauses: none")

    print("top_positions:")
    if top_positions:
        for p in top_positions:
            print(
                "- "
                f"{p['ticker']} qty={p['qty']} state={p['state']} value={fmt_money(p['current_value'])} "
                f"pnl_slippage={fmt_money(p['pnl_slippage_adjusted'])}"
            )
    else:
        print("- none")

    print("recent_intents:")
    if recent_intents:
        for i in recent_intents:
            print(f"- {i['created_at']} {i['id']} {i['action']} {i['ticker']} state={i['state']}")
    else:
        print("- none")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
