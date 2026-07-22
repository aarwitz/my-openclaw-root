#!/usr/bin/env python3
"""Trader · enforce_falsifiers.py — the falsifier tripwire, finally wired (D57).

falsifier_signals existed since June but was write-only: no script ever read it
(2026-07-09 audit), and writes stopped Jun 23. This makes the tripwire live:
any OPEN position whose hypothesis has a falsifier with current_status='broken'
gets a full exit intent through the normal gate path. Status flips are the
researcher's job (daily learning pass duty); this script only ACTS on them.

  python3 enforce_falsifiers.py [--dry-run]
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

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
from connectors.marketdata import latest_trade  # noqa: E402

DB = os.path.expanduser("~/.openclaw/state/trading-intel.sqlite")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT DISTINCT p.id AS pos_id, p.hypothesis_id, p.ticker, p.qty, f.id AS fals_id, f.condition "
        "FROM positions p "
        "JOIN falsifier_signals f ON f.hypothesis_id = p.hypothesis_id "
        "WHERE p.state IN ('opening','open','scaling') AND p.qty != 0 "
        "AND f.current_status = 'broken'").fetchall()

    open_exits = {r[0].upper() for r in conn.execute(
        "SELECT DISTINCT ticker FROM trade_intents WHERE action='exit' "
        "AND state IN ('proposed','critic_review','risk_review','approved','submitted','partial')")}

    fired = []
    for r in rows:
        tick = r["ticker"].upper()
        if tick in open_exits:
            continue
        fired.append({"ticker": tick, "falsifier": r["fals_id"], "condition": (r["condition"] or "")[:80]})
        if a.dry_run:
            continue
        ec = conn.execute(
            "SELECT expression_candidate_id FROM trade_intents WHERE UPPER(ticker)=? "
            "AND expression_candidate_id IS NOT NULL ORDER BY created_at DESC LIMIT 1", (tick,)).fetchone()
        if not ec:
            fired[-1]["skipped"] = "no lineage"
            continue
        lt = latest_trade(tick)
        px = float(lt["price"]) if lt and lt.get("price") else None
        iid = f"ti-fals-{uuid.uuid4().hex[:20]}"
        conn.execute(
            "INSERT INTO trade_intents (id, hypothesis_id, expression_candidate_id, created_by, "
            "created_at, action, tranche_type, ticker, vehicle, size, entry_price_target, stop_rule, "
            "time_horizon, triggered_by, modeled_slippage_bps, state, direction) "
            "VALUES (?, ?, ?, 'trader', ?, 'exit', NULL, ?, 'direct_equity', ?, ?, ?, "
            "'position_1_4w', 'falsifier_enforcer_v1', 8.0, 'proposed', 'long')",
            (iid, r["hypothesis_id"], ec[0], _now(), tick, abs(float(r["qty"])), px,
             f"FALSIFIER BROKEN: {r['fals_id']}"))
        aid = "AUDIT-" + _now().replace(":", "").replace("-", "") + "-" + uuid.uuid4().hex[:8]
        conn.execute(
            "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, "
            "before_state, after_state, rationale_concise) VALUES (?, ?, 'trader', 'trade_intent', ?, "
            "'author_falsifier_exit', NULL, 'proposed', ?)",
            (aid, _now(), iid,
             f"falsifier {r['fals_id']} broken on {r['hypothesis_id']} ({tick}): thesis tripwire fired; exit authored"))
        conn.commit()
    print(json.dumps({"broken_falsifiers": fired, "authored": 0 if a.dry_run else len([f for f in fired if 'skipped' not in f]), "dry_run": a.dry_run}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
