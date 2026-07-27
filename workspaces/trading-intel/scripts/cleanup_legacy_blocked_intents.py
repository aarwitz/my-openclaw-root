#!/usr/bin/env python3
"""Report and mark legacy blocked intents that are now historical artifacts.

This script deliberately implements one disposition path only:
mark eligible legacy `risk:no sizing headroom (name=N, gross=N)` rows as
historical artifacts via a single idempotent audits row. It does not requeue,
cancel, resize, or otherwise update trade_intents.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone

import drag_report

DB_PATH = drag_report.DB_PATH
SUMMARY_KEY = "risk:no sizing headroom (name=N, gross=N)"
EXPERIMENT_ID = "TM-266"
AUDIT_ID = "AUDIT-TM-266-legacy-no-sizing-headroom"


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _json_array(raw: str | None) -> list:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


def eligible_rows(conn: sqlite3.Connection, lookback_days: int) -> list[dict]:
    rows = conn.execute(
        """SELECT id, action, blocked_reason, created_at, ticker, size, entry_price_target
           FROM trade_intents
           WHERE state='blocked'
             AND blocked_reason LIKE 'risk:no sizing headroom%'
             AND created_at > datetime('now', ?)
           ORDER BY created_at DESC""",
        (f"-{lookback_days} days",),
    ).fetchall()
    out: list[dict] = []
    for row in rows:
        review = drag_report.latest_risk_review_limits(conn, row["id"])
        limits = review.get("limits", {})
        sizing = drag_report.trade_intent_sizing_inputs(conn, row["id"])
        current = drag_report.current_gross_sizing_snapshot(
            conn,
            sizing.get("ticker", ""),
            float(sizing.get("entry_price") or 0.0),
            fallback_equity=limits.get("equity"),
        )
        if not current.get("can_buy_one_share"):
            continue
        out.append({
            "id": row["id"],
            "created_at": row["created_at"],
            "action": row["action"],
            "ticker": row["ticker"],
            "size": row["size"],
            "entry_price_target": row["entry_price_target"],
            "blocked_reason": row["blocked_reason"],
            "reviewed_at": review.get("reviewed_at"),
            "binding_breaches": (limits.get("sizing_block_attribution") or {}).get("binding_breaches")
                or _json_array(review.get("breaches_json")),
            "blocked_gross_headroom": limits.get("gross_headroom"),
            "blocked_name_headroom": limits.get("name_headroom"),
            "live_gross_headroom": current.get("gross_headroom"),
            "live_name_headroom": current.get("name_headroom"),
            "live_pending_new_risk_notional": current.get("pending_new_risk_notional"),
            "live_pending_risk_reducing_notional": current.get("pending_risk_reducing_notional"),
            "can_buy_one_share": current.get("can_buy_one_share"),
        })
    return out


def mark_historical_artifacts(conn: sqlite3.Connection, rows: list[dict]) -> str:
    ids = [row["id"] for row in rows]
    reps = ids[:5]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rationale = (
        f"TM-266 marked {len(rows)} legacy {SUMMARY_KEY} blocked rows as historical artifacts; "
        f"representative_intent_ids={','.join(reps)}; disposition=retain_audit_history"
    )[:500]
    before = json.dumps({
        "state": "blocked",
        "count": len(rows),
        "representative_intent_ids": reps,
    }, sort_keys=True)
    after = json.dumps({
        "state": "historical_artifact",
        "disposition": "retain_audit_history",
        "trade_intents_updated": False,
    }, sort_keys=True)
    conn.execute(
        "INSERT OR REPLACE INTO audits "
        "(id, timestamp, actor, entity_type, entity_id, action, before_state, after_state, "
        "rationale_concise, journal_ref, experiment_id) "
        "VALUES (?, ?, 'developer', 'trade_intents', ?, ?, ?, ?, ?, ?, ?)",
        (
            AUDIT_ID,
            now,
            SUMMARY_KEY,
            drag_report.LEGACY_ARTIFACT_ACTION,
            before,
            after,
            rationale,
            "TM-266",
            EXPERIMENT_ID,
        ),
    )
    conn.commit()
    return AUDIT_ID


def read_back(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute(
        "SELECT id, timestamp, actor, entity_type, entity_id, action, before_state, "
        "after_state, rationale_concise, journal_ref, experiment_id "
        "FROM audits WHERE id=?",
        (AUDIT_ID,),
    ).fetchone()
    return dict(row) if row else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument("--lookback-days", type=int, default=drag_report.BLOCK_LOOKBACK_DAYS)
    parser.add_argument("--mark-historical", action="store_true")
    args = parser.parse_args(argv)

    conn = _connect(args.db)
    try:
        rows = eligible_rows(conn, args.lookback_days)
        audit_id = mark_historical_artifacts(conn, rows) if args.mark_historical else None
        result = {
            "task_id": "TM-266",
            "summary_key": SUMMARY_KEY,
            "lookback_days": args.lookback_days,
            "eligible_count": len(rows),
            "representative_intent_ids": [row["id"] for row in rows[:5]],
            "disposition": "mark_historical_artifacts" if args.mark_historical else "report_only",
            "audit_id": audit_id,
            "audit_read_back": read_back(conn) if audit_id else None,
            "eligible_rows": rows,
        }
    finally:
        conn.close()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
