#!/usr/bin/env python3
"""Executor · reconcile.py

Reconcile Alpaca broker positions + orders against canonical DB.
Writes a `reconciliation_runs` row with divergences_json.

Divergences detected:
  - position_in_db_not_in_broker
  - position_in_broker_not_in_db
  - qty_mismatch
  - order_in_db_not_in_broker (open orders)
  - order_in_broker_not_in_db

Usage:
  python3 reconcile.py                  # write a run
  python3 reconcile.py --dry-run        # only print diff
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _db import audit, connect, now_iso  # noqa: E402

from connectors.alpaca import ConnectorError, list_orders, list_positions  # noqa: E402


def _db_positions(conn) -> dict[str, dict]:
    rows = conn.execute(
        "SELECT id, ticker, vehicle, qty, state FROM positions "
        "WHERE state IN ('opening','open','scaling','trimming','closing')"
    ).fetchall()
    return {r["ticker"].upper(): dict(r) for r in rows}


def _db_open_orders(conn) -> dict[str, dict]:
    rows = conn.execute(
        "SELECT broker_order_id, symbol, side, qty, status, trade_intent_id "
        "FROM orders WHERE status NOT IN ('filled','canceled','cancelled','rejected','expired','done_for_day')"
    ).fetchall()
    return {r["broker_order_id"]: dict(r) for r in rows}


def compute_divergences(conn) -> dict:
    try:
        broker_pos = list_positions()
    except ConnectorError as exc:
        return {"connector_error": f"list_positions: {exc}"}
    try:
        broker_orders = list_orders(status="open", limit=100)
    except ConnectorError as exc:
        return {"connector_error": f"list_orders: {exc}"}

    bpos = {p["symbol"].upper(): p for p in broker_pos}
    dpos = _db_positions(conn)
    bord = {o["id"]: o for o in broker_orders}
    dord = _db_open_orders(conn)

    divergences: list[dict] = []

    for sym, p in dpos.items():
        if sym not in bpos:
            divergences.append({
                "type": "position_in_db_not_in_broker",
                "ticker": sym, "db_qty": p["qty"], "db_state": p["state"],
            })
        else:
            bq = float(bpos[sym].get("qty", 0))
            if abs(bq - float(p["qty"])) > 1e-6:
                divergences.append({
                    "type": "qty_mismatch", "ticker": sym,
                    "db_qty": p["qty"], "broker_qty": bq,
                })
    for sym in bpos:
        if sym not in dpos:
            divergences.append({
                "type": "position_in_broker_not_in_db",
                "ticker": sym, "broker_qty": float(bpos[sym].get("qty", 0)),
            })

    for oid, o in dord.items():
        if oid not in bord:
            divergences.append({
                "type": "order_in_db_not_in_broker",
                "broker_order_id": oid, "db_status": o["status"], "symbol": o["symbol"],
            })
    for oid, o in bord.items():
        if oid not in dord:
            divergences.append({
                "type": "order_in_broker_not_in_db",
                "broker_order_id": oid, "broker_status": o.get("status"),
                "symbol": o.get("symbol"),
            })

    return {
        "divergences": divergences,
        "summary": {
            "db_positions": len(dpos),
            "broker_positions": len(bpos),
            "db_open_orders": len(dord),
            "broker_open_orders": len(bord),
            "divergence_count": len(divergences),
        },
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    conn = connect()
    result = compute_divergences(conn)
    if "connector_error" in result:
        print(json.dumps({"ok": False, **result}, indent=2))
        return 2

    print(json.dumps(result, indent=2, default=str))
    if args.dry_run:
        return 0

    started = now_iso()
    rid = "RECON-" + started.replace(":", "").replace("-", "")
    div_count = result["summary"]["divergence_count"]
    conn.execute(
        "INSERT INTO reconciliation_runs (id, started_at, finished_at, divergences_json, resolved) "
        "VALUES (?, ?, ?, ?, ?)",
        (rid, started, now_iso(), json.dumps(result), 1 if div_count == 0 else 0),
    )
    audit(conn, actor="executor", entity_type="reconciliation_run", entity_id=rid,
          action="reconcile",
          rationale=f"alpaca vs db: db_pos={result['summary']['db_positions']} "
                    f"broker_pos={result['summary']['broker_positions']} "
                    f"divergences={div_count}")
    conn.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
