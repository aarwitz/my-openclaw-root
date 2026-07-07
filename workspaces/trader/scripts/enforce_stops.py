#!/usr/bin/env python3
"""Trader · enforce_stops.py — deterministic stop-rule ENFORCEMENT (D53).

Every intent the desk authors declares `stop_rule` ("-8% from entry"), but
nothing ever enforced it: on 2026-07-07 the book held ORCL -22.6%, CRM -9.9%,
CEG -8.2% against that stated stop while continuing to open new names. A rule
that is written but not executed is a lie the desk tells itself.

Each pass: for every OPEN long position whose mark is <= entry basis × (1 - STOP_PCT),
author ONE exit intent (state=proposed, action=exit, full size). Shorts: mark >=
basis × (1 + STOP_PCT). The exit then flows the normal non-bypassable path —
gate_evaluator (exits are sanity-gated only, D47) → risk gate (auto-approves
risk-reducing) → executor. This script only AUTHORS; it never touches the broker.

Idempotent: skips tickers that already have an open exit intent.
STOP_PCT from env TRADER_STOP_PCT (default 0.08, matching the authored rule).

  python3 enforce_stops.py [--dry-run]
"""

from __future__ import annotations

import sys
sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper

require_wrapper()

import argparse
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
sys.path.insert(0, "/home/aaron/.openclaw/workspaces/executor/scripts")
from connectors.alpaca import latest_trade  # noqa: E402  (market data)

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))
STOP_PCT = float(os.environ.get("TRADER_STOP_PCT", "0.08"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    positions = conn.execute(
        "SELECT id, hypothesis_id, ticker, qty, cost_basis FROM positions "
        "WHERE state IN ('opening','open','scaling') AND qty != 0").fetchall()

    open_exits = {r[0].upper() for r in conn.execute(
        "SELECT DISTINCT ticker FROM trade_intents WHERE action IN ('exit','trim') "
        "AND state IN ('proposed','critic_review','risk_review','approved','submitted','partial')")}

    results = []
    for p in positions:
        tick = p["ticker"].upper()
        basis = float(p["cost_basis"] or 0)
        qty = float(p["qty"])
        if basis <= 0 or tick in open_exits:
            continue
        lt = latest_trade(tick)
        if not lt or not lt.get("price"):
            continue  # no quote — check again next pass; never act blind
        px = float(lt["price"])
        breach = (px <= basis * (1 - STOP_PCT)) if qty > 0 else (px >= basis * (1 + STOP_PCT))
        if not breach:
            continue
        dd = (px / basis - 1) * 100
        results.append({"ticker": tick, "basis": basis, "mark": px, "dd_pct": round(dd, 1),
                        "qty": qty})
        if a.dry_run:
            continue
        iid = f"ti-stop-{uuid.uuid4().hex[:20]}"
        # expression_candidate_id is NOT NULL — inherit from the lineage's most
        # recent intent on this ticker (the exit expresses the same candidate).
        ec = conn.execute(
            "SELECT expression_candidate_id FROM trade_intents WHERE UPPER(ticker)=? "
            "AND expression_candidate_id IS NOT NULL ORDER BY created_at DESC LIMIT 1",
            (tick,)).fetchone()
        if not ec:
            results[-1]["skipped"] = "no expression candidate lineage"
            continue
        conn.execute(
            "INSERT INTO trade_intents (id, hypothesis_id, expression_candidate_id, created_by, "
            "created_at, action, tranche_type, ticker, vehicle, size, entry_price_target, "
            "stop_rule, time_horizon, triggered_by, modeled_slippage_bps, state, direction) "
            "VALUES (?, ?, ?, 'trader', ?, 'exit', NULL, ?, 'direct_equity', ?, ?, ?, "
            "'position_1_4w', 'stop_rule_enforcer_v1', 8.0, 'proposed', ?)",
            (iid, p["hypothesis_id"], ec[0], _now_iso(), tick, abs(qty), px,
             f"STOP HIT: {dd:+.1f}% vs -{STOP_PCT*100:.0f}% rule",
             "long" if qty > 0 else "short"))
        aid = "AUDIT-" + _now_iso().replace(":", "").replace("-", "") + "-" + iid[:24]
        conn.execute(
            "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, "
            "before_state, after_state, rationale_concise) VALUES (?, ?, 'trader', "
            "'trade_intent', ?, 'author_stop_exit', NULL, 'proposed', ?)",
            (aid, _now_iso(), iid,
             f"stop-rule enforcement: {tick} {dd:+.1f}% from basis {basis:.2f} "
             f"(mark {px:.2f}) breaches the -{STOP_PCT*100:.0f}% stop every intent declares. "
             "Exit authored; flows the normal gate path."))
        conn.commit()
    print(json.dumps({"stop_breaches": results, "authored": 0 if a.dry_run else len(results),
                      "dry_run": a.dry_run, "stop_pct": STOP_PCT}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
