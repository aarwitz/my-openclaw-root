#!/usr/bin/env python3
"""Executor · apply_symbol_change.py — repair a ticker rename the data feed moved past.

The sim engine's corporate-action machinery only knows splits and dividends
(sim_corporate_actions CHECK), so a symbol change (e.g. BK -> BNY, 2026) leaves a
position priced off the old symbol's last stale bar: unmarked, unprotected (stops
and falsifiers can never fire), and invisible to P&L. Discovered 2026-07-28 when
BK sat open since 2026-07-08 with NULL pnl_ideal while the real instrument (BNY)
had moved from 150.14 to ~157.

This script renames the position across the canonical book, the sim mirror, and
the hypothesis ticker list, and — when the original fill was itself priced off a
stale close (the engine filled at old-symbol data that had stopped updating) —
re-prices the cost basis at the REAL fill-date close of the new symbol, preserving
the original modeled-slippage ratio, and adjusts sim cash by the difference so the
ledger stays conserved. One audits row records before/after.

Deterministic: every number is an argument or read from the store; nothing is
fetched implicitly except the live mark for the new symbol.

Usage (dry-run by default):
    python3 apply_symbol_change.py --position-id POS-SYNC-a2b65e99a3a5 \
        --sim-position-id pos-desk-13731f19fd0d --book desk \
        --old BK --new BNY --real-fill-close 150.14 [--execute]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path("/home/aaron/.openclaw/state/trading-intel.sqlite")
sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
from connectors.marketdata import latest_trade  # noqa: E402

OPEN_STATES = ("opening", "open", "scaling", "trimming", "closing")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--position-id", required=True)
    p.add_argument("--sim-position-id", required=True)
    p.add_argument("--book", required=True)
    p.add_argument("--old", required=True, help="dead ticker, e.g. BK")
    p.add_argument("--new", required=True, help="successor ticker, e.g. BNY")
    p.add_argument("--real-fill-close", type=float, default=None,
                   help="new-symbol close on the original fill date; when given, "
                        "cost basis is re-priced (stale-fill repair) and sim cash "
                        "adjusted by the difference")
    p.add_argument("--execute", action="store_true")
    args = p.parse_args(argv)
    old, new = args.old.upper(), args.new.upper()

    lt = latest_trade(new)
    if not lt or not lt.get("price"):
        print(json.dumps({"error": f"no live price for {new}; refusing"}), file=sys.stderr)
        return 2
    live = float(lt["price"])

    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    pos = conn.execute(
        f"SELECT * FROM positions WHERE id=? AND UPPER(ticker)=? AND state IN ({','.join('?'*len(OPEN_STATES))})",
        (args.position_id, old, *OPEN_STATES)).fetchone()
    sim = conn.execute(
        "SELECT * FROM sim_positions WHERE id=? AND UPPER(ticker)=? AND book=? AND state='open'",
        (args.sim_position_id, old, args.book)).fetchone()
    if not pos or not sim:
        print(json.dumps({"error": f"open {old} position not found by those ids"}), file=sys.stderr)
        return 2

    qty = float(pos["qty"])
    old_basis = float(pos["cost_basis"])
    new_basis, cash_delta = old_basis, 0.0
    if args.real_fill_close:
        stale_close = float(sim["current_price"])          # the frozen mark the fill priced off
        slip_ratio = old_basis / stale_close if stale_close else 1.0
        new_basis = round(args.real_fill_close * slip_ratio, 4)
        cash_delta = round((new_basis - old_basis) * qty, 6)

    plan = {
        "rename": f"{old} -> {new}", "qty": qty,
        "cost_basis": {"old": old_basis, "new": new_basis},
        "live_mark": live, "new_value": round(live * qty, 2),
        "new_pnl_ideal": round((live - new_basis) * qty, 2),
        "sim_cash_adjustment": -cash_delta,
        "dry_run": not args.execute,
    }
    if not args.execute:
        print(json.dumps(plan, indent=1))
        return 0

    before = {k: pos[k] for k in ("ticker", "cost_basis", "current_price", "current_value")}
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            "UPDATE positions SET ticker=?, cost_basis=?, current_price=?, "
            "current_value=ROUND(?*qty,2), pnl_ideal=ROUND((?-?)*qty,2), "
            "unrealized_pnl_pct=ROUND((?/?-1)*100,4) WHERE id=?",
            (new, new_basis, live, live, live, new_basis, live, new_basis, args.position_id))
        conn.execute(
            "UPDATE sim_positions SET ticker=?, cost_basis=?, current_price=?, "
            "current_value=ROUND(?*qty,2) WHERE id=?",
            (new, new_basis, live, live, args.sim_position_id))
        if cash_delta:
            conn.execute("UPDATE sim_accounts SET cash = cash - ? WHERE book=?",
                         (cash_delta, args.book))
        if pos["hypothesis_id"]:
            h = conn.execute("SELECT tickers FROM hypotheses WHERE id=?",
                             (pos["hypothesis_id"],)).fetchone()
            if h and h["tickers"]:
                fixed = [new if str(t).upper() == old else t for t in json.loads(h["tickers"])]
                conn.execute("UPDATE hypotheses SET tickers=? WHERE id=?",
                             (json.dumps(fixed), pos["hypothesis_id"]))
        aid = "AUDIT-" + _now().replace(":", "").replace("-", "") + f"-{old.lower()}-{new.lower()}-rename"
        conn.execute(
            "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, "
            "before_state, after_state, rationale_concise, experiment_id) "
            "VALUES (?, ?, 'executor', 'position', ?, 'corporate_action_symbol_change', ?, ?, ?, 'world_model_v1')",
            (aid, _now(), args.position_id, json.dumps(before), json.dumps(plan),
             f"{old} renamed to {new}; providers stopped pricing {old}, position was "
             f"unmarked/unprotected since open. Fill re-priced at real fill-date close "
             f"{args.real_fill_close} x original slippage ratio; cash conserved."))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    plan["audit"] = aid
    print(json.dumps(plan, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
