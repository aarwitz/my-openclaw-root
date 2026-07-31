#!/usr/bin/env python3
"""Bounded handoff between deterministic triage and substantive Critic review.

``list`` exposes only scored hypotheses that have not received a substantive
Critic review since their latest quant score. The Critic agent writes review
rows; ``finalize --apply`` validates that output and performs the mechanical
state transition. This gives the stage an explicit owner without allowing a
checklist screen or a stale review to promote a hypothesis.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))
MIN_SCORE = 60.0


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def candidates(conn: sqlite3.Connection, limit: int = 10) -> list[dict]:
    rows = conn.execute(
        "SELECT h.* FROM hypotheses h WHERE h.state='scored' "
        "AND h.quant_score>=? AND NOT EXISTS ("
        "  SELECT 1 FROM critic_reviews c WHERE c.target_type='hypothesis' "
        "  AND c.target_id=h.id AND c.reviewed_by='critic' "
        "  AND c.reviewed_at>=COALESCE(h.scored_at,h.created_at)"
        ") ORDER BY h.scored_at ASC,h.id ASC LIMIT ?",
        (MIN_SCORE, limit),
    ).fetchall()
    out: list[dict] = []
    for row in rows:
        evidence = conn.execute(
            "SELECT indicator,value,signal_type,source_url,retrieved_at FROM hypothesis_evidence "
            "WHERE hypothesis_id=? ORDER BY retrieved_at DESC LIMIT 8",
            (row["id"],),
        ).fetchall()
        falsifiers = conn.execute(
            "SELECT condition,current_status,updated_at FROM falsifier_signals "
            "WHERE hypothesis_id=? ORDER BY updated_at DESC",
            (row["id"],),
        ).fetchall()
        try:
            tickers = [str(value).upper() for value in json.loads(row["tickers"] or "[]")]
        except (json.JSONDecodeError, TypeError):
            tickers = []
        valuation = None
        if tickers:
            try:
                valuation_row = conn.execute(
                    "SELECT as_of,zone,margin_of_safety,confidence,implied_growth,growth_assumed "
                    "FROM valuations WHERE ticker=? AND applicable=1 ORDER BY as_of DESC LIMIT 1",
                    (tickers[0],),
                ).fetchone()
            except sqlite3.Error:
                valuation_row = None
            valuation = dict(valuation_row) if valuation_row else None
        out.append({
            "hypothesis_id": row["id"],
            "tickers": tickers,
            "thesis_summary": row["thesis_summary"],
            "rationale_concise": row["rationale_concise"],
            "time_horizon": row["time_horizon"],
            "quant_score": row["quant_score"],
            "scored_at": row["scored_at"],
            "evidence": [dict(item) for item in evidence],
            "falsifiers": [dict(item) for item in falsifiers],
            "valuation": valuation,
        })
    return out


def _review_quality(review: sqlite3.Row) -> tuple[bool, str, list[dict]]:
    try:
        challenges = json.loads(review["challenges_json"] or "[]")
    except json.JSONDecodeError:
        return False, "malformed challenges_json", []
    if not isinstance(challenges, list) or len(challenges) < 2:
        return False, "fewer than two counterarguments", []
    if any(
        not isinstance(item, dict)
        or len(str(item.get("challenge") or "").strip()) < 20
        or not isinstance(item.get("resolved"), bool)
        for item in challenges
    ):
        return False, "counterarguments are not concrete/structured", challenges
    addressed = bool(review["all_challenges_addressed"])
    all_resolved = all(item["resolved"] is True for item in challenges)
    if addressed != all_resolved:
        return False, "addressed flag disagrees with challenge resolutions", challenges
    if addressed and any(len(str(item.get("response") or "").strip()) < 40 for item in challenges):
        return False, "resolved counterargument response is under 40 characters", challenges
    return True, "addressed" if addressed else "unresolved", challenges


def finalize(conn: sqlite3.Connection, *, apply: bool = False) -> dict:
    rows = conn.execute(
        "SELECT h.id,h.state,h.scored_at,h.created_at,c.id review_id,c.reviewed_at,"
        "c.challenges_json,c.all_challenges_addressed FROM hypotheses h "
        "JOIN critic_reviews c ON c.id=("
        " SELECT c2.id FROM critic_reviews c2 WHERE c2.target_type='hypothesis' "
        " AND c2.target_id=h.id AND c2.reviewed_by='critic' "
        " AND c2.reviewed_at>=COALESCE(h.scored_at,h.created_at) "
        " ORDER BY c2.reviewed_at DESC,c2.id DESC LIMIT 1"
        ") WHERE h.state='scored' ORDER BY c.reviewed_at,h.id"
    ).fetchall()
    transitioned: list[dict] = []
    invalid: list[dict] = []
    for row in rows:
        valid, reason, _ = _review_quality(row)
        if not valid:
            invalid.append({"hypothesis_id": row["id"], "review_id": row["review_id"], "reason": reason})
            continue
        after = "ready" if bool(row["all_challenges_addressed"]) else "challenged"
        transitioned.append({
            "hypothesis_id": row["id"], "review_id": row["review_id"],
            "before": "scored", "after": after,
        })
        if apply:
            conn.execute(
                "UPDATE hypotheses SET state=?,last_critic_review_at=? WHERE id=? AND state='scored'",
                (after, row["reviewed_at"], row["id"]),
            )
            conn.execute(
                "INSERT INTO audits(id,timestamp,actor,entity_type,entity_id,action,"
                "before_state,after_state,rationale_concise) VALUES(?,?, 'developer',"
                "'hypothesis',?,'finalize_critic_review','scored',?,?)",
                (
                    "AUDIT-" + uuid.uuid4().hex, _now(), row["id"], after,
                    f"Validated substantive Critic review {row['review_id']}; outcome={reason}",
                ),
            )
    if apply:
        conn.commit()
    return {"apply": apply, "transitioned": transitioned, "invalid": invalid}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DB_PATH))
    sub = parser.add_subparsers(dest="command", required=True)
    list_parser = sub.add_parser("list")
    list_parser.add_argument("--max", type=int, default=10)
    finalize_parser = sub.add_parser("finalize")
    finalize_parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    conn = _connect(args.db)
    if args.command == "list":
        rows = candidates(conn, max(0, args.max))
        result = {"count": len(rows), "candidates": rows}
    else:
        result = finalize(conn, apply=args.apply)
    conn.close()
    print(json.dumps(result, indent=2))
    return 1 if result.get("invalid") else 0


if __name__ == "__main__":
    raise SystemExit(main())
