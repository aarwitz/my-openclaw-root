#!/usr/bin/env python3
"""Market-open edge-case harness for the live EXECUTION path — asserts FAIL-CLOSED behavior under adverse
conditions (stale reasoning, price drift, volatile vs calm names, no live quote, connector failure).

Deterministic: monkeypatches the live-price + realized-vol so it needs NO network/market and is safe to run
any time (dry_run=True throughout — never submits an order). Exit code 0 = all green.

  python3 test_pipeline_edgecases.py
"""

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/executor/scripts")
sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
import execute_intent as ex  # noqa: E402

PASS, FAIL = [], []


def check(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name)


def _intent(action, eref=100.0, age_min=3, ticker="TESTX"):
    created = (datetime.now(timezone.utc) - timedelta(minutes=age_min)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": "TEST", "state": "approved", "action": action, "size": 2, "vehicle": "equity",
            "ticker": ticker, "entry_price_target": eref, "created_at": created}


def run(live_price, dvol, intent_row, raise_quote=False):
    if raise_quote:
        ex.latest_trade = lambda t: (_ for _ in ()).throw(RuntimeError("feed down"))
    else:
        ex.latest_trade = lambda t: ({"price": live_price, "ts": "now"} if live_price is not None else None)
    ex._recent_daily_vol = lambda t: dvol
    return ex.process(intent_row, dry_run=True, conn=None)


def main():
    act = [r[0] for r in sqlite3.connect(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))
           .execute("SELECT DISTINCT action FROM trade_intents")][0]
    print("EXECUTION FRESHNESS / FAIL-CLOSED EDGE CASES (dry-run, deterministic)\n")

    r = run(100.0, 0.02, _intent(act, eref=100.0, age_min=3))
    check("fresh signal -> submits a marketable LIMIT near live", bool(r.get("would_submit")) and r["would_submit"]["order_type"] == "limit")

    r = run(100.0, 0.02, _intent(act, eref=100.0, age_min=600))
    check("stale reasoning (600m old) -> REJECTED", bool(r.get("rejected_stale")))

    r = run(108.0, 0.01, _intent(act, eref=100.0, age_min=3))            # +8% drift, calm (dvol 1%)
    check("calm name +8% drift -> REJECTED (tol ~4%)", bool(r.get("rejected_stale")))

    r = run(108.0, 0.09, _intent(act, eref=100.0, age_min=3))            # +8% drift, volatile (dvol 9%)
    check("volatile name +8% drift -> PASSES (vol-aware tol)", bool(r.get("would_submit")))

    r = run(125.0, 0.09, _intent(act, eref=100.0, age_min=3))            # +25% drift > 15% cap
    check("any name +25% drift -> REJECTED (drift cap)", bool(r.get("rejected_stale")))

    r = run(None, 0.02, _intent(act, eref=100.0, age_min=3))             # halted / feed-down
    check("no live quote (halted/feed-down) -> SKIP, no blind order", bool(r.get("skipped_no_quote")) and not r.get("would_submit"))

    r = run(None, 0.02, _intent(act, eref=100.0, age_min=600))           # no quote AND stale
    check("no quote + stale -> REJECTED (terminal, not retried forever)", bool(r.get("rejected_stale")))

    r = run(None, 0.02, _intent(act, eref=100.0, age_min=3), raise_quote=True)  # connector raises
    check("connector failure -> no crash, SKIP (fail-closed)", bool(r.get("skipped_no_quote")))

    print(f"\n{'GREEN — market-open execution path is fail-closed' if not FAIL else 'RED — ' + str(len(FAIL)) + ' FAILURES'}: "
          f"{len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
