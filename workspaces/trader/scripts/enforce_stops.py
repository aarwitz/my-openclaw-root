#!/usr/bin/env python3
"""Trader · enforce_stops.py — deterministic stop-rule ENFORCEMENT (D53).

Every intent the desk authors declares `stop_rule` ("-8% from entry"), but
nothing ever enforced it: on 2026-07-07 the book held ORCL -22.6%, CRM -9.9%,
CEG -8.2% against that stated stop while continuing to open new names. A rule
that is written but not executed is a lie the desk tells itself.

Each pass: for every OPEN long position whose mark is <= entry basis × (1 - STOP_PCT),
author ONE risk-reducing intent.

Default is a full `exit`, but a near-threshold breach can route to a deterministic
"soft stop" `trim` when momentum remains constructive. This cuts risk while
preserving upside participation and reduces one-bar stop-outs right before a
rebound.

Shorts: mark >= basis × (1 + STOP_PCT). The exit then flows the normal non-bypassable path —
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
from connectors.marketdata import daily_bars, latest_trade  # noqa: E402  (market data)

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))
STOP_PCT = float(os.environ.get("TRADER_STOP_PCT", "0.08"))
SOFT_STOP_BUFFER_PCT = float(os.environ.get("TRADER_SOFT_STOP_BUFFER_PCT", "0.015"))
SOFT_STOP_TRIM_FRACTION = float(os.environ.get("TRADER_SOFT_STOP_TRIM_FRACTION", "0.5"))
SOFT_STOP_LOOKBACK_DAYS = int(os.environ.get("TRADER_SOFT_STOP_LOOKBACK_DAYS", "5"))
SOFT_STOP_MIN_MOMENTUM_PCT = float(os.environ.get("TRADER_SOFT_STOP_MIN_MOMENTUM_PCT", "0.01"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_stop_postmortem(conn: sqlite3.Connection, hypothesis_id: str, *,
                            ticker: str, dd_pct: float | None = None,
                            basis: float | None = None, mark: float | None = None) -> bool:
    """Write one structured postmortem row per stopped-out hypothesis.

    This ensures stop-loss failures are not only audited as events, but also
    ingested by Archivist pattern extraction (`extract_patterns.py`).
    """
    exists = conn.execute(
        "SELECT 1 FROM postmortems WHERE hypothesis_id=? LIMIT 1",
        (hypothesis_id,),
    ).fetchone()
    if exists:
        return False

    ts = _now_iso()
    pid = "PM-STOP-" + ts.replace(":", "").replace("-", "") + "-" + hypothesis_id[:18]
    dd_txt = "unknown" if dd_pct is None else f"{dd_pct:+.1f}%"
    basis_txt = "unknown" if basis is None else f"{basis:.2f}"
    mark_txt = "unknown" if mark is None else f"{mark:.2f}"
    theme = "stop_whipsaw_or_tight_stop"
    thesis = {
        "theme": theme,
        "summary": (
            f"{ticker} stop-triggered loss at {dd_txt}; assess whether hard -8% full exit "
            "was too tight versus prevailing trend and volatility regime."
        ),
        "details": {
            "ticker": ticker,
            "stop_drawdown_pct": dd_pct,
            "entry_basis": basis,
            "stop_mark": mark,
            "stop_policy": f"-{STOP_PCT * 100:.0f}% hard stop",
            "assessment": "trade_wrong",
        },
    }
    expression = {
        "issue": "full liquidation on first stop breach",
        "improvement": "use deterministic trim-first near threshold when momentum remains constructive",
    }
    critic = {
        "challenge": "stop may clip winners during transient dislocations",
        "status": "accepted",
    }
    researcher = {
        "next_checks": [
            "measure 1d/3d/5d post-stop rebound distribution",
            "segment outcomes by regime and volatility",
        ]
    }

    conn.execute(
        "INSERT INTO postmortems (id, hypothesis_id, resolved_at, grade, "
        "thesis_analysis_json, expression_analysis_json, critic_analysis_json, "
        "researcher_analysis_json, external_mechanism_check_json, experiment_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            pid,
            hypothesis_id,
            ts,
            "wrong",
            json.dumps(thesis),
            json.dumps(expression),
            json.dumps(critic),
            json.dumps(researcher),
            json.dumps({"status": "pending"}),
            "world_model_v1",
        ),
    )
    aid = "AUDIT-" + ts.replace(":", "").replace("-", "") + "-" + pid[:24]
    conn.execute(
        "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, "
        "before_state, after_state, rationale_concise) VALUES (?, ?, 'trader', "
        "'postmortem', ?, 'record_stop_postmortem', NULL, 'written', ?)",
        (
            aid,
            ts,
            pid,
            (
                f"postmortem recorded for stopped-out thesis {hypothesis_id} "
                f"({ticker}, dd={dd_txt}, basis={basis_txt}, mark={mark_txt})"
            ),
        ),
    )
    return True


def _constructive_momentum(ticker: str) -> bool:
    """Return True when recent daily trend remains constructive.

    Deterministic check: lookback return >= min momentum and last close above
    short moving average. On data gaps we fail closed (False).
    """
    try:
        bars = daily_bars(ticker, days=max(12, SOFT_STOP_LOOKBACK_DAYS + 3))
        closes = [float(b.get("c")) for b in bars if b.get("c") is not None]
        if len(closes) < SOFT_STOP_LOOKBACK_DAYS + 1:
            return False
        last = closes[-1]
        prev = closes[-1 - SOFT_STOP_LOOKBACK_DAYS]
        if prev <= 0:
            return False
        ret = (last / prev) - 1.0
        sma_n = min(5, len(closes))
        sma = sum(closes[-sma_n:]) / float(sma_n)
        return ret >= SOFT_STOP_MIN_MOMENTUM_PCT and last >= sma
    except Exception:
        return False


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

    # Phase 0: close the learning loop on prior stop-outs — a FILLED stop exit
    # means the thesis was falsified at the desk's own risk limit. Resolve the
    # hypothesis (resolved_state='wrong' as a TRADE verdict; archivist may
    # refine the narrative grade later). Without this, stopped-out hypotheses
    # sat 'active' forever and the most informative outcomes never fed learning.
    resolved = 0
    postmortems_written = 0
    for r in conn.execute(
            "SELECT ti.hypothesis_id, ti.ticker, ti.stop_rule, p.cost_basis "
            "FROM trade_intents ti "
            "JOIN hypotheses h ON h.id = ti.hypothesis_id "
            "LEFT JOIN positions p ON p.hypothesis_id = ti.hypothesis_id AND UPPER(p.ticker)=UPPER(ti.ticker) "
            "WHERE ti.triggered_by IN ('stop_rule_enforcer_v1','stop_rule_soft_enforcer_v1') "
            "AND ti.state='filled' AND ti.action='exit' "
            "GROUP BY ti.hypothesis_id, ti.ticker "
            "ORDER BY MAX(ti.created_at) DESC").fetchall():
        hid = r["hypothesis_id"]
        tick = (r["ticker"] or "").upper()
        stop_rule = (r["stop_rule"] or "")
        dd_pct = None
        try:
            # stop_rule strings include forms like "STOP HIT: -8.3% vs -8% rule"
            pct = stop_rule.split(":", 1)[-1].strip().split("%", 1)[0]
            dd_pct = float(pct.split()[-1])
        except Exception:
            dd_pct = None
        basis = float(r["cost_basis"] or 0) if r["cost_basis"] is not None else None
        mark = None if (basis is None or dd_pct is None) else basis * (1.0 + (dd_pct / 100.0))

        if not a.dry_run:
            if _ensure_stop_postmortem(conn, hid, ticker=tick, dd_pct=dd_pct, basis=basis, mark=mark):
                postmortems_written += 1
        else:
            exists = conn.execute(
                "SELECT 1 FROM postmortems WHERE hypothesis_id=? LIMIT 1",
                (hid,),
            ).fetchone()
            if not exists:
                postmortems_written += 1

        hrow = conn.execute("SELECT state FROM hypotheses WHERE id=?", (hid,)).fetchone()
        if not hrow or hrow["state"] in ("resolved", "retired"):
            continue
        if not a.dry_run:
            conn.execute(
                "UPDATE hypotheses SET state='resolved', resolved_at=?, resolved_state='wrong', "
                "rationale_concise=COALESCE(rationale_concise,'') WHERE id=?", (_now_iso(), hid))
            aid = "AUDIT-" + _now_iso().replace(":", "").replace("-", "") + "-" + hid[:24]
            conn.execute(
                "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, "
                "before_state, after_state, rationale_concise) VALUES (?, ?, 'trader', "
                "'hypothesis', ?, 'resolve_stopped_out', 'active', 'resolved', "
                "'stop-rule exit filled: thesis falsified at the -8% risk limit (trade verdict wrong; archivist may refine)')",
                (aid, _now_iso(), hid))
        resolved += 1
    if resolved or postmortems_written:
        conn.commit()

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
        abs_qty = abs(qty)

        # Anti-whipsaw: near-threshold long stop + constructive momentum => trim
        # instead of full liquidation.
        soft_stop = False
        action = "exit"
        trigger = "stop_rule_enforcer_v1"
        intent_qty = abs_qty
        if qty > 0 and abs_qty >= 2:
            soft_floor = basis * (1 - (STOP_PCT + SOFT_STOP_BUFFER_PCT))
            near_threshold = px > soft_floor
            if near_threshold and _constructive_momentum(tick):
                soft_stop = True
                action = "trim"
                trigger = "stop_rule_soft_enforcer_v1"
                trim_qty = max(1.0, round(abs_qty * SOFT_STOP_TRIM_FRACTION, 6))
                # Keep a residual runner when possible.
                intent_qty = min(trim_qty, max(1.0, abs_qty - 1.0))

        results.append({
            "ticker": tick,
            "basis": basis,
            "mark": px,
            "dd_pct": round(dd, 1),
            "qty": qty,
            "action": action,
            "intent_qty": intent_qty,
            "soft_stop": soft_stop,
        })
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
            "VALUES (?, ?, ?, 'trader', ?, ?, NULL, ?, 'direct_equity', ?, ?, ?, "
            "'position_1_4w', ?, 8.0, 'proposed', ?)",
            (iid, p["hypothesis_id"], ec[0], _now_iso(), action, tick, intent_qty, px,
             (
                 f"SOFT STOP HIT: {dd:+.1f}% vs -{STOP_PCT*100:.0f}% rule; "
                 f"trimmed {intent_qty:.4g} to reduce risk while momentum stayed constructive"
                 if soft_stop else
                 f"STOP HIT: {dd:+.1f}% vs -{STOP_PCT*100:.0f}% rule"
             ),
             trigger,
             "long" if qty > 0 else "short"))
        aid = "AUDIT-" + _now_iso().replace(":", "").replace("-", "") + "-" + iid[:24]
        conn.execute(
            "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, "
            "before_state, after_state, rationale_concise) VALUES (?, ?, 'trader', "
            "'trade_intent', ?, 'author_stop_exit', NULL, 'proposed', ?)",
            (aid, _now_iso(), iid,
             (
                 f"soft-stop enforcement: {tick} {dd:+.1f}% from basis {basis:.2f} (mark {px:.2f}) "
                 f"is within {SOFT_STOP_BUFFER_PCT*100:.1f}% of the hard stop and trend is constructive; "
                 f"trim intent ({intent_qty:.4g}) authored to de-risk without full liquidation."
                 if soft_stop else
                 f"stop-rule enforcement: {tick} {dd:+.1f}% from basis {basis:.2f} "
                 f"(mark {px:.2f}) breaches the -{STOP_PCT*100:.0f}% stop every intent declares. "
                 "Exit authored; flows the normal gate path."
             )))
        conn.commit()
    print(json.dumps({"stop_breaches": results, "resolved_stopped_out": resolved,
                      "postmortems_written": postmortems_written,
                      "authored": 0 if a.dry_run else len(results),
                      "dry_run": a.dry_run, "stop_pct": STOP_PCT}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
