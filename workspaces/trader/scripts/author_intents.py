#!/usr/bin/env python3
"""Trader · author_intents.py

Deterministic intent authoring: for every hypothesis in state=ready that has
no open intent or position on its primary ticker, mint exactly one
trade_intents row in state='proposed' so the gate_evaluator + executor can
take it the rest of the way to the broker.

Sizing rule (conservative, paper-account):
  - notional = clamp(SIZE_PCT_OF_EQUITY * account.equity, NOTIONAL_FLOOR, NOTIONAL_CEILING)
  - qty      = floor(notional / last_close)
  - skip if qty < 1

Risk-off behaviour: refuse to author any new intents.

Idempotent: walks hypotheses in priority order (highest quant_score first),
stops once MAX_OPEN_INTENTS is reached across the book.

Usage:
    python3 author_intents.py
    python3 author_intents.py --dry-run
    python3 author_intents.py --max 2
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))

# Connector path
sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
from connectors.alpaca import (  # noqa: E402
    ConnectorError,
    daily_bars,
    get_account,
)

SIZE_PCT_OF_EQUITY = 0.01      # 1% per intent
NOTIONAL_FLOOR = 200.0
NOTIONAL_CEILING = 2000.0
MAX_OPEN_INTENTS = 5
STOP_RULE = "-8% from entry"
MODELED_SLIPPAGE_BPS = 8.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        raise FileNotFoundError(f"trading-intel DB missing at {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _audit(conn, *, entity_id, action, before_state, after_state, rationale):
    aid = "AUDIT-" + _now_iso().replace(":", "").replace("-", "") + "-" + entity_id[:24]
    conn.execute(
        "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, "
        "before_state, after_state, rationale_concise) VALUES (?, ?, 'trader', "
        "'trade_intent', ?, ?, ?, ?, ?)",
        (aid, _now_iso(), entity_id, action,
         before_state, after_state, (rationale or "")[:500]),
    )


def _regime_current(conn) -> str:
    row = conn.execute(
        "SELECT current FROM regime ORDER BY determined_at DESC LIMIT 1"
    ).fetchone()
    return row["current"] if row else "unknown"


def _count_open_intents(conn) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM trade_intents "
        "WHERE state IN ('proposed','critic_review','approved','submitted','partial')"
    ).fetchone()
    return int(row["n"] if row else 0)


def _has_open_exposure(conn, ticker: str) -> bool:
    sym = ticker.upper()
    pos = conn.execute(
        "SELECT 1 FROM positions WHERE UPPER(ticker)=? AND state IN "
        "('opening','open','scaling','trimming','closing')",
        (sym,),
    ).fetchone()
    if pos:
        return True
    intent = conn.execute(
        "SELECT 1 FROM trade_intents WHERE UPPER(ticker)=? AND state IN "
        "('proposed','critic_review','approved','submitted','partial')",
        (sym,),
    ).fetchone()
    return bool(intent)


def _equity_usd() -> float:
    acc = get_account()
    return float(acc.get("equity") or acc.get("portfolio_value") or 0.0)


def _last_close(ticker: str) -> float:
    bars = daily_bars(ticker, days=10)
    if not bars:
        raise ConnectorError(f"no bars for {ticker}")
    return float(bars[-1]["c"])


def _infer_direction(thesis: str | None) -> str:
    """Cheap deterministic direction parse from thesis_summary leading tokens."""
    t = (thesis or "").strip().lower()
    if t.startswith(("short", "bearish", "sell ", "fade ")) or "short/bearish" in t:
        return "short"
    if t.startswith(("long", "bullish", "buy ", "accumulate ", "add ")):
        return "long"
    # Fallback: scan first 80 chars for keywords
    head = t[:80]
    if any(w in head for w in (" short ", " bearish ", " fade ")):
        return "short"
    return "long"  # default-long for ambiguous; safer than mis-shorting


def author(conn, hyp_row, *, equity: float, dry_run: bool) -> dict:
    hid = hyp_row["id"]
    try:
        tickers = json.loads(hyp_row["tickers"] or "[]")
    except json.JSONDecodeError:
        tickers = []
    if not tickers:
        return {"id": hid, "skip": True, "reason": "no tickers"}
    ticker = str(tickers[0]).upper()

    direction = _infer_direction(hyp_row["thesis_summary"])
    if direction != "long":
        return {"id": hid, "ticker": ticker, "skip": True,
                "reason": f"direction={direction} not yet supported by baseline trader"}

    if _has_open_exposure(conn, ticker):
        return {"id": hid, "ticker": ticker, "skip": True,
                "reason": "open exposure already"}

    try:
        last = _last_close(ticker)
    except ConnectorError as exc:
        return {"id": hid, "ticker": ticker, "skip": True,
                "reason": f"price_lookup_failed: {exc}"}

    notional_raw = SIZE_PCT_OF_EQUITY * equity
    notional = max(NOTIONAL_FLOOR, min(NOTIONAL_CEILING, notional_raw))
    qty = int(math.floor(notional / last))
    if qty < 1:
        return {"id": hid, "ticker": ticker, "skip": True,
                "reason": f"qty<1 at price={last}"}
    realized_notional = round(qty * last, 2)

    edge_scorecard = {
        "quant_score": float(hyp_row["quant_score"] or 0),
        "evidence_floor_met": True,
        "regime_at_author": _regime_current(conn),
    }

    intent_id = "ti-" + uuid.uuid4().hex[:24]
    ec_id = "ec-" + uuid.uuid4().hex[:24]
    if dry_run:
        return {
            "id": hid, "ticker": ticker, "would_author": True,
            "intent_id": intent_id, "qty": qty, "price": last,
            "notional": realized_notional,
        }

    conn.execute(
        "INSERT INTO expression_candidates (id, hypothesis_id, vehicle, ticker, "
        "conviction_weight, quant_rationale, recommended, score_json, created_at) "
        "VALUES (?, ?, 'direct_equity', ?, ?, ?, 1, ?, ?)",
        (ec_id, hid, ticker, SIZE_PCT_OF_EQUITY,
         f"trader_baseline: long {ticker} at ${last} (quant_score={hyp_row['quant_score']})",
         json.dumps(edge_scorecard), _now_iso()),
    )

    conn.execute(
        "INSERT INTO trade_intents ("
        "id, hypothesis_id, expression_candidate_id, created_by, created_at, "
        "action, tranche_type, ticker, vehicle, size, entry_price_target, stop_rule, "
        "time_horizon, triggered_by, edge_scorecard_json, "
        "modeled_slippage_bps, state) "
        "VALUES (?, ?, ?, 'trader', ?, 'open', 'starter', ?, 'direct_equity', ?, ?, ?, "
        "?, 'trader_baseline_v1', ?, ?, 'proposed')",
        (intent_id, hid, ec_id, _now_iso(), ticker, float(qty), last, STOP_RULE,
         hyp_row["time_horizon"] or "position_1_4w",
         json.dumps(edge_scorecard), MODELED_SLIPPAGE_BPS),
    )
    _audit(conn, entity_id=intent_id, action="author",
           before_state=None, after_state="proposed",
           rationale=f"author open {ticker} qty={qty} @ ~{last} notional≈${realized_notional}")
    # Mark hypothesis active once an intent rides on it
    conn.execute(
        "UPDATE hypotheses SET state='active' WHERE id=? AND state='ready'", (hid,)
    )
    return {"id": hid, "ticker": ticker, "authored": True, "intent_id": intent_id,
            "qty": qty, "price": last, "notional": realized_notional}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--max", type=int, default=3,
                   help="cap intents authored this pass (default 3)")
    args = p.parse_args(argv)

    conn = _connect()
    regime = _regime_current(conn)
    if regime == "risk_off":
        print(json.dumps({"authored": 0, "skipped_all": True,
                          "reason": f"regime={regime}"}, indent=2))
        return 0

    try:
        equity = _equity_usd()
    except ConnectorError as exc:
        print(json.dumps({"error": f"alpaca_account: {exc}",
                          "authored": 0}, indent=2))
        return 2

    open_existing = _count_open_intents(conn)
    capacity = max(0, MAX_OPEN_INTENTS - open_existing)
    if capacity == 0:
        print(json.dumps({"authored": 0,
                          "reason": f"open_intents={open_existing} >= cap={MAX_OPEN_INTENTS}"},
                         indent=2))
        return 0

    rows = conn.execute(
        "SELECT id, tickers, state, quant_score, time_horizon, thesis_summary "
        "FROM hypotheses WHERE state='ready' "
        "ORDER BY quant_score DESC NULLS LAST, scored_at DESC NULLS LAST"
    ).fetchall()

    results = []
    authored = 0
    for r in rows:
        if authored >= min(capacity, args.max):
            break
        res = author(conn, r, equity=equity, dry_run=args.dry_run)
        if res.get("authored") or res.get("would_author"):
            authored += 1
        results.append(res)

    if not args.dry_run:
        conn.commit()
    print(json.dumps({
        "authored": authored,
        "considered": len(rows),
        "open_intents_before": open_existing,
        "capacity": capacity,
        "equity_usd": round(equity, 2),
        "regime": regime,
        "dry_run": bool(args.dry_run),
        "results": results,
    }, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
