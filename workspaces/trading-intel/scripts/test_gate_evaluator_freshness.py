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
import unittest
from datetime import datetime, timezone

import gate_evaluator as ge  # noqa: E402

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
          reviewed_by TEXT,
          all_challenges_addressed INTEGER,
          challenges_json TEXT
        );
        CREATE TABLE predictions (
          id TEXT PRIMARY KEY,
          hypothesis_id TEXT,
          predicted_at TEXT,
          p_correct REAL,
          return_p50 REAL
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
          created_at TEXT,
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
        "INSERT INTO critic_reviews (id, target_id, reviewed_at, reviewed_by, all_challenges_addressed, challenges_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "CRIT-1",
            "HYPO-1",
            "2026-06-22T19:00:00Z",
            "critic",
            1,
            '[{"challenge":"base case","response":"addressed"}]',
        ),
    )
    conn.execute(
        "INSERT INTO trade_intents (id, hypothesis_id, action, tranche_type, ticker, vehicle, size, "
        "entry_price_target, stop_rule, time_horizon, edge_scorecard_json, max_fillable_size, "
        "modeled_slippage_bps, state, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
            "2026-06-22T19:30:00Z",
        ),
    )
    conn.commit()
    return conn


class GateFreshnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.real_now = ge._now
        ge._now = lambda: datetime.fromisoformat("2026-06-22T20:00:00+00:00")

    def tearDown(self) -> None:
        ge._now = self.real_now

    def test_weekend_hours_do_not_stale_friday_artifact(self) -> None:
        conn = setup_db("2026-06-19T12:00:00Z")
        try:
            result = ge.evaluate(conn, "INTENT-1")
            gate = next(g for g in result["gates"] if g["name"] == "evidence_freshness")
            self.assertTrue(gate["pass"], gate["detail"])
            self.assertEqual(gate["artifacts"], [])
        finally:
            conn.close()

    def test_stale_artifact_is_attributed_and_persisted(self) -> None:
        conn = setup_db("2026-06-17T12:00:00Z")
        try:
            stale = ge.evaluate(conn, "INTENT-1")
            ge.apply(conn, "INTENT-1", stale)
            blocked_reason = conn.execute(
                "SELECT blocked_reason FROM trade_intents WHERE id='INTENT-1'"
            ).fetchone()[0]
            gate = next(g for g in stale["gates"] if g["name"] == "evidence_freshness")
            artifact = gate["artifacts"][0]
            self.assertEqual(gate["attribution_code"], "stale:EVID-1")
            self.assertEqual(artifact["weekend_hours_discount"], 48.0)
            self.assertEqual(artifact["adjusted_hours_old"], 80.0)
            self.assertTrue(
                blocked_reason.startswith("gates_failed:evidence_freshness[stale:EVID-1]"),
                blocked_reason,
            )
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
