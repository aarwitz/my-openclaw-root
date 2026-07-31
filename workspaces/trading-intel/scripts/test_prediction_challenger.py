#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sqlite3
import unittest
from pathlib import Path

ROOT = Path("/home/aaron/.openclaw")
spec = importlib.util.spec_from_file_location(
    "prediction_challenger",
    ROOT / "workspaces/developer/scripts/prediction_challenger.py",
)
assert spec and spec.loader
challenger = importlib.util.module_from_spec(spec)
spec.loader.exec_module(challenger)


class PredictionChallengerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript((ROOT / "workspaces/trading-intel/sql/schema.sql").read_text())
        self.cfg = {
            "experiment_id": "test-forward",
            "protocol_version": "v1",
            "start_at": "2026-08-03T00:00:00Z",
            "minimum_resolved_predictions": 100,
            "minimum_trading_sessions": 60,
            "variants": {
                "champion_v1": "champion", "base_rate": "base", "shrink75_to_base": "shrink"
            },
            "promotion_rule": "never early",
            "trading_authority": "none",
        }
        self.conn.execute(
            "INSERT INTO hypotheses(id,created_at,created_by,tickers,thesis_summary,state) "
            "VALUES('h','2026-08-03T12:00:00Z','researcher','[\"ABC\"]','Long ABC','ready')"
        )
        self.conn.execute(
            "INSERT INTO experiments(id,started_at,scope,hypothesis,decided_by) "
            "VALUES('test-forward','2026-08-03T00:00:00Z','prediction_calibration_shadow',"
            "'test protocol','developer')"
        )
        self.conn.execute(
            "INSERT INTO predictions(id,hypothesis_id,predicted_at,predicted_by,horizon,p_correct) "
            "VALUES('p','h','2026-08-03T14:00:00Z','quant','swing_1_5d',0.70)"
        )

    def test_record_is_paired_and_grade_is_shadow_only(self) -> None:
        result = challenger.record(self.conn, self.cfg)
        self.assertEqual(result["written"], 3)
        probabilities = dict(self.conn.execute(
            "SELECT variant,p_correct FROM prediction_challengers ORDER BY variant"
        ))
        self.assertAlmostEqual(probabilities["champion_v1"], 0.70)
        self.assertAlmostEqual(probabilities["base_rate"], 0.466)
        self.assertAlmostEqual(probabilities["shrink75_to_base"], 0.5245)
        self.conn.execute(
            "UPDATE predictions SET realized_outcome='correct',realized_excess_pct=2.5,"
            "resolved_at='2026-08-10T00:00:00Z' WHERE id='p'"
        )
        self.assertEqual(challenger.grade(self.conn, self.cfg)["graded"], 3)
        report = challenger.report(self.conn, self.cfg)
        self.assertEqual(report["resolved_predictions"], 1)
        self.assertFalse(report["eligible_for_decision"])
        self.assertAlmostEqual(report["variants"]["champion_v1"]["mean_brier"], 0.09)
        # The shadow experiment must not alter the canonical prediction.
        self.assertAlmostEqual(self.conn.execute("SELECT p_correct FROM predictions").fetchone()[0], 0.70)


if __name__ == "__main__":
    unittest.main()
