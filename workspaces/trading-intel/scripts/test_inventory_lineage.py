#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path("/home/aaron/.openclaw")
sys.path.insert(0, str(ROOT / "workspaces/developer/scripts"))
import inventory_lineage  # noqa: E402


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE meta(key TEXT,value TEXT);
        CREATE TABLE positions(id TEXT,hypothesis_id TEXT,ticker TEXT,qty REAL,
          current_value REAL,opened_at TEXT,book TEXT,state TEXT);
        CREATE TABLE trade_intents(id TEXT,hypothesis_id TEXT,ticker TEXT,action TEXT,created_at TEXT);
        CREATE TABLE orders(trade_intent_id TEXT,status TEXT,filled_at TEXT);
        CREATE TABLE predictions(id TEXT,hypothesis_id TEXT,predicted_at TEXT);
        CREATE TABLE critic_reviews(id TEXT,target_type TEXT,target_id TEXT,reviewed_by TEXT,
          all_challenges_addressed INTEGER,reviewed_at TEXT);
        CREATE TABLE risk_reviews(id TEXT,target_type TEXT,target_id TEXT,verdict TEXT,reviewed_at TEXT);
        INSERT INTO meta VALUES('_prediction_lineage_cutover','2026-08-02T10:00:00Z');
    """)
    return conn


def _position(conn, suffix: str, intent_at: str, *, complete: bool) -> None:
    hyp, intent = f"h-{suffix}", f"ti-{suffix}"
    conn.execute(
        "INSERT INTO positions VALUES(?,?,?,?,?,?,?,?)",
        (f"p-{suffix}", hyp, suffix.upper(), 1.0, 100.0, intent_at, "desk", "open"),
    )
    conn.execute("INSERT INTO trade_intents VALUES(?,?,?,?,?)", (intent, hyp, suffix.upper(), "open", intent_at))
    fill_at = "2026-08-03T10:04:00Z" if complete else intent_at
    conn.execute("INSERT INTO orders VALUES(?,?,?)", (intent, "filled", fill_at))
    if complete:
        conn.execute("INSERT INTO predictions VALUES(?,?,?)", (f"pred-{suffix}", hyp, "2026-08-03T10:00:00Z"))
        conn.execute(
            "INSERT INTO critic_reviews VALUES(?,?,?,?,?,?)",
            (f"cr-{suffix}", "hypothesis", hyp, "critic", 1, "2026-08-03T10:01:00Z"),
        )
        conn.execute(
            "INSERT INTO risk_reviews VALUES(?,?,?,?,?)",
            (f"rr-{suffix}", "trade_intent", intent, "approved", "2026-08-03T10:03:00Z"),
        )


class InventoryLineageTests(unittest.TestCase):
    def test_legacy_is_honest_and_post_cutover_gap_is_violation(self) -> None:
        conn = _conn()
        _position(conn, "legacy", "2026-08-01T12:00:00Z", complete=False)
        _position(conn, "modern", "2026-08-03T10:02:00Z", complete=True)
        _position(conn, "broken", "2026-08-03T11:00:00Z", complete=False)
        report = inventory_lineage.build_report(conn)
        self.assertEqual(report["status_counts"], {
            "modern_lineage": 1,
            "legacy_pre_cutover": 1,
            "post_cutover_violation": 1,
        })
        legacy = next(row for row in report["positions"] if row["ticker"] == "LEGACY")
        self.assertIn("prediction_before_intent", legacy["gaps"])
        self.assertEqual(report["modern_lineage_pct"], 33.33)

    def test_orphaned_position_uses_opened_at_only_to_classify_legacy_age(self) -> None:
        conn = _conn()
        conn.execute(
            "INSERT INTO positions VALUES(?,?,?,?,?,?,?,?)",
            ("p-orphan", "h-orphan", "OLD", 1.0, 50.0,
             "2026-08-01T12:00:00Z", "desk", "open"),
        )
        report = inventory_lineage.build_report(conn)
        row = report["positions"][0]
        self.assertEqual(row["status"], "legacy_pre_cutover")
        self.assertIn("opening_intent", row["gaps"])
        self.assertIn("opening_fill", row["gaps"])

    def test_broken_post_cutover_add_taints_legacy_position(self) -> None:
        conn = _conn()
        _position(conn, "legacy", "2026-08-01T12:00:00Z", complete=False)
        conn.execute(
            "INSERT INTO trade_intents VALUES(?,?,?,?,?)",
            ("ti-add", "h-legacy", "LEGACY", "add", "2026-08-03T12:00:00Z"),
        )
        conn.execute(
            "INSERT INTO orders VALUES(?,?,?)",
            ("ti-add", "filled", "2026-08-03T12:05:00Z"),
        )
        row = inventory_lineage.build_report(conn)["positions"][0]
        self.assertEqual(row["status"], "post_cutover_violation")
        self.assertEqual(row["post_cutover_fill_count"], 1)
        self.assertEqual(row["post_cutover_violations"][0]["intent_id"], "ti-add")

    def test_risk_approval_after_fill_does_not_validate_lineage(self) -> None:
        conn = _conn()
        _position(conn, "late", "2026-08-03T10:02:00Z", complete=True)
        conn.execute(
            "UPDATE risk_reviews SET reviewed_at='2026-08-03T10:05:00Z' "
            "WHERE target_id='ti-late'"
        )
        row = inventory_lineage.build_report(conn)["positions"][0]
        self.assertEqual(row["status"], "post_cutover_violation")
        self.assertIn("risk_approval_before_fill", row["gaps"])

    def test_missing_cutover_fails_closed(self) -> None:
        conn = _conn()
        conn.execute("DELETE FROM meta WHERE key='_prediction_lineage_cutover'")
        _position(conn, "modern", "2026-08-03T10:02:00Z", complete=True)
        report = inventory_lineage.build_report(conn)
        self.assertFalse(report["cutover_present"])
        self.assertEqual(report["positions"][0]["status"], "post_cutover_violation")


if __name__ == "__main__":
    unittest.main()
