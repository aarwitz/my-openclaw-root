#!/usr/bin/env python3
"""Deterministic harness for TM-239 freshness attribution.

Validates two cases without touching the live DB:
  1. Weekend hours are discounted from evidence freshness age.
  2. A truly stale artifact is attributed deterministically in blocked_reason.

Usage:
  python3 test_gate_evaluator_freshness.py
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone

import gate_evaluator as ge  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    suffix = f" :: {detail}" if detail else ""
    print(("  PASS " if cond else "  FAIL ") + name + suffix)


def setup_db(retrieved_at: str | None) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE regime (
          current TEXT,
          signals_json TEXT,
          determined_at TEXT
        );
        CREATE TABLE hypotheses (
          id TEXT PRIMARY KEY,
          rationale_concise TEXT,
          thesis_summary TEXT
        );
        CREATE TABLE hypothesis_evidence (
          id TEXT PRIMARY KEY,
          hypothesis_id TEXT,
          source TEXT,
          source_url TEXT,
          retrieved_at TEXT
        );
        CREATE TABLE positions (
          id TEXT PRIMARY KEY,
          ticker TEXT,
          state TEXT
        );
        CREATE TABLE critic_reviews (
          id TEXT PRIMARY KEY,
          target_id TEXT,
          reviewed_at TEXT,
          all_challenges_addressed INTEGER,
          challenges_json TEXT
        );
        CREATE TABLE trade_intents (
          id TEXT PRIMARY KEY,
          hypothesis_id TEXT,
          action TEXT,
          tranche_type TEXT,
          ticker TEXT,
          vehicle TEXT,
          size REAL,
          entry_price_target REAL,
          stop_rule TEXT,
          time_horizon TEXT,
          edge_scorecard_json TEXT,
          max_fillable_size REAL,
          modeled_slippage_bps REAL,
          state TEXT,
          evidence_freshness_status TEXT,
          factor_overlap_status TEXT,
          provenance_completeness_pct REAL,
          counterargument_quality_score REAL,
          explainability_status TEXT,
          blocked_reason TEXT
        );
        CREATE TABLE audits (
          id TEXT PRIMARY KEY,
          timestamp TEXT,
          actor TEXT,
          entity_type TEXT,
          entity_id TEXT,
          action TEXT,
          before_state TEXT,
          after_state TEXT,
          rationale_concise TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO regime (current, signals_json, determined_at) VALUES (?, ?, ?)",
        ("neutral", '{"fail_closed": false}', "2026-06-22T12:00:00Z"),
    )
    conn.execute(
        "INSERT INTO hypotheses (id, rationale_concise, thesis_summary) VALUES (?, ?, ?)",
        (
            "HYPO-1",
            "A sufficiently detailed rationale that clears the explainability floor.",
            "Long setup with primary-source support.",
        ),
    )
    if retrieved_at is not None:
        conn.execute(
            "INSERT INTO hypothesis_evidence (id, hypothesis_id, source, source_url, retrieved_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "EVID-1",
                "HYPO-1",
                "Primary source",
                "https://example.com/evidence",
                retrieved_at,
            ),
        )
    conn.execute(
        "INSERT INTO critic_reviews (id, target_id, reviewed_at, all_challenges_addressed, challenges_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            "CRIT-1",
            "HYPO-1",
            "2026-06-22T19:00:00Z",
            1,
            '[{"challenge":"base case","response":"addressed"}]',
        ),
    )
    conn.execute(
        "INSERT INTO trade_intents (id, hypothesis_id, action, tranche_type, ticker, vehicle, size, "
        "entry_price_target, stop_rule, time_horizon, edge_scorecard_json, max_fillable_size, "
        "modeled_slippage_bps, state) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "INTENT-1",
            "HYPO-1",
            "open",
            "starter",
            "TEST",
            "equity",
            5,
            100.0,
            "Exit below thesis-break support.",
            "position_1_4w",
            "{}",
            10,
            15,
            "proposed",
        ),
    )
    conn.commit()
    return conn


def with_now(now_iso: str):
    real_now = ge._now
    ge._now = lambda: datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
    return real_now


def main() -> int:
    real_now = with_now("2026-06-22T20:00:00Z")
    try:
        conn = setup_db("2026-06-19T12:00:00Z")
        weekend_pass = ge.evaluate(conn, "INTENT-1")
        fresh_gate = next(g for g in weekend_pass["gates"] if g["name"] == "evidence_freshness")
        check(
            "weekend hours do not stale a Friday artifact by Monday",
            weekend_pass["all_pass"] and fresh_gate["pass"] and fresh_gate["artifacts"] == [],
            fresh_gate["detail"],
        )

        conn = setup_db("2026-06-17T12:00:00Z")
        stale = ge.evaluate(conn, "INTENT-1")
        ge.apply(conn, "INTENT-1", stale)
        blocked_reason = conn.execute(
            "SELECT blocked_reason FROM trade_intents WHERE id='INTENT-1'"
        ).fetchone()[0]
        fresh_gate = next(g for g in stale["gates"] if g["name"] == "evidence_freshness")
        artifact = fresh_gate["artifacts"][0]
        check(
            "stale artifact is attributed in gate output",
            fresh_gate["attribution_code"] == "stale:EVID-1"
            and artifact["weekend_hours_discount"] == 48.0
            and artifact["adjusted_hours_old"] == 80.0,
            str(artifact),
        )
        check(
            "blocked_reason persists freshness attribution",
            blocked_reason == "gates_failed:evidence_freshness[stale:EVID-1]",
            blocked_reason,
        )
    finally:
        ge._now = real_now

    print(f"\n{'GREEN' if not FAIL else 'RED'}: {len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
