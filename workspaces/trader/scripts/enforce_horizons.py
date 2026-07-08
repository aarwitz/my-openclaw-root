#!/usr/bin/env python3
"""Trader · enforce_horizons.py — deterministic thesis-horizon enforcement.

A position must not drift forever after its declared thesis horizon.
This script authors deterministic EXIT intents when a holding is beyond
(horizon trading days + grace), routed through the normal non-bypassable gate
path (gate_evaluator -> risk_gate -> executor).

Rules:
- Uses the holding's linked hypothesis time_horizon.
- Converts trading-day horizon to a conservative calendar approximation.
- Adds +5 trading-day grace (converted to calendar days).
- Idempotent: skips names that already have open exit/trim intents.

Usage:
  python3 enforce_horizons.py [--dry-run]
"""

from __future__ import annotations

import sys
sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper

require_wrapper()

import argparse
import json
import math
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
from connectors.alpaca import latest_trade  # noqa: E402
import worldmodel as wm  # noqa: E402

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))
TRADING_TO_CAL = 1.45
GRACE_TRADING_DAYS = int(os.environ.get("TRADER_HORIZON_GRACE_DAYS", "5"))
# TRADING days per horizon — canonical wm.HORIZON_DAYS (position_1_4w = 15 td),
# the SAME clock predict.py and grade_outcomes.py use. Any other clock here
# would exit positions out of sync with prediction maturity/grading.
HORIZON_DAYS = dict(wm.HORIZON_DAYS)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cal_days(horizon: str) -> int:
    base_td = HORIZON_DAYS.get(horizon or "", 15)
    return int(math.ceil(base_td * TRADING_TO_CAL))


def _parse_opened_at(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except Exception:
        return None


def _spy_return(since_iso: str) -> float | None:
    """SPY close-to-latest return since a date (market-relative grading)."""
    try:
        from connectors.alpaca import daily_bars
        bars = daily_bars("SPY", days=400)
        past = [b for b in bars if b["t"][:10] >= since_iso[:10]]
        if len(past) >= 2 and float(past[0]["c"]) > 0:
            return float(past[-1]["c"]) / float(past[0]["c"]) - 1.0
    except Exception:
        pass
    return None


def _phase0_grade_filled_exits(conn) -> int:
    """Close the learning loop for FILLED horizon exits whose hypothesis has NO
    predictions (grade_outcomes grades via predictions and can never touch
    these — 65 active hypotheses predate the prediction machinery). Grades the
    thesis from realized market-relative return over the holding window.
    Hypotheses WITH predictions are left for grade_outcomes (canonical path)."""
    graded = 0
    rows = conn.execute(
        "SELECT DISTINCT ti.hypothesis_id, ti.ticker FROM trade_intents ti "
        "JOIN hypotheses h ON h.id = ti.hypothesis_id "
        "WHERE ti.triggered_by='horizon_enforcer_v1' AND ti.state='filled' "
        "AND h.state NOT IN ('resolved','retired') "
        "AND NOT EXISTS (SELECT 1 FROM predictions p WHERE p.hypothesis_id = ti.hypothesis_id)"
    ).fetchall()
    for hid, tick in rows:
        pos = conn.execute(
            "SELECT cost_basis, opened_at, qty FROM positions WHERE hypothesis_id=? "
            "ORDER BY opened_at DESC LIMIT 1", (hid,)).fetchone()
        if not pos or not pos["cost_basis"] or not pos["opened_at"]:
            continue
        basis = float(pos["cost_basis"])
        lt = latest_trade(str(tick).upper())
        mark = float(lt.get("price") or 0.0) if isinstance(lt, dict) else 0.0
        if basis <= 0 or mark <= 0:
            continue
        name_ret = mark / basis - 1.0
        spy_ret = _spy_return(str(pos["opened_at"]))
        if spy_ret is None:
            continue
        excess = name_ret - spy_ret
        # direction from the exited intent's direction column (fallback long)
        drow = conn.execute(
            "SELECT direction FROM trade_intents WHERE hypothesis_id=? "
            "AND triggered_by='horizon_enforcer_v1' ORDER BY created_at DESC LIMIT 1", (hid,)).fetchone()
        short = (drow and drow[0] == "short")
        hit = (excess < 0) if short else (excess > 0)
        rs = "correct_wrong_reasons" if hit else "wrong"
        note = (f"horizon-exit grade (no prediction existed): ret={name_ret:+.3f} "
                f"spy={spy_ret:+.3f} excess={excess:+.3f} -> {rs}")
        now_iso = _now_iso()
        conn.execute(
            "UPDATE hypotheses SET state='resolved', resolved_at=?, resolved_state=?, "
            "archivist_grade=? WHERE id=?", (now_iso, rs, note, hid))
        aid = "AUDIT-" + now_iso.replace(":", "").replace("-", "") + "-" + str(hid)[:24]
        conn.execute(
            "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, "
            "before_state, after_state, rationale_concise) VALUES (?, ?, 'trader', "
            "'hypothesis', ?, 'resolve_horizon_exit', 'active', 'resolved', ?)",
            (aid, now_iso, hid, note[:500]))
        graded += 1
    if graded:
        conn.commit()
    return graded


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    graded_phase0 = 0 if a.dry_run else _phase0_grade_filled_exits(conn)

    rows = conn.execute(
        "SELECT p.id, p.hypothesis_id, p.ticker, p.qty, p.opened_at, h.time_horizon "
        "FROM positions p LEFT JOIN hypotheses h ON h.id = p.hypothesis_id "
        "WHERE p.state IN ('opening','open','scaling') AND p.qty != 0"
    ).fetchall()

    open_exits = {r[0].upper() for r in conn.execute(
        "SELECT DISTINCT ticker FROM trade_intents WHERE action IN ('exit','trim') "
        "AND state IN ('proposed','critic_review','risk_review','approved','submitted','partial')"
    )}

    now = datetime.now(timezone.utc)
    overdue = []
    authored = 0

    for p in rows:
        tick = str(p["ticker"] or "").upper()
        if not tick or tick in open_exits:
            continue

        opened_at = _parse_opened_at(p["opened_at"])
        if opened_at is None:
            continue

        horizon = p["time_horizon"] or "position_1_4w"
        max_age_days = _cal_days(horizon) + int(math.ceil(GRACE_TRADING_DAYS * TRADING_TO_CAL))
        age_days = (now - opened_at).days
        if age_days <= max_age_days:
            continue

        qty = float(p["qty"] or 0.0)
        if qty == 0:
            continue

        lt = latest_trade(tick)
        mark = float(lt.get("price") or 0.0) if isinstance(lt, dict) else 0.0
        if mark <= 0:
            continue

        overdue.append({
            "ticker": tick,
            "hypothesis_id": p["hypothesis_id"],
            "time_horizon": horizon,
            "age_days": age_days,
            "max_age_days": max_age_days,
            "qty": qty,
            "mark": round(mark, 4),
        })

        if a.dry_run:
            continue

        ec = conn.execute(
            "SELECT expression_candidate_id FROM trade_intents WHERE UPPER(ticker)=? "
            "AND expression_candidate_id IS NOT NULL ORDER BY created_at DESC LIMIT 1",
            (tick,),
        ).fetchone()
        if not ec:
            overdue[-1]["skipped"] = "no expression candidate lineage"
            continue

        iid = f"ti-horizon-{uuid.uuid4().hex[:20]}"
        now_iso = _now_iso()
        conn.execute(
            "INSERT INTO trade_intents (id, hypothesis_id, expression_candidate_id, created_by, "
            "created_at, action, tranche_type, ticker, vehicle, size, entry_price_target, "
            "stop_rule, time_horizon, triggered_by, modeled_slippage_bps, state, direction) "
            "VALUES (?, ?, ?, 'trader', ?, 'exit', NULL, ?, 'direct_equity', ?, ?, ?, ?, "
            "'horizon_enforcer_v1', 8.0, 'proposed', ?)",
            (
                iid,
                p["hypothesis_id"],
                ec[0],
                now_iso,
                tick,
                abs(qty),
                mark,
                f"HORIZON EXPIRED: age={age_days}d > {max_age_days}d ({horizon} + {GRACE_TRADING_DAYS}td grace)",
                horizon,
                "long" if qty > 0 else "short",
            ),
        )

        aid = "AUDIT-" + now_iso.replace(":", "").replace("-", "") + "-" + iid[:24]
        conn.execute(
            "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, "
            "before_state, after_state, rationale_concise) VALUES (?, ?, 'trader', "
            "'trade_intent', ?, 'author_horizon_exit', NULL, 'proposed', ?)",
            (
                aid,
                now_iso,
                iid,
                f"horizon enforcement: {tick} age {age_days}d exceeded {max_age_days}d for "
                f"{horizon}; authored deterministic exit via normal gate path",
            ),
        )
        authored += 1

    if not a.dry_run:
        conn.commit()

    print(json.dumps({
        "checked_positions": len(rows),
        "overdue_positions": len(overdue),
        "authored": authored,
        "graded_filled_exits_no_prediction": graded_phase0,
        "grace_trading_days": GRACE_TRADING_DAYS,
        "dry_run": bool(a.dry_run),
        "results": overdue,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
