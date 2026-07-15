#!/usr/bin/env python3
"""Executor · sync_fills.py

Close the fill-tracking hole (found 2026-07-06): execute_intent records an
order at submit time, but nothing ever updated it once the broker filled it
later. Orders stuck `pending_new` forever; the pre-market reconcile then saw
them vanish from the broker's OPEN-order list, marked them `closed_unknown`
(fill price lost), and re-created the resulting broker positions as POS-SYNC
placeholders with fabricated hypotheses — severing the fill from the real
hypothesis/experiment lineage the learning loop grades against.

This stage queries the broker for each non-terminal DB order BY ID (works for
closed orders), then writes the truth back:
  - orders: status, filled_at, avg_fill_price
  - trade_intents: state='filled', executed_at, actual_price, actual_size
    (or canceled/expired/rejected)
  - positions: upsert against the REAL hypothesis_id from the authoring
    intent — signed qty (short = negative), weighted-avg basis on adds,
    basis unchanged on reduces, closed at ~0.

Deterministic, no LLM, every mutation writes an audit (actor='executor').
Runs in the pass between execute_intent and reconcile; reconcile's placeholder
path remains as a true last resort for orders not placed by the desk.

Usage:
  python3 sync_fills.py                  # sync all non-terminal orders
  python3 sync_fills.py --backfill       # also repair closed_unknown orders and
                                         #   re-link POS-SYNC placeholder positions
  python3 sync_fills.py --dry-run
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

from broker import ConnectorError, get_order  # noqa: E402  (adapter, D52)

TERMINAL = ("filled", "canceled", "cancelled", "rejected", "expired", "closed", "done_for_day")
OPEN_POS_STATES = ("opening", "open", "scaling", "trimming", "closing")


def _signed_delta(side: str, filled_qty: float) -> float:
    return filled_qty if side == "buy" else -filled_qty


def _upsert_position(conn, *, ticker: str, hypothesis_id: str, delta: float,
                     fill_price: float, filled_at: str, dry_run: bool) -> str:
    """Apply a signed fill to the desk position for `ticker`. Returns action taken."""
    row = conn.execute(
        f"SELECT id, qty, cost_basis FROM positions WHERE UPPER(ticker)=? "
        f"AND state IN {OPEN_POS_STATES} ORDER BY opened_at DESC LIMIT 1",
        (ticker.upper(),),
    ).fetchone()

    if row is None:
        pid = f"POS-{uuid.uuid4().hex[:12]}"
        if not dry_run:
            conn.execute(
                "INSERT INTO positions (id, hypothesis_id, ticker, vehicle, qty, cost_basis, "
                "current_price, current_value, state, opened_at) "
                "VALUES (?, ?, ?, 'equity', ?, ?, ?, ?, 'open', ?)",
                (pid, hypothesis_id, ticker, delta, fill_price, fill_price,
                 delta * fill_price, filled_at),
            )
        return f"opened {pid} qty={delta}"

    pid, old_qty, old_basis = row["id"], float(row["qty"]), float(row["cost_basis"] or 0)
    new_qty = old_qty + delta
    if abs(new_qty) < 1e-9:
        if not dry_run:
            conn.execute(
                "UPDATE positions SET qty=0, state='closed', closed_at=?, current_price=? WHERE id=?",
                (filled_at, fill_price, pid),
            )
        return f"closed {pid}"
    if abs(new_qty) > abs(old_qty):  # add: weighted-average basis
        new_basis = (abs(old_qty) * old_basis + abs(delta) * fill_price) / abs(new_qty)
    else:  # reduce: basis unchanged
        new_basis = old_basis
    if not dry_run:
        conn.execute(
            "UPDATE positions SET qty=?, cost_basis=?, current_price=?, current_value=? WHERE id=?",
            (new_qty, new_basis, fill_price, new_qty * fill_price, pid),
        )
    return f"updated {pid} qty {old_qty}->{new_qty}"


def _apply_broker_truth(conn, order_row, broker, *, dry_run: bool) -> dict:
    oid = order_row["broker_order_id"]
    b_status = str(broker.get("status") or "").lower()
    filled_qty = float(broker.get("filled_qty") or 0)
    avg_price = float(broker.get("filled_avg_price") or 0) or None
    filled_at = broker.get("filled_at") or None
    intent_id = order_row["trade_intent_id"]
    # A closed_unknown order's fill was already materialized as a position by
    # reconcile's placeholder path — restore its price/lineage but never
    # re-apply the qty, or the position doubles.
    materialize = order_row["status"] != "closed_unknown"
    out = {"broker_order_id": oid, "symbol": order_row["symbol"],
           "db_status": order_row["status"], "broker_status": b_status}

    if b_status == order_row["status"]:
        out["action"] = "unchanged"
        return out

    if not dry_run:
        conn.execute(
            "UPDATE orders SET status=?, filled_at=?, avg_fill_price=? WHERE broker_order_id=?",
            (b_status, filled_at, avg_price, oid),
        )

    if b_status == "filled" and filled_qty > 0 and avg_price:
        intent = conn.execute(
            "SELECT id, hypothesis_id, state FROM trade_intents WHERE id=?", (intent_id,)
        ).fetchone()
        if not dry_run:
            conn.execute(
                "UPDATE trade_intents SET state='filled', executed_at=?, actual_price=?, actual_size=? "
                "WHERE id=? AND state IN ('submitted','partial','approved','filled')",
                (filled_at, avg_price, filled_qty, intent_id),
            )
        if materialize:
            delta = _signed_delta(order_row["side"], filled_qty)
            pos_action = _upsert_position(
                conn, ticker=order_row["symbol"],
                hypothesis_id=(intent["hypothesis_id"] if intent else None),
                delta=delta, fill_price=avg_price, filled_at=filled_at or now_iso(),
                dry_run=dry_run,
            )
        else:
            pos_action = "skipped (already materialized by reconcile placeholder path)"
        out.update({"action": "filled", "avg_price": avg_price, "qty": filled_qty,
                    "position": pos_action})
        if not dry_run:
            audit(conn, actor="executor", entity_type="order", entity_id=oid,
                  action="fill_sync", before_state=order_row["status"], after_state="filled",
                  rationale=f"{order_row['side']} {filled_qty} {order_row['symbol']} @ {avg_price}; {pos_action}")
    elif b_status in ("canceled", "cancelled", "expired", "rejected"):
        if not dry_run:
            conn.execute(
                "UPDATE trade_intents SET state=?, blocked_reason=? WHERE id=? AND state='submitted'",
                ("canceled" if b_status in ("canceled", "cancelled", "expired") else "rejected",
                 f"broker order {b_status}", intent_id),
            )
            audit(conn, actor="executor", entity_type="order", entity_id=oid,
                  action="fill_sync", before_state=order_row["status"], after_state=b_status,
                  rationale=f"broker reports {b_status}, no fill")
        out["action"] = b_status
    else:
        # still working (new / partially_filled / pending_*) — record status only
        if b_status == "partially_filled" and not dry_run:
            conn.execute("UPDATE trade_intents SET state='partial' WHERE id=? AND state='submitted'",
                         (intent_id,))
        out["action"] = f"status_updated:{b_status}"
    return out


def sync(conn, *, include_closed_unknown: bool = False, dry_run: bool = False) -> list[dict]:
    q = ("SELECT broker_order_id, trade_intent_id, symbol, side, qty, status "
         "FROM orders WHERE status NOT IN ({})".format(",".join("?" * len(TERMINAL))))
    if not include_closed_unknown:
        q += " AND status != 'closed_unknown'"
    rows = conn.execute(q, list(TERMINAL)).fetchall()
    results = []
    for r in rows:
        try:
            broker = get_order(r["broker_order_id"])
        except ConnectorError as exc:
            results.append({"broker_order_id": r["broker_order_id"], "symbol": r["symbol"],
                            "action": "error", "error": str(exc)[:200]})
            continue
        results.append(_apply_broker_truth(conn, r, broker, dry_run=dry_run))
    return results


def relink_placeholders(conn, *, dry_run: bool = False) -> list[dict]:
    """Re-attach POS-SYNC placeholder positions to the real hypothesis of the
    intent whose filled order opened them (latest filled intent for the ticker
    at or before the position's creation)."""
    out = []
    for p in conn.execute(
        f"SELECT id, ticker, qty, opened_at, hypothesis_id FROM positions "
        f"WHERE id LIKE 'POS-SYNC%' AND state IN {OPEN_POS_STATES}"
    ).fetchall():
        intent = conn.execute(
            "SELECT ti.id, ti.hypothesis_id FROM trade_intents ti "
            "JOIN orders o ON o.trade_intent_id = ti.id "
            "WHERE UPPER(ti.ticker)=? AND ti.hypothesis_id NOT LIKE 'HYP-SYNC%' "
            "AND o.status IN ('filled','closed_unknown') "
            "ORDER BY ti.created_at DESC LIMIT 1",
            (p["ticker"].upper(),),
        ).fetchone()
        if intent is None:
            out.append({"position": p["id"], "ticker": p["ticker"], "action": "no_real_intent_found"})
            continue
        if intent["hypothesis_id"] == p["hypothesis_id"]:
            continue  # lineage already correct — no write, no audit noise
        if not dry_run:
            conn.execute("UPDATE positions SET hypothesis_id=? WHERE id=?",
                         (intent["hypothesis_id"], p["id"]))
            audit(conn, actor="executor", entity_type="position", entity_id=p["id"],
                  action="relink_lineage", before_state=p["hypothesis_id"],
                  after_state=intent["hypothesis_id"],
                  rationale=f"POS-SYNC placeholder re-linked to authoring intent {intent['id']}")
        out.append({"position": p["id"], "ticker": p["ticker"], "action": "relinked",
                    "from": p["hypothesis_id"], "to": intent["hypothesis_id"]})
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--backfill", action="store_true",
                    help="also repair closed_unknown orders and re-link POS-SYNC positions")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    conn = connect()
    report = {"synced": sync(conn, include_closed_unknown=args.backfill, dry_run=args.dry_run)}
    # Relink EVERY run (cheap; targets only open POS-SYNC rows): defense in depth
    # after the 2026-07-15 recurrence — any path that fabricates a placeholder
    # gets its lineage restored on the next pass instead of waiting for a human.
    report["relinked"] = relink_placeholders(conn, dry_run=args.dry_run)
    if not args.dry_run:
        conn.commit()
    changed = [r for r in report["synced"] if r.get("action") not in ("unchanged",)]
    report["summary"] = {"orders_checked": len(report["synced"]), "changed": len(changed),
                         "dry_run": args.dry_run}
    print(json.dumps(report, indent=2))
    errors = [r for r in report["synced"] if r.get("action") == "error"]
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
