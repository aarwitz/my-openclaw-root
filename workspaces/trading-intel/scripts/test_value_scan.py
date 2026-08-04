#!/usr/bin/env python3
import json
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import value_scan


class ValueScanLiveThesisTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript("""
            CREATE TABLE hypotheses(id TEXT PRIMARY KEY,state TEXT,tickers TEXT);
            CREATE TABLE hypothesis_evidence(
              id TEXT PRIMARY KEY,hypothesis_id TEXT,indicator TEXT,value TEXT,
              source TEXT,retrieved_at TEXT,signal_type TEXT
            );
            CREATE TABLE falsifier_signals(
              id TEXT PRIMARY KEY,hypothesis_id TEXT,condition TEXT,
              monitor_frequency TEXT,current_status TEXT,updated_at TEXT,source_ref TEXT
            );
        """)

    def tearDown(self) -> None:
        self.conn.close()

    def test_live_map_has_no_age_cutoff(self) -> None:
        self.conn.executemany(
            "INSERT INTO hypotheses VALUES(?,?,?)",
            [
                ("old-live", "scored", json.dumps(["OLD"])),
                ("history", "resolved", json.dumps(["DONE"])),
            ],
        )
        self.assertEqual(value_scan._live_hypotheses(self.conn), {"OLD": "old-live"})

    def test_evidence_and_falsifier_refresh_are_idempotent(self) -> None:
        value_scan._upsert_evidence(self.conn, "h", "margin_of_safety", "0.30")
        value_scan._upsert_evidence(self.conn, "h", "margin_of_safety", "0.31")
        value_scan._ensure_falsifier(self.conn, "h", "gap closes")
        value_scan._ensure_falsifier(self.conn, "h", "gap closes")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM hypothesis_evidence").fetchone()[0], 1)
        self.assertEqual(self.conn.execute("SELECT value FROM hypothesis_evidence").fetchone()[0], "0.31")
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM falsifier_signals").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
