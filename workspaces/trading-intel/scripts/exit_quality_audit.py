#!/usr/bin/env python3
"""Trading-intel · exit_quality_audit.py — post-exit rebound tracking (D56).

"We sold AVGO right before it went up 5%" must be a MEASURED quantity, not an
anecdote. For every FILLED exit/trim intent this script computes what the
ticker did 1/3/5 trading days AFTER the exit fill, vs the exit price:

  ret_1d / ret_3d / ret_5d   — raw post-exit returns
  spy_ret_5d                  — SPY over the same window (market context)
  premature_5d                — 1 when ret_5d >= PREMATURE_PCT (default +3%)
  regret_usd_5d               — dollars of upside handed back (qty × price × ret)

Rows are recomputed every pass until the 5-day window matures (final=1), so the
desk sees regret build in near-real-time. Aggregates by exit lane
(stop/horizon/swap) tell us WHICH exit rule is bleeding money, feeding the
slow-lane rule_proposals loop with evidence instead of vibes.

Deterministic; pure price math; safe to re-run.

  python3 exit_quality_audit.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
from connectors.marketdata import daily_bars  # noqa: E402

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))
PREMATURE_PCT = float(os.environ.get("EXIT_PREMATURE_PCT", "0.03"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bars_after(ticker: str, after_date: str, need: int = 6) -> list[dict]:
    """Daily bars strictly after `after_date` (YYYY-MM-DD), oldest first."""
    try:
        bars = daily_bars(ticker, days=30)
    except Exception:
        return []
    return [b for b in bars if str(b.get("t", ""))[:10] > after_date][:need]


def _ret_at(bars: list[dict], n: int, base: float) -> float | None:
    """Return vs base after n trading days (bars[n-1].close), None if unmatured."""
    if base <= 0 or len(bars) < n:
        return None
    c = float(bars[n - 1].get("c") or 0)
    if c <= 0:
        return None
    return c / base - 1.0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    conn = sqlite3.connect(DB_PATH, timeout=60.0)
    conn.row_factory = sqlite3.Row

    # Filled exits with fill price/time resolved through the fallback chain:
    # broker orders → sim ledger → intent's authoring mark.
    exits = conn.execute(
        "SELECT ti.id AS intent_id, ti.hypothesis_id, UPPER(ti.ticker) AS ticker, "
        "  ti.triggered_by, ti.created_at, ti.entry_price_target, ti.size, "
        "  o.avg_fill_price AS o_price, o.filled_at AS o_time, o.qty AS o_qty, "
        "  so.fill_price AS s_price, so.filled_at AS s_time, so.qty AS s_qty "
        "FROM trade_intents ti "
        "LEFT JOIN orders o ON o.trade_intent_id = ti.id "
        "LEFT JOIN sim_orders so ON so.order_id = o.broker_order_id "
        "WHERE ti.action IN ('exit','trim') AND ti.state='filled'").fetchall()

    done = {r["intent_id"] for r in conn.execute(
        "SELECT intent_id FROM exit_quality WHERE final=1")}

    spy_cache: list[dict] | None = None
    rows, updated = [], 0
    for e in exits:
        if e["intent_id"] in done:
            continue
        price = e["o_price"] or e["s_price"] or e["entry_price_target"]
        when = e["o_time"] or e["s_time"] or e["created_at"]
        qty = e["o_qty"] or e["s_qty"] or e["size"] or 0
        if not price or not when:
            continue
        price = float(price)
        exit_day = str(when)[:10]
        bars = _bars_after(e["ticker"], exit_day)
        r1 = _ret_at(bars, 1, price)
        r3 = _ret_at(bars, 3, price)
        r5 = _ret_at(bars, 5, price)
        final = 1 if r5 is not None else 0

        spy5 = None
        if spy_cache is None:
            spy_cache = _bars_after("SPY", exit_day) or []
        sbars = [b for b in (spy_cache or []) if str(b.get("t", ""))[:10] > exit_day]
        if len(sbars) >= 5:
            s0 = float(sbars[0].get("o") or sbars[0].get("c") or 0)
            s5 = float(sbars[4].get("c") or 0)
            if s0 > 0 and s5 > 0:
                spy5 = s5 / s0 - 1.0

        best = r5 if r5 is not None else (r3 if r3 is not None else r1)
        premature = 1 if (r5 is not None and r5 >= PREMATURE_PCT) else 0
        regret = round(float(qty) * price * max(best or 0.0, 0.0), 2)

        row = {
            "intent_id": e["intent_id"], "ticker": e["ticker"],
            "exit_reason": e["triggered_by"], "exited_at": when,
            "exit_price": round(price, 4), "qty": float(qty),
            "ret_1d": r1, "ret_3d": r3, "ret_5d": r5, "spy_ret_5d": spy5,
            "premature_5d": premature, "regret_usd_5d": regret, "final": final,
        }
        rows.append(row)
        if a.dry_run:
            continue
        conn.execute(
            "INSERT INTO exit_quality (id, intent_id, hypothesis_id, ticker, exit_reason, "
            "exited_at, exit_price, qty, ret_1d, ret_3d, ret_5d, spy_ret_5d, premature_5d, "
            "regret_usd_5d, computed_at, final) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(intent_id) DO UPDATE SET ret_1d=excluded.ret_1d, "
            "ret_3d=excluded.ret_3d, ret_5d=excluded.ret_5d, spy_ret_5d=excluded.spy_ret_5d, "
            "premature_5d=excluded.premature_5d, regret_usd_5d=excluded.regret_usd_5d, "
            "computed_at=excluded.computed_at, final=excluded.final",
            ("eq-" + uuid.uuid4().hex[:16], e["intent_id"], e["hypothesis_id"],
             e["ticker"], e["triggered_by"], when, price, float(qty),
             r1, r3, r5, spy5, premature, regret, _now_iso(), final))
        updated += 1
    if not a.dry_run and updated:
        # One summary audit per run keeps the trail light but visible.
        ts = _now_iso()
        prem = sum(1 for r in rows if r["premature_5d"])
        regret_total = round(sum(r["regret_usd_5d"] for r in rows), 2)
        aid = "AUDIT-" + ts.replace(":", "").replace("-", "") + "-exit-quality"
        conn.execute(
            "INSERT OR REPLACE INTO audits (id, timestamp, actor, entity_type, entity_id, "
            "action, rationale_concise) VALUES (?, ?, 'archivist', 'exit_quality', "
            "'exit-quality-sweep', 'compute', ?)",
            (aid, ts, f"tracked {updated} exits; {prem} premature (>= +{PREMATURE_PCT*100:.0f}% "
             f"within 5d); rebound regret ${regret_total}"))
        conn.commit()

    # Lane aggregates — which exit RULE is bleeding money?
    lanes = {}
    for r in conn.execute(
            "SELECT exit_reason, COUNT(*) n, SUM(premature_5d) prem, "
            "ROUND(SUM(regret_usd_5d),2) regret FROM exit_quality GROUP BY exit_reason"):
        lanes[r["exit_reason"] or "unknown"] = {
            "exits": r["n"], "premature": r["prem"] or 0, "regret_usd": r["regret"] or 0.0}

    print(json.dumps({"tracked": len(rows), "updated": updated, "dry_run": a.dry_run,
                      "premature_threshold_pct": PREMATURE_PCT * 100,
                      "by_lane": lanes, "rows": rows}, indent=2, default=str))
    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
