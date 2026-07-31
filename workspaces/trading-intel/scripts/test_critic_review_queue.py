#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path("/home/aaron/.openclaw")
spec = importlib.util.spec_from_file_location(
    "critic_review_queue",
    ROOT / "workspaces/critic/scripts/critic_review_queue.py",
)
assert spec and spec.loader
queue = importlib.util.module_from_spec(spec)
spec.loader.exec_module(queue)


class CriticReviewQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript((ROOT / "workspaces/trading-intel/sql/schema.sql").read_text())
        self.conn.execute(
            "INSERT INTO hypotheses(id,created_at,created_by,tickers,thesis_summary,state,"
            "quant_score,scored_at,rationale_concise) VALUES"
            "('h','2026-07-31T12:00:00Z','researcher','[\"ABC\"]','Long ABC','scored',"
            "70,'2026-07-31T13:00:00Z','A sufficiently developed research rationale for ABC.')"
        )

    def _review(self, review_id: str, reviewed_at: str, addressed: bool, challenges: list[dict]) -> None:
        self.conn.execute(
            "INSERT INTO critic_reviews VALUES(?, 'hypothesis','h',?,'critic',?,?)",
            (review_id, reviewed_at, json.dumps(challenges), int(addressed)),
        )

    def test_stale_review_does_not_remove_rescored_candidate(self) -> None:
        self._review(
            "old", "2026-07-31T12:30:00Z", True,
            [
                {"challenge": "First concrete counterargument", "response": "x" * 40, "resolved": True},
                {"challenge": "Second concrete counterargument", "response": "y" * 40, "resolved": True},
            ],
        )
        self.assertEqual([row["hypothesis_id"] for row in queue.candidates(self.conn)], ["h"])

    def test_valid_addressed_review_promotes_mechanically(self) -> None:
        self._review(
            "new", "2026-07-31T13:30:00Z", True,
            [
                {"challenge": "First concrete counterargument", "response": "x" * 40, "resolved": True},
                {"challenge": "Second concrete counterargument", "response": "y" * 40, "resolved": True},
            ],
        )
        result = queue.finalize(self.conn, apply=True)
        self.assertFalse(result["invalid"])
        self.assertEqual(self.conn.execute("SELECT state FROM hypotheses").fetchone()[0], "ready")
        self.assertEqual(
            self.conn.execute("SELECT action FROM audits ORDER BY timestamp DESC LIMIT 1").fetchone()[0],
            "finalize_critic_review",
        )

    def test_malformed_review_fails_closed(self) -> None:
        self._review(
            "bad", "2026-07-31T13:30:00Z", True,
            [{"challenge": "Only one counterargument", "response": "x", "resolved": True}],
        )
        result = queue.finalize(self.conn, apply=True)
        self.assertEqual(len(result["invalid"]), 1)
        self.assertEqual(self.conn.execute("SELECT state FROM hypotheses").fetchone()[0], "scored")


if __name__ == "__main__":
    unittest.main()
