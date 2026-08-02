#!/usr/bin/env python3
"""Audit whether current desk inventory was opened through the modern gate stack.

Historical positions are never assigned invented provenance. Positions opened
before the prediction-lineage cutover are labeled legacy and remain
risk-reducing-only under the current intent gates. Any post-cutover opening that
lacks a pre-intent prediction, substantive Critic pass, or Risk approval is an
integrity violation.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from developer_db import connect, now_iso  # noqa: E402


def _opening_intent(conn: sqlite3.Connection, hypothesis_id: str, ticker: str):
    row = conn.execute(
        "SELECT ti.id,ti.created_at,o.filled_at FROM orders o "
        "JOIN trade_intents ti ON ti.id=o.trade_intent_id "
        "WHERE ti.hypothesis_id=? AND UPPER(ti.ticker)=UPPER(?) "
        "AND ti.action IN ('open','add') AND o.status='filled' "
        "ORDER BY o.filled_at,ti.created_at LIMIT 1",
        (hypothesis_id, ticker),
    ).fetchone()
    if row:
        return row
    return conn.execute(
        "SELECT id,created_at,NULL AS filled_at FROM trade_intents "
        "WHERE hypothesis_id=? AND UPPER(ticker)=UPPER(?) "
        "AND action IN ('open','add') ORDER BY created_at LIMIT 1",
        (hypothesis_id, ticker),
    ).fetchone()


def _filled_intents(conn: sqlite3.Connection, hypothesis_id: str, ticker: str):
    return conn.execute(
        "SELECT ti.id,ti.created_at,o.filled_at FROM orders o "
        "JOIN trade_intents ti ON ti.id=o.trade_intent_id "
        "WHERE ti.hypothesis_id=? AND UPPER(ti.ticker)=UPPER(?) "
        "AND ti.action IN ('open','add') AND o.status='filled' "
        "ORDER BY o.filled_at,ti.created_at",
        (hypothesis_id, ticker),
    ).fetchall()


def _intent_lineage(
    conn: sqlite3.Connection, hypothesis_id: str, intent: sqlite3.Row | None,
) -> dict:
    intent_id = None if intent is None else str(intent["id"])
    intent_at = None if intent is None else str(intent["created_at"])
    filled_at = None if intent is None or intent["filled_at"] is None else str(intent["filled_at"])
    prediction = None
    critic = None
    risk = None
    if intent_at:
        prediction = conn.execute(
            "SELECT id,predicted_at FROM predictions WHERE hypothesis_id=? "
            "AND predicted_at<=? ORDER BY predicted_at DESC LIMIT 1",
            (hypothesis_id, intent_at),
        ).fetchone()
        critic = conn.execute(
            "SELECT id,reviewed_at FROM critic_reviews "
            "WHERE target_type='hypothesis' AND target_id=? "
            "AND reviewed_by='critic' AND all_challenges_addressed=1 "
            "AND reviewed_at<=? ORDER BY reviewed_at DESC LIMIT 1",
            (hypothesis_id, intent_at),
        ).fetchone()
    if intent_id and filled_at:
        risk = conn.execute(
            "SELECT id,reviewed_at,verdict FROM risk_reviews "
            "WHERE target_type='trade_intent' AND target_id=? "
            "AND verdict IN ('approved','resized') AND reviewed_at<=? "
            "ORDER BY reviewed_at DESC LIMIT 1",
            (intent_id, filled_at),
        ).fetchone()
    gaps = []
    if not intent_at:
        gaps.append("opening_intent")
    if not filled_at:
        gaps.append("opening_fill")
    if prediction is None:
        gaps.append("prediction_before_intent")
    if critic is None:
        gaps.append("substantive_critic_before_intent")
    if risk is None:
        gaps.append("risk_approval_before_fill")
    return {
        "intent_id": intent_id,
        "intent_at": intent_at,
        "filled_at": filled_at,
        "prediction": prediction,
        "critic": critic,
        "risk": risk,
        "gaps": gaps,
        "complete": not gaps,
    }


def build_report(conn: sqlite3.Connection) -> dict:
    conn.row_factory = sqlite3.Row
    cutover_row = conn.execute(
        "SELECT value FROM meta WHERE key='_prediction_lineage_cutover'"
    ).fetchone()
    cutover = None if not cutover_row else str(cutover_row[0])
    rows = []
    for position in conn.execute(
        "SELECT id,hypothesis_id,ticker,qty,current_value,opened_at FROM positions "
        "WHERE book='desk' AND state!='closed' ORDER BY ticker"
    ):
        intent = _opening_intent(conn, position["hypothesis_id"], position["ticker"])
        opening = _intent_lineage(conn, position["hypothesis_id"], intent)
        opened_at = None if position["opened_at"] is None else str(position["opened_at"])
        lineage_times = [value for value in (opening["intent_at"], opened_at) if value]
        lineage_time = min(lineage_times) if lineage_times else None
        pre_cutover = bool(cutover and lineage_time and lineage_time < cutover)

        post_cutover_violations = []
        post_cutover_fill_count = 0
        for filled_intent in _filled_intents(
            conn, position["hypothesis_id"], position["ticker"]
        ):
            if not cutover or str(filled_intent["created_at"]) < cutover:
                continue
            post_cutover_fill_count += 1
            check = _intent_lineage(conn, position["hypothesis_id"], filled_intent)
            if not check["complete"]:
                post_cutover_violations.append({
                    "intent_id": check["intent_id"], "gaps": check["gaps"],
                })

        if not cutover:
            status = "post_cutover_violation"
        elif post_cutover_violations:
            status = "post_cutover_violation"
        elif pre_cutover:
            status = "legacy_pre_cutover"
        elif opening["complete"]:
            status = "modern_lineage"
        else:
            status = "post_cutover_violation"
        rows.append({
            "position_id": position["id"],
            "hypothesis_id": position["hypothesis_id"],
            "ticker": position["ticker"],
            "qty": float(position["qty"]),
            "current_value": position["current_value"],
            "gross_value": abs(float(position["current_value"] or 0.0)),
            "opened_at": opened_at,
            "opening_intent_id": opening["intent_id"],
            "opening_intent_at": opening["intent_at"],
            "opening_fill_at": opening["filled_at"],
            "prediction_id": None if opening["prediction"] is None else opening["prediction"]["id"],
            "critic_review_id": None if opening["critic"] is None else opening["critic"]["id"],
            "risk_review_id": None if opening["risk"] is None else opening["risk"]["id"],
            "post_cutover_fill_count": post_cutover_fill_count,
            "post_cutover_violations": post_cutover_violations,
            "status": status,
            "gaps": opening["gaps"],
        })
    statuses = {
        name: sum(row["status"] == name for row in rows)
        for name in ("modern_lineage", "legacy_pre_cutover", "post_cutover_violation")
    }
    gross_by_status = {
        name: round(sum(row["gross_value"] for row in rows if row["status"] == name), 2)
        for name in statuses
    }
    return {
        "generated_at": now_iso(),
        "prediction_lineage_cutover": cutover,
        "cutover_present": cutover is not None,
        "open_positions": len(rows),
        "status_counts": statuses,
        "gross_value_by_status": gross_by_status,
        "modern_lineage_pct": (
            None if not rows else round(100.0 * statuses["modern_lineage"] / len(rows), 2)
        ),
        "policy": (
            "legacy positions retain true history and may only be reduced or freshly "
            "re-underwritten through current gates; post-cutover gaps are integrity failures"
        ),
        "positions": rows,
    }


def main() -> int:
    conn = connect()
    report = build_report(conn)
    print(json.dumps(report, indent=2))
    return 1 if report["status_counts"]["post_cutover_violation"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
