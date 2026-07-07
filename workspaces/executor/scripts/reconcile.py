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
import uuid
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
        "FROM orders WHERE status NOT IN ('filled','canceled','cancelled','rejected','expired',"
        "'done_for_day','closed_unknown','closed')"
    ).fetchall()
    return {r["broker_order_id"]: dict(r) for r in rows}


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _insert_placeholder_hypothesis(conn, symbol: str) -> str:
    now = now_iso()
    hid = _new_id("HYP-SYNC")
    conn.execute(
        "INSERT INTO hypotheses (id, created_at, created_by, tickers, thesis_summary, state, rationale_concise) "
        "VALUES (?, ?, 'executor', ?, ?, 'active', ?)",
        (
            hid,
            now,
            json.dumps([symbol.upper()]),
            f"Broker-synced placeholder hypothesis for {symbol.upper()}",
            "Auto-created by reconcile.py --repair to mirror broker state.",
        ),
    )
    return hid


def apply_repairs(conn, result: dict) -> dict:
    """Apply conservative, deterministic repairs for position drifts.

    Repairs performed:
    - position_in_broker_not_in_db: create placeholder hypothesis + open position.
    - position_in_db_not_in_broker: mark DB position closed with qty=0.
    - qty_mismatch: sync DB qty/current fields to broker qty.
    - order_in_db_not_in_broker: mark order status closed_unknown.

    We intentionally do not auto-insert broker orders missing in DB because
    `orders.trade_intent_id` requires a valid trade_intent foreign key.
    """
    repaired = []
    unresolved = []
    if result.get("broker_data_suspect"):
        return {"repaired": [], "unresolved": [{
            "type": "broker_data_suspect",
            "reason": result.get("suspect_reason"),
            "action": "refused_all_repairs",
        }]}
    now = now_iso()
    by_type = {}
    for d in result.get("divergences", []):
        by_type.setdefault(d.get("type"), []).append(d)

    for d in by_type.get("position_in_broker_not_in_db", []):
        broker = d.get("broker", {})
        symbol = (d.get("ticker") or "").upper()
        try:
            hid = _insert_placeholder_hypothesis(conn, symbol)
            pid = _new_id("POS-SYNC")
            qty = float(broker.get("qty", 0) or 0)
            avg_entry = float(broker.get("avg_entry_price", 0) or 0)
            current_price = float(broker.get("current_price", 0) or 0)
            market_value = float(broker.get("market_value", 0) or 0)
            conn.execute(
                "INSERT INTO positions (id, hypothesis_id, ticker, vehicle, qty, cost_basis, current_price, current_value, state, opened_at) "
                "VALUES (?, ?, ?, 'equity', ?, ?, ?, ?, 'open', ?)",
                (pid, hid, symbol, qty, avg_entry, current_price, market_value, now),
            )
            repaired.append({"type": d["type"], "ticker": symbol, "action": "created_placeholder_position", "position_id": pid})
        except Exception as exc:
            unresolved.append({"type": d["type"], "ticker": symbol, "reason": str(exc)[:240]})

    for d in by_type.get("position_in_db_not_in_broker", []):
        symbol = (d.get("ticker") or "").upper()
        conn.execute(
            "UPDATE positions SET state='closed', qty=0, closed_at=? WHERE ticker=? AND state IN ('opening','open','scaling','trimming','closing')",
            (now, symbol),
        )
        repaired.append({"type": d["type"], "ticker": symbol, "action": "closed_db_position"})

    for d in by_type.get("qty_mismatch", []):
        symbol = (d.get("ticker") or "").upper()
        broker_qty = float(d.get("broker_qty", 0) or 0)
        broker = d.get("broker", {})
        avg_entry = float(broker.get("avg_entry_price", 0) or 0)
        current_price = float(broker.get("current_price", 0) or 0)
        market_value = float(broker.get("market_value", 0) or 0)
        cost_basis_total = float(broker.get("cost_basis", 0) or 0)
        unrealized_pl = float(broker.get("unrealized_pl", 0) or 0)
        unrealized_plpc = float(broker.get("unrealized_plpc", 0) or 0)

        # Keep per-share basis stable after splits by deriving from total
        # cost basis when present; fall back to broker avg entry otherwise.
        cost_basis_per_share = (
            (cost_basis_total / broker_qty)
            if broker_qty and cost_basis_total
            else avg_entry
        )

        conn.execute(
            "UPDATE positions SET qty=?, cost_basis=?, current_price=?, current_value=?, "
            "unrealized_pnl_pct=?, pnl_slippage_adjusted=? "
            "WHERE ticker=? AND state IN ('opening','open','scaling','trimming','closing')",
            (
                broker_qty,
                cost_basis_per_share,
                current_price,
                market_value,
                unrealized_plpc * 100.0,
                unrealized_pl,
                symbol,
            ),
        )
        repaired.append({"type": d["type"], "ticker": symbol, "action": "synced_position_qty"})

    for d in by_type.get("order_in_db_not_in_broker", []):
        oid = d.get("broker_order_id")
        conn.execute("UPDATE orders SET status='closed_unknown' WHERE broker_order_id=?", (oid,))
        repaired.append({"type": d["type"], "broker_order_id": oid, "action": "marked_order_closed_unknown"})

    for d in by_type.get("order_in_broker_not_in_db", []):
        unresolved.append({
            "type": d.get("type"),
            "broker_order_id": d.get("broker_order_id"),
            "reason": "cannot insert order without valid trade_intent foreign key",
        })

    # Advance trade_intents whose broker order is no longer open (filled/closed) out of the OPEN-intents
    # count. Without this, filled orders leave their intents stuck 'submitted' forever, silently consuming
    # the desk-wide open-intent throttle until it re-blocks new ideas (the 2026-06-22 incident).
    adv = conn.execute(
        "UPDATE trade_intents SET state='filled' WHERE state IN ('submitted','partial','approved') "
        "AND broker_order_id IN (SELECT broker_order_id FROM orders WHERE status IN "
        "('filled','closed_unknown','closed','canceled','cancelled','expired','rejected','done_for_day'))"
    ).rowcount
    if adv:
        repaired.append({"type": "stale_open_intents", "action": "advanced_to_filled", "count": adv})

    return {"repaired": repaired, "unresolved": unresolved}


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
    bord = {}
    for o in broker_orders:
        oid = o.get("id")
        coid = o.get("client_order_id")
        if oid:
            bord[oid] = o
        if coid:
            bord[coid] = o
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
                "ticker": sym,
                "broker_qty": float(bpos[sym].get("qty", 0)),
                "broker": bpos[sym],
            })

    for oid, o in dord.items():
        if oid not in bord:
            divergences.append({
                "type": "order_in_db_not_in_broker",
                "broker_order_id": oid, "db_status": o["status"], "symbol": o["symbol"],
            })
    for o in broker_orders:
        # match on either id — bord double-keys each order under UUID and
        # client_order_id, so iterating bord flags every DB-known order once
        # as a phantom under its other key
        if o.get("id") not in dord and o.get("client_order_id") not in dord:
            divergences.append({
                "type": "order_in_broker_not_in_db",
                "broker_order_id": o.get("id"), "broker_status": o.get("status"),
                "symbol": o.get("symbol"),
            })

    # --- Broker-data sanity guard (2026-07-07 incident) ---------------------
    # Alpaca's positions endpoint transiently served 3 of 24 positions ("$24k
    # vanished") for a window on 2026-07-07; cash was intact and no sell orders
    # existed. A liquidation ALWAYS leaves closing orders + proceeds, so:
    # if several DB positions are "missing" at the broker but none of those
    # symbols has a recent filled SELL order, the broker DATA is suspect —
    # flag it and let apply_repairs refuse to act. Repairing against a glitch
    # would close a healthy book.
    missing = [d["ticker"] for d in divergences if d["type"] == "position_in_db_not_in_broker"]
    broker_data_suspect = False
    suspect_reason = None
    if len(missing) >= 3:
        try:
            closed_orders = list_orders(status="closed", limit=200)
            sold = {o["symbol"].upper() for o in closed_orders
                    if o.get("side") == "sell" and o.get("status") == "filled"}
            no_proceeds = [s for s in missing if s not in sold]
            if len(no_proceeds) >= 3:
                broker_data_suspect = True
                suspect_reason = (f"{len(missing)} DB positions missing at broker but "
                                  f"{len(no_proceeds)} of them have NO filled sell order — "
                                  "positions cannot vanish without proceeds; broker positions "
                                  "endpoint is likely serving stale/partial data. NO repairs; re-poll later.")
        except ConnectorError:
            broker_data_suspect = True
            suspect_reason = "could not verify closing orders — refusing to trust the divergence"

    return {
        "divergences": divergences,
        "broker_data_suspect": broker_data_suspect,
        "suspect_reason": suspect_reason,
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
    p.add_argument("--repair", action="store_true", help="apply conservative DB repairs for detected drifts")
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
    rid = _new_id("RECON")
    repairs = {"repaired": [], "unresolved": []}
    if args.repair:
        repairs = apply_repairs(conn, result)

    div_count = result["summary"]["divergence_count"]
    payload = dict(result)
    payload["repairs"] = repairs
    conn.execute(
        "INSERT INTO reconciliation_runs (id, started_at, finished_at, divergences_json, resolved) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            rid,
            started,
            now_iso(),
            json.dumps(payload),
            1 if (div_count == 0 and len(repairs["unresolved"]) == 0) else 0,
        ),
    )
    audit(conn, actor="executor", entity_type="reconciliation_run", entity_id=rid,
          action="reconcile",
          rationale=f"alpaca vs db: db_pos={result['summary']['db_positions']} "
                    f"broker_pos={result['summary']['broker_positions']} "
                    f"divergences={div_count} repaired={len(repairs['repaired'])} "
                    f"unresolved={len(repairs['unresolved'])}")
    conn.commit()

    print(json.dumps({"reconciliation_run_id": rid, "repairs": repairs}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
