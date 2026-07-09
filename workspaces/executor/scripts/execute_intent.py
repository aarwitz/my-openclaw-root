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
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Connector path
sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _db import audit, connect, now_iso  # noqa: E402

from connectors.alpaca import ConnectorError, daily_bars, latest_trade, market_clock  # noqa: E402  (market DATA)
from broker import place_order  # noqa: E402  (trading state -> adapter, D52)

# ---- freshness gate: never execute on stale reasoning or a price that moved since the signal priced it.
# Tunable via env; defaults are conservative for a swing/position desk (cadence ~minutes-to-hours).
MAX_INTENT_AGE_MIN = float(os.environ.get("EXEC_MAX_AGE_MIN", "180"))      # reject reasoning older than 3h
MAX_PRICE_DRIFT_PCT = float(os.environ.get("EXEC_MAX_DRIFT_PCT", "0.04"))  # BASE drift tolerance (calm names)
DRIFT_VOL_MULT = float(os.environ.get("EXEC_DRIFT_VOL_MULT", "2.5"))       # tol = max(base, mult * daily_vol)
MAX_DRIFT_CAP = float(os.environ.get("EXEC_MAX_DRIFT_CAP", "0.15"))        # never tolerate >15% drift, however vol
LIMIT_BUFFER = float(os.environ.get("EXEC_LIMIT_BUFFER", "0.005"))         # marketable-limit cushion vs live price


def _age_minutes(created_iso):
    if not created_iso:
        return None
    try:
        c = datetime.fromisoformat(str(created_iso).replace("Z", "+00:00"))
        if c.tzinfo is None:
            c = c.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - c).total_seconds() / 60.0
    except Exception:
        return None


def _recent_daily_vol(ticker):
    """Realized daily volatility from recent (cached) bars, used to scale the drift tolerance so high-vol
    names (lithium/uranium/space) aren't over-rejected while calm names stay tight. Returns stdev or None."""
    try:
        bars = daily_bars(ticker, days=25)
        closes = [float(b["c"]) for b in bars[-21:] if b.get("c")]
        if len(closes) < 10:
            return None
        rets = [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
        m = sum(rets) / len(rets)
        return (sum((r - m) ** 2 for r in rets) / len(rets)) ** 0.5
    except Exception:
        return None


def _select_intents(conn, intent_id: str | None):
    if intent_id:
        sql = ("SELECT id, hypothesis_id, ticker, vehicle, action, size, "
               "entry_price_target, created_at, state, direction FROM trade_intents WHERE id=?")
        rows = conn.execute(sql, (intent_id,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, hypothesis_id, ticker, vehicle, action, size, "
            "entry_price_target, created_at, state, direction FROM trade_intents "
            "WHERE state='approved' ORDER BY created_at ASC"
        ).fetchall()
    return rows


def _side_from_action(action: str, direction: str = "long", held_qty: float | None = None) -> str:
    # Direction-aware (migration 0013):
    #   open/add  long  → buy            open/add  short → sell (sell-to-open)
    #   trim/exit of a long → sell       trim/exit of a short → buy (buy-to-cover)
    # For exits the ACTUAL held position sign wins over the intent's direction
    # column (self-correcting if the two ever disagree).
    d = (direction or "long").lower()
    if action in ("open", "add"):
        return "sell" if d == "short" else "buy"
    if action in ("trim", "exit"):
        if held_qty is not None and held_qty != 0:
            return "buy" if held_qty < 0 else "sell"
        return "buy" if d == "short" else "sell"
    if action == "rotate":
        return "buy"  # treat rotate as a buy leg; sell leg should be a separate intent
    raise ValueError(f"unknown action: {action}")


def _held_qty(conn, ticker: str) -> float | None:
    """Signed net quantity currently held per the positions table (None if flat/unknown)."""
    try:
        row = conn.execute(
            "SELECT SUM(qty) FROM positions WHERE UPPER(ticker)=? "
            "AND state IN ('opening','open','scaling','trimming','closing')",
            ((ticker or "").upper(),)).fetchone()
        return float(row[0]) if row and row[0] is not None else None
    except Exception:
        return None


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

    direction = (intent_row["direction"] if "direction" in intent_row.keys() else None) or "long"
    side = _side_from_action(intent_row["action"], direction,
                             _held_qty(conn, intent_row["ticker"]))
    qty = _qty_from_size(intent_row["size"], intent_row["vehicle"])

    # ---- FRESHNESS GATE: don't act on stale reasoning or a price that moved since the signal priced it ----
    try:
        live = latest_trade(intent_row["ticker"])      # live last-trade (None = halted / feed-down / illiquid)
    except Exception:
        live = None                                    # connector failure -> treat as no quote (fail-closed)
    entry_ref = intent_row["entry_price_target"]
    reasons, drift = [], None
    age_min = _age_minutes(intent_row["created_at"])
    if age_min is not None and age_min > MAX_INTENT_AGE_MIN:
        reasons.append(f"stale_age={age_min:.0f}m>{MAX_INTENT_AGE_MIN:.0f}m")
    # FAIL-CLOSED: no live quote (halted symbol / data feed down / illiquid) and not stale-by-age ->
    # do NOT blind-market-order. Skip this pass, leave 'approved' to retry when a quote returns; the age
    # gate eventually rejects it if it stays unquotable.
    if live is None and not reasons:
        if not dry_run:
            audit(conn, actor="executor", entity_type="trade_intent", entity_id=intent_id,
                  action="skip", before_state="approved", after_state="approved",
                  rationale="freshness gate: no live quote (halted/feed-down/illiquid) — skip, retry next pass")
            conn.commit()
        return {"intent_id": intent_id, "submitted": False, "skipped_no_quote": True}
    if live and entry_ref:
        drift = (live["price"] - float(entry_ref)) / float(entry_ref)
        dvol = _recent_daily_vol(intent_row["ticker"])
        drift_tol = min(MAX_DRIFT_CAP, max(MAX_PRICE_DRIFT_PCT, DRIFT_VOL_MULT * dvol)) if dvol else MAX_PRICE_DRIFT_PCT
        if abs(drift) > drift_tol:
            reasons.append(f"price_drift={drift*100:+.1f}%>tol{drift_tol*100:.0f}%"
                           + (f" (dvol={dvol*100:.1f}%)" if dvol else "")
                           + f" (ref {entry_ref}->live {live['price']})")
    if reasons:
        if not dry_run:
            conn.execute("UPDATE trade_intents SET state='rejected', blocked_reason=? WHERE id=?",
                         (("stale: " + "; ".join(reasons))[:300], intent_id))
            audit(conn, actor="executor", entity_type="trade_intent", entity_id=intent_id,
                  action="reject", before_state="approved", after_state="rejected",
                  rationale=("freshness gate rejected (re-derive next pass) — " + "; ".join(reasons))[:380])
            conn.commit()
        return {"intent_id": intent_id, "submitted": False, "rejected_stale": True, "reasons": reasons}

    # protective MARKETABLE LIMIT bounded near the live price (so a stale/gapped quote can't fill far off)
    limit_price = round(live["price"] * ((1 + LIMIT_BUFFER) if side == "buy" else (1 - LIMIT_BUFFER)), 2) if live else None
    order_type = "limit" if limit_price else "market"

    if dry_run:
        return {"intent_id": intent_id, "dry_run": True,
                "would_submit": {"symbol": intent_row["ticker"], "qty": qty, "side": side,
                                 "order_type": order_type, "limit_price": limit_price,
                                 "live_price": (live or {}).get("price"),
                                 "drift_pct": round(drift * 100, 2) if drift is not None else None,
                                 "age_min": round(age_min, 1) if age_min is not None else None}}

    client_order_id = f"oc-{intent_id}-{now_iso().replace(':','').replace('-','')}"
    try:
        resp = place_order(
            symbol=intent_row["ticker"],
            qty=qty,
            side=side,
            order_type=order_type,
            time_in_force="day",
            client_order_id=client_order_id,
            limit_price=limit_price,
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
        (broker_order_id, intent_id, intent_row["ticker"], side, qty, order_type,
         limit_price, resp.get("status") or "submitted", submitted_at,
         # sim fills are instant — persist fill truth here or analytics read NULLs
         resp.get("filled_at"), _f(resp.get("filled_avg_price")), None),
    )
    if (resp.get("status") == "filled") and resp.get("filled_avg_price") is not None:
        conn.execute(
            "UPDATE trade_intents SET actual_price=?, actual_size=?, executed_at=? WHERE id=?",
            (_f(resp.get("filled_avg_price")), qty, resp.get("filled_at"), intent_id))
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


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--intent-id", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max", type=int, default=10)
    args = p.parse_args(argv)

    conn = connect()
    rows = _select_intents(conn, args.intent_id)

    # Market-calendar gate (fail-closed, like everything on the order path):
    # never queue orders on a closed market — they'd execute at the next open's
    # gap on reasoning that is hours-to-days stale (2026-07-03 FDX incident:
    # order submitted 07:18 ET on the Jul-4-observed holiday, queued into a
    # 3-day weekend). Approved intents are left untouched; the staleness gate
    # purges them and the desk re-authors fresh next session. An unreadable
    # clock is treated as closed.
    if rows and not args.dry_run:
        try:
            clock = market_clock()
            market_open = bool(clock.get("is_open"))
            next_open = clock.get("next_open")
        except Exception as exc:
            market_open, next_open = False, f"clock unreadable: {exc}"
        if not market_open:
            print(json.dumps({"processed": 0, "deferred": len(rows),
                              "reason": "market closed — not submitting orders",
                              "next_open": str(next_open)}, indent=2))
            return 0

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
