#!/usr/bin/env python3
"""Exit exposure that cannot prove the current opening-decision contract.

Holding a position is a continuing risk decision. A desk that blocks identical
new risk while silently carrying pre-cutover inventory does not have a clean
forward experiment. This enforcer therefore authors full, risk-reducing exits
for legacy or invalid-lineage positions. It never reconstructs missing history
and never touches a position with complete modern lineage.

The script only authors while the exchange is open, using a fresh bulk quote.
Every exit still flows through gate_evaluator -> Risk -> Executor. Dry-run is
read-only and may be used while the exchange is closed.
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
from typing import Callable

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/developer/scripts")
sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
from inventory_lineage import build_report  # noqa: E402
from connectors.marketdata import latest_trades, market_clock  # noqa: E402

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))
OPEN_EXIT_STATES = (
    "proposed", "critic_review", "risk_review", "approved", "submitted", "partial",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def enforce(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = False,
    clock: dict | None = None,
    quote_loader: Callable[[list[str]], dict] = latest_trades,
) -> dict:
    conn.row_factory = sqlite3.Row
    lineage = build_report(conn)
    candidates = [
        row for row in lineage["positions"] if row["status"] != "modern_lineage"
    ]
    tickers = [str(row["ticker"]).upper() for row in candidates]
    if len(tickers) != len(set(tickers)):
        raise RuntimeError("duplicate open position tickers prevent unambiguous lineage exits")

    placeholders = ",".join("?" for _ in OPEN_EXIT_STATES)
    open_exits = {
        str(row[0]).upper()
        for row in conn.execute(
            f"SELECT DISTINCT ticker FROM trade_intents WHERE action IN ('exit','trim') "
            f"AND state IN ({placeholders})",
            OPEN_EXIT_STATES,
        )
    }
    pending = [row for row in candidates if str(row["ticker"]).upper() not in open_exits]
    session = market_clock() if clock is None else clock
    is_open = bool(session.get("is_open"))
    quotes = (
        quote_loader([str(row["ticker"]).upper() for row in pending])
        if pending and (dry_run or is_open)
        else {}
    )

    results = []
    authored = 0
    for row in candidates:
        ticker = str(row["ticker"]).upper()
        result = {
            "ticker": ticker,
            "position_id": row["position_id"],
            "status": row["status"],
            "qty": row["qty"],
            "gross_value": row["gross_value"],
            "gaps": row["gaps"],
        }
        if ticker in open_exits:
            result["disposition"] = "existing_exit"
            results.append(result)
            continue
        if not dry_run and not is_open:
            result["disposition"] = "deferred_market_closed"
            results.append(result)
            continue
        quote = quotes.get(ticker) or {}
        price = float(quote.get("price") or 0.0)
        if price <= 0:
            result["disposition"] = "missing_fresh_quote"
            results.append(result)
            continue
        ec = conn.execute(
            "SELECT expression_candidate_id FROM trade_intents "
            "WHERE hypothesis_id=? AND expression_candidate_id IS NOT NULL "
            "ORDER BY created_at DESC LIMIT 1",
            (row["hypothesis_id"],),
        ).fetchone()
        if not ec:
            result["disposition"] = "missing_expression_lineage"
            results.append(result)
            continue
        result["mark"] = price
        if dry_run:
            result["disposition"] = "would_author_exit"
            results.append(result)
            continue

        now = _now_iso()
        intent_id = f"ti-lineage-{uuid.uuid4().hex[:20]}"
        rationale = (
            f"inventory-lineage quarantine: {ticker} {row['status']} lacks trusted "
            f"opening provenance ({','.join(row['gaps']) or 'post-cutover add violation'}); "
            "holding unvalidated exposure is not exempt from the current gate contract"
        )
        conn.execute(
            "INSERT INTO trade_intents (id,hypothesis_id,expression_candidate_id,created_by,"
            "created_at,action,tranche_type,ticker,vehicle,size,entry_price_target,stop_rule,"
            "time_horizon,triggered_by,modeled_slippage_bps,state,direction) "
            "VALUES (?,?,?,'trader',?,'exit',NULL,?,'direct_equity',?,?,?,"
            "'position_1_4w','inventory_lineage_quarantine_v1',8.0,'proposed',?)",
            (
                intent_id, row["hypothesis_id"], ec[0], now, ticker,
                abs(float(row["qty"])), price, rationale[:500],
                "long" if float(row["qty"]) > 0 else "short",
            ),
        )
        conn.execute(
            "INSERT INTO audits (id,timestamp,actor,entity_type,entity_id,action,"
            "before_state,after_state,rationale_concise) "
            "VALUES (?,?,'trader','trade_intent',?,'author_lineage_exit',NULL,'proposed',?)",
            (
                "AUDIT-" + now.replace(":", "").replace("-", "") + "-" + uuid.uuid4().hex[:8],
                now, intent_id, rationale[:500],
            ),
        )
        authored += 1
        result.update({"disposition": "authored_exit", "intent_id": intent_id})
        results.append(result)

    if authored:
        conn.commit()
    unresolved = [
        row for row in results
        if row["disposition"] in ("missing_fresh_quote", "missing_expression_lineage")
    ]
    return {
        "market_open": is_open,
        "dry_run": dry_run,
        "legacy_or_invalid_positions": len(candidates),
        "gross_value": round(sum(float(row["gross_value"]) for row in candidates), 2),
        "existing_exits": sum(row["disposition"] == "existing_exit" for row in results),
        "deferred_market_closed": sum(
            row["disposition"] == "deferred_market_closed" for row in results
        ),
        "would_author": sum(row["disposition"] == "would_author_exit" for row in results),
        "authored": authored,
        "unresolved": len(unresolved),
        "results": results,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    conn = sqlite3.connect(DB_PATH)
    report = enforce(conn, dry_run=args.dry_run)
    print(json.dumps(report, indent=2))
    return 1 if report["unresolved"] and report["market_open"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
