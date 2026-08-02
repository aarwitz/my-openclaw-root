#!/usr/bin/env python3
"""Audit and repair invalid internal-paper cash-yield credits.

The original simulator credited APY/252 every calendar day and treated short
sale proceeds as deployable cash. This migration keeps the original values on
each event, writes one immutable repair record, rebases affected equity/cash
history, and recomputes daily return attribution. It is dry-run by default.

Usage:
  python3 repair_cash_yield_history.py
  python3 repair_cash_yield_history.py --apply
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import trading_policy  # noqa: E402
from _db import audit, now_iso  # noqa: E402
from connectors.marketdata import is_trading_day  # noqa: E402
import symbol_lifecycle  # noqa: E402

DB = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))
REPAIR_ID = "cash-yield-calendar-collateral-v2"


def _parse_ts(value: str) -> float:
    return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()


def _canonical(symbol: str, as_of: str | None = None) -> str:
    return symbol_lifecycle.canonical_symbol(str(symbol).upper(), as_of=as_of)


def _initial_positions(
    conn: sqlite3.Connection,
    orders: list[sqlite3.Row],
    book: str,
) -> dict[str, dict[str, float]]:
    """Infer imported seed inventory from final inventory minus every fill.

    The simulator cutover imported positions without synthetic buy orders. A
    fill-only replay therefore mistakes later sell-to-close fills for shorts.
    Conservation gives the exact seed quantity: final_qty - signed_fill_sum.
    """
    final: dict[str, float] = {}
    basis: dict[str, float] = {}
    for row in conn.execute(
        "SELECT ticker,qty,cost_basis FROM sim_positions "
        "WHERE book=? AND state='open'",
        (book,),
    ).fetchall():
        symbol = _canonical(row["ticker"])
        final[symbol] = final.get(symbol, 0.0) + float(row["qty"] or 0.0)
        basis.setdefault(symbol, float(row["cost_basis"] or 0.0))
    signed_fills: dict[str, float] = {}
    for order in orders:
        symbol = _canonical(order["symbol"], str(order["filled_at"])[:10])
        signed = float(order["qty"]) if str(order["side"]).lower() == "buy" else -float(order["qty"])
        signed_fills[symbol] = signed_fills.get(symbol, 0.0) + signed
    out = {}
    for symbol in set(final) | set(signed_fills):
        qty = final.get(symbol, 0.0) - signed_fills.get(symbol, 0.0)
        if abs(qty) >= 1e-9:
            out[symbol] = {"qty": qty, "cost_basis": basis.get(symbol, 0.0)}
    return out


def _short_collateral_at(
    orders: list[sqlite3.Row],
    cutoff: str,
    initial_positions: dict[str, dict[str, float]],
) -> float:
    """Replay signed fills through cutoff and value shorts at their fill basis."""
    positions = {symbol: dict(position) for symbol, position in initial_positions.items()}
    for order in orders:
        if str(order["filled_at"]) > cutoff:
            break
        symbol = _canonical(order["symbol"], str(order["filled_at"])[:10])
        side = str(order["side"]).lower()
        signed = float(order["qty"]) if side == "buy" else -float(order["qty"])
        price = float(order["fill_price"])
        current = positions.get(symbol, {"qty": 0.0, "cost_basis": 0.0})
        old_qty = float(current["qty"])
        new_qty = old_qty + signed
        if abs(new_qty) < 1e-9:
            positions.pop(symbol, None)
            continue
        if old_qty == 0 or old_qty * signed > 0:
            total_cost = float(current["cost_basis"]) * abs(old_qty) + price * abs(signed)
            basis = total_cost / abs(new_qty)
        elif old_qty * new_qty < 0:
            # A historical fill that crossed through flat starts a new position
            # at that fill; current runtime policy forbids this short-opening path.
            basis = price
        else:
            basis = float(current["cost_basis"])
        positions[symbol] = {"qty": new_qty, "cost_basis": basis}
    return trading_policy.short_collateral(positions.values())


def plan_corrections(conn: sqlite3.Connection, book: str = "desk") -> list[dict]:
    events = conn.execute(
        "SELECT id,book,as_of_date,annual_yield,cash_start,credit,applied_at,"
        "original_cash_start,original_credit FROM sim_cash_yield_events "
        "WHERE book=? ORDER BY applied_at,id",
        (book,),
    ).fetchall()
    orders = conn.execute(
        "SELECT symbol,side,qty,fill_price,filled_at FROM sim_orders "
        "WHERE book=? ORDER BY filled_at,order_id",
        (book,),
    ).fetchall()
    initial_positions = _initial_positions(conn, orders, book)
    cumulative_target = 0.0
    plan: list[dict] = []
    for event in events:
        original_cash = float(
            event["original_cash_start"]
            if event["original_cash_start"] is not None
            else event["cash_start"]
        )
        original_credit = float(
            event["original_credit"]
            if event["original_credit"] is not None
            else event["credit"]
        )
        corrected_gross_cash = max(0.0, original_cash - cumulative_target)
        collateral = _short_collateral_at(
            orders,
            str(event["applied_at"]),
            initial_positions,
        )
        deployable_cash = trading_policy.deployable_cash(
            corrected_gross_cash,
            collateral,
        )
        trading_day = is_trading_day(str(event["as_of_date"]))
        corrected_credit = (
            round(deployable_cash * float(event["annual_yield"]) / 252.0, 6)
            if trading_day
            else 0.0
        )
        target_correction = round(original_credit - corrected_credit, 6)
        already_corrected = round(original_credit - float(event["credit"]), 6)
        correction_delta = round(target_correction - already_corrected, 6)
        cumulative_target = round(cumulative_target + target_correction, 6)
        plan.append(
            {
                "id": event["id"],
                "book": event["book"],
                "as_of_date": event["as_of_date"],
                "applied_at": event["applied_at"],
                "trading_day": trading_day,
                "original_cash_start": original_cash,
                "corrected_cash_start": deployable_cash,
                "short_collateral": collateral,
                "original_credit": original_credit,
                "corrected_credit": corrected_credit,
                "cash_correction": correction_delta,
                "target_cash_correction": target_correction,
                "previously_applied_correction": already_corrected,
                "cumulative_cash_correction": cumulative_target,
            }
        )
    return plan


def _offset_for_date(plan: list[dict], as_of_date: str, *, inclusive: bool = True) -> float:
    return sum(
        float(item["cash_correction"])
        for item in plan
        if item["as_of_date"] <= as_of_date
        if inclusive or item["as_of_date"] < as_of_date
    )


def _rebase_history(conn: sqlite3.Connection, book: str, plan: list[dict]) -> dict:
    daily = 0
    for row in conn.execute(
        "SELECT date,equity,cash FROM book_equity WHERE book=? ORDER BY date",
        (book,),
    ).fetchall():
        offset = _offset_for_date(plan, str(row["date"]))
        if abs(offset) < 1e-12:
            continue
        conn.execute(
            "UPDATE book_equity SET equity=?,cash=? WHERE book=? AND date=?",
            (float(row["equity"]) - offset, float(row["cash"]) - offset, book, row["date"]),
        )
        daily += 1

    intraday = 0
    event_ts = [(_parse_ts(item["applied_at"]) * 1000.0, item["cash_correction"]) for item in plan]
    for row in conn.execute(
        "SELECT ts,equity FROM book_equity_intraday WHERE book=? ORDER BY ts",
        (book,),
    ).fetchall():
        offset = sum(float(delta) for ts, delta in event_ts if float(row["ts"]) >= ts)
        if abs(offset) < 1e-12:
            continue
        conn.execute(
            "UPDATE book_equity_intraday SET equity=? WHERE book=? AND ts=?",
            (float(row["equity"]) - offset, book, row["ts"]),
        )
        intraday += 1

    snapshots = 0
    for row in conn.execute(
        "SELECT id,captured_at,equity,last_equity,day_pl,cash,buying_power "
        "FROM portfolio_snapshots WHERE source='internal_paper_history' "
        "ORDER BY captured_at"
    ).fetchall():
        day = str(row["captured_at"])[:10]
        current_offset = _offset_for_date(plan, day)
        prior_offset = _offset_for_date(plan, day, inclusive=False)
        if abs(current_offset) < 1e-12 and abs(prior_offset) < 1e-12:
            continue
        equity = float(row["equity"]) - current_offset
        last_equity = (
            None
            if row["last_equity"] is None
            else float(row["last_equity"]) - prior_offset
        )
        day_pl = None if last_equity is None else round(equity - last_equity, 2)
        cash = None if row["cash"] is None else float(row["cash"]) - current_offset
        buying_power = (
            None
            if row["buying_power"] is None
            else max(0.0, float(row["buying_power"]) - current_offset)
        )
        conn.execute(
            "UPDATE portfolio_snapshots SET equity=?,last_equity=?,day_pl=?,cash=?,buying_power=? "
            "WHERE id=?",
            (equity, last_equity, day_pl, cash, buying_power, row["id"]),
        )
        snapshots += 1

    attribution = 0
    equity_rows = conn.execute(
        "SELECT date,equity FROM book_equity WHERE book=? ORDER BY date",
        (book,),
    ).fetchall()
    for index, row in enumerate(equity_rows):
        exists = conn.execute(
            "SELECT 1 FROM book_return_attribution WHERE book=? AND date=?",
            (book, row["date"]),
        ).fetchone()
        if not exists:
            continue
        equity = float(row["equity"])
        last_equity = None if index == 0 else float(equity_rows[index - 1]["equity"])
        cash_yield = conn.execute(
            "SELECT COALESCE(SUM(credit),0) FROM sim_cash_yield_events "
            "WHERE book=? AND as_of_date=?",
            (book, row["date"]),
        ).fetchone()[0]
        cash_yield = float(cash_yield or 0.0)
        total_pl = 0.0 if last_equity in (None, 0.0) else equity - last_equity
        trading_pl = total_pl - cash_yield
        trading_ret = None if last_equity in (None, 0.0) else trading_pl / last_equity * 100.0
        cash_ret = None if last_equity in (None, 0.0) else cash_yield / last_equity * 100.0
        total_ret = None if last_equity in (None, 0.0) else total_pl / last_equity * 100.0
        conn.execute(
            "UPDATE book_return_attribution SET equity=?,last_equity=?,trading_pl=?,"
            "cash_yield_pl=?,total_pl=?,trading_return_pct=?,cash_yield_return_pct=?,"
            "total_return_pct=?,created_at=? WHERE book=? AND date=?",
            (
                equity,
                last_equity,
                trading_pl,
                cash_yield,
                total_pl,
                trading_ret,
                cash_ret,
                total_ret,
                now_iso(),
                book,
                row["date"],
            ),
        )
        attribution += 1
    return {
        "book_equity_rows": daily,
        "intraday_rows": intraday,
        "portfolio_snapshots": snapshots,
        "return_attribution_rows": attribution,
    }


def apply_repair(conn: sqlite3.Connection, book: str = "desk") -> dict:
    prior = conn.execute(
        "SELECT cash_delta,details_json FROM sim_ledger_repairs WHERE id=?",
        (f"{REPAIR_ID}-{book}",),
    ).fetchone()
    if prior:
        return {
            "applied": False,
            "already_applied": True,
            "cash_delta": float(prior["cash_delta"]),
            "details": json.loads(prior["details_json"]),
        }
    plan = plan_corrections(conn, book)
    changed = [item for item in plan if abs(float(item["cash_correction"])) >= 0.0000005]
    total = round(sum(float(item["cash_correction"]) for item in changed), 6)
    applied_at = now_iso()
    conn.execute("BEGIN IMMEDIATE")
    try:
        for item in changed:
            reason = (
                "non_trading_day_credit"
                if not item["trading_day"]
                else "restricted_short_collateral"
            )
            conn.execute(
                "UPDATE sim_cash_yield_events SET original_cash_start=?,original_credit=?,"
                "cash_start=?,credit=?,corrected_at=?,correction_reason=? WHERE id=?",
                (
                    item["original_cash_start"],
                    item["original_credit"],
                    item["corrected_cash_start"],
                    item["corrected_credit"],
                    applied_at,
                    reason,
                    item["id"],
                ),
            )
        conn.execute(
            "UPDATE sim_accounts SET cash=cash-? WHERE book=?",
            (total, book),
        )
        rebase = _rebase_history(conn, book, changed)
        details = {
            "events_corrected": len(changed),
            "cash_overstatement_removed": total,
            "event_corrections": changed,
            "history_rebased": rebase,
        }
        conn.execute(
            "INSERT INTO sim_ledger_repairs(id,applied_at,kind,book,cash_delta,details_json) "
            "VALUES(?,?,?,?,?,?)",
            (
                f"{REPAIR_ID}-{book}",
                applied_at,
                "cash_yield_calendar_and_short_collateral_v2",
                book,
                -total,
                json.dumps(details, sort_keys=True),
            ),
        )
        audit(
            conn,
            actor="developer",
            entity_type="sim_account",
            entity_id=book,
            action="repair_cash_yield_history",
            rationale=(
                f"removed ${total:.6f} invalid cash yield across {len(changed)} events; "
                "weekends use no /252 credit and short proceeds are restricted collateral"
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"applied": True, "already_applied": False, "cash_delta": -total, "details": details}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--book", default="desk")
    parser.add_argument("--db", type=Path, default=DB)
    args = parser.parse_args()
    conn = sqlite3.connect(args.db, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    if args.apply:
        result = apply_repair(conn, args.book)
    else:
        prior = conn.execute(
            "SELECT 1 FROM sim_ledger_repairs WHERE id=?",
            (f"{REPAIR_ID}-{args.book}",),
        ).fetchone()
        plan = [] if prior else plan_corrections(conn, args.book)
        result = {
            "applied": False,
            "already_applied": bool(prior),
            "book": args.book,
            "events": plan,
            "cash_overstatement": round(
                sum(float(item["cash_correction"]) for item in plan), 6
            ),
        }
    conn.close()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
