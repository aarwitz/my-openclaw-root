#!/usr/bin/env python3
"""Executor · execute_intent.py

Pick `trade_intents` rows in state='approved', submit to Alpaca paper, write
`orders` row, advance intent state. Deterministic, fail-loud.

Goal alignment:
- G1: only acts on intents that already passed quant + critic + gate_evaluator.
- G2: deterministic; no LLM; every submission writes an audit with actor='executor'.

Usage:
  python3 execute_intent.py                      # process all approved
  python3 execute_intent.py --intent-id ID       # process one specific intent
  python3 execute_intent.py --dry-run            # log what would happen, no broker hit
  python3 execute_intent.py --max 1              # cap submissions
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Connector path
sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _db import audit, connect, now_iso  # noqa: E402

from connectors.alpaca import ConnectorError, place_order  # noqa: E402


def _select_intents(conn, intent_id: str | None):
    if intent_id:
        sql = ("SELECT id, hypothesis_id, ticker, vehicle, action, size, "
               "entry_price_target, state FROM trade_intents WHERE id=?")
        rows = conn.execute(sql, (intent_id,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, hypothesis_id, ticker, vehicle, action, size, "
            "entry_price_target, state FROM trade_intents "
            "WHERE state='approved' ORDER BY created_at ASC"
        ).fetchall()
    return rows


def _side_from_action(action: str) -> str:
    # open/add/rotate-into  → buy
    # trim/exit             → sell
    if action in ("open", "add"):
        return "buy"
    if action in ("trim", "exit"):
        return "sell"
    if action == "rotate":
        return "buy"  # treat rotate as a buy leg; sell leg should be a separate intent
    raise ValueError(f"unknown action: {action}")


def _qty_from_size(size: float, vehicle: str) -> int:
    """size is in shares for equity vehicles; integer for Alpaca."""
    if vehicle.lower() not in ("equity", "etf", "stock", "direct_equity"):
        raise ValueError(f"vehicle not supported by executor v1: {vehicle}")
    q = int(round(float(size)))
    if q <= 0:
        raise ValueError(f"non-positive qty after rounding: {size}")
    return q


def process(intent_row, *, dry_run: bool, conn) -> dict:
    intent_id = intent_row["id"]
    if intent_row["state"] != "approved":
        return {"intent_id": intent_id, "skipped": True, "reason": f"state={intent_row['state']!r} not approved"}

    side = _side_from_action(intent_row["action"])
    qty = _qty_from_size(intent_row["size"], intent_row["vehicle"])

    if dry_run:
        return {
            "intent_id": intent_id,
            "dry_run": True,
            "would_submit": {
                "symbol": intent_row["ticker"],
                "qty": qty,
                "side": side,
                "order_type": "market",
                "time_in_force": "day",
            },
        }

    client_order_id = f"oc-{intent_id}-{now_iso().replace(':','').replace('-','')}"
    try:
        resp = place_order(
            symbol=intent_row["ticker"],
            qty=qty,
            side=side,
            order_type="market",
            time_in_force="day",
            client_order_id=client_order_id,
        )
    except ConnectorError as exc:
        # mark intent blocked
        conn.execute(
            "UPDATE trade_intents SET state='blocked', blocked_reason=? WHERE id=?",
            (f"alpaca_submit_failed: {str(exc)[:300]}", intent_id),
        )
        audit(conn, actor="executor", entity_type="trade_intent", entity_id=intent_id,
              action="block", before_state="approved", after_state="blocked",
              rationale=f"alpaca submit failed: {str(exc)[:380]}")
        conn.commit()
        return {"intent_id": intent_id, "submitted": False, "error": str(exc)}

    broker_order_id = resp.get("id") or resp.get("client_order_id") or client_order_id
    submitted_at = resp.get("submitted_at") or now_iso()

    conn.execute(
        "INSERT INTO orders (broker_order_id, trade_intent_id, symbol, side, qty, "
        "type, limit_price, status, submitted_at, filled_at, avg_fill_price, raw_payload_path) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (broker_order_id, intent_id, intent_row["ticker"], side, qty, "market",
         None, resp.get("status") or "submitted", submitted_at, None, None, None),
    )
    conn.execute(
        "UPDATE trade_intents SET state='submitted', submitted_at=?, broker_order_id=? "
        "WHERE id=?",
        (submitted_at, broker_order_id, intent_id),
    )
    audit(conn, actor="executor", entity_type="trade_intent", entity_id=intent_id,
          action="submit", before_state="approved", after_state="submitted",
          rationale=f"submitted {side} {qty} {intent_row['ticker']} -> {broker_order_id}")
    conn.commit()
    return {"intent_id": intent_id, "submitted": True, "broker_order_id": broker_order_id,
            "status": resp.get("status"), "submitted_at": submitted_at}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--intent-id", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max", type=int, default=10)
    args = p.parse_args(argv)

    conn = connect()
    rows = _select_intents(conn, args.intent_id)
    results = []
    for r in rows[: args.max]:
        try:
            results.append(process(r, dry_run=args.dry_run, conn=conn))
        except Exception as exc:  # fail-loud at boundary
            results.append({"intent_id": r["id"], "error": f"{type(exc).__name__}: {exc}"})
    print(json.dumps({"processed": len(results), "results": results,
                      "dry_run": bool(args.dry_run)}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
