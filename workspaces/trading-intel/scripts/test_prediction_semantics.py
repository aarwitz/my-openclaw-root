#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path("/home/aaron/.openclaw")
sys.path.insert(0, str(ROOT / "workspaces/trading-intel/scripts"))
sys.path.insert(0, str(ROOT / "workspaces/quant/scripts"))
import integrity_check
import predict
import worldmodel


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


calibrate = _load("calibrate_semantics", ROOT / "workspaces/archivist/scripts/calibrate.py")
prediction_replay = _load(
    "prediction_replay_semantics",
    ROOT / "workspaces/developer/scripts/prediction_replay.py",
)


class PredictionDirectionSemanticsTests(unittest.TestCase):
    def test_shared_direction_and_excess_semantics(self) -> None:
        self.assertEqual(worldmodel.thesis_direction("Long ABC"), "long")
        self.assertEqual(worldmodel.thesis_direction("Bearish setup in ABC"), "short")
        self.assertEqual(worldmodel.directional_excess_pct(-4.0, "short"), 4.0)
        self.assertEqual(worldmodel.directional_excess_pct(4.0, "long"), 4.0)

    def test_predictor_freezes_direction_and_source_policy_fingerprint(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript((ROOT / "workspaces/trading-intel/sql/schema.sql").read_text())
        conn.execute(
            "INSERT INTO hypotheses(id,created_at,created_by,tickers,thesis_summary,state) "
            "VALUES('h','2026-08-03T12:00:00Z','researcher','[\"ABC\"]',"
            "'Short ABC on a test catalyst','ready')"
        )
        hypothesis = (
            "h", "2026-08-03T12:00:00Z", "Short ABC on a test catalyst",
            "position_1_4w", "ready", '["ABC"]',
        )
        result = predict.predict_one(
            conn, hypothesis, [], {}, "neutral", "test-policy-lineage", False
        )
        row = conn.execute(
            "SELECT thesis_direction,prediction_policy_version,prediction_policy_hash "
            "FROM predictions WHERE id=?",
            (result["id"],),
        ).fetchone()
        self.assertEqual(row[0], "short")
        self.assertEqual(row[1], predict.PREDICTION_POLICY_VERSION)
        self.assertEqual(len(row[2]), 64)
        self.assertEqual(row[2], predict.prediction_policy_hash())
        lineage = integrity_check.prediction_lineage(conn)
        self.assertEqual(lineage["status"], "OK")
        conn.execute(
            "INSERT INTO predictions(id,hypothesis_id,predicted_at,predicted_by,horizon,"
            "p_correct,thesis_direction) VALUES('rogue','h','2026-08-03T12:00:00Z',"
            "'quant','position_1_4w',0.5,'short')"
        )
        lineage = integrity_check.prediction_lineage(conn)
        self.assertEqual(lineage["status"], "RED")

    def test_health_uses_thesis_aligned_returns_for_shorts(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            "CREATE TABLE hypotheses(id TEXT PRIMARY KEY,thesis_summary TEXT);"
            "CREATE TABLE predictions(hypothesis_id TEXT,p_correct REAL,"
            "realized_excess_pct REAL,resolved_at TEXT,thesis_direction TEXT);"
            "CREATE TABLE attribution(realized_edge_vs_spy_bps REAL,closed_at TEXT);"
        )
        for index in range(20):
            probability = 0.51 + index * 0.01
            conn.execute("INSERT INTO hypotheses VALUES(?,?)", (f"h{index}", "Short ABC"))
            conn.execute(
                "INSERT INTO predictions VALUES(?,?,?,'2026-08-01T00:00:00Z','short')",
                (f"h{index}", probability, -probability * 10.0),
            )
        signal = next(item for item in integrity_check.edge(conn) if item["id"] == "edge:conviction_predicts")
        self.assertEqual(signal["status"], "OK")
        self.assertIn("directional excess", signal["detail"])

    def test_mechanism_expectancy_direction_adjusts_supporting_shorts(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.executescript(
            "CREATE TABLE mechanisms(id TEXT PRIMARY KEY,name TEXT);"
            "CREATE TABLE hypotheses(id TEXT PRIMARY KEY,thesis_summary TEXT);"
            "CREATE TABLE predictions(id TEXT PRIMARY KEY,hypothesis_id TEXT,realized_excess_pct REAL,thesis_direction TEXT);"
            "CREATE TABLE mechanism_observations(mechanism_id TEXT,source_type TEXT,source_id TEXT,"
            "outcome TEXT,notes TEXT);"
            "INSERT INTO mechanisms VALUES('m','test mechanism');"
            "INSERT INTO hypotheses VALUES('hl','Long ABC'),('hs1','Short DEF'),('hs2','Bearish GHI');"
            "INSERT INTO predictions VALUES('pl','hl',2.0,'long'),('ps1','hs1',-3.0,'short'),('ps2','hs2',-4.0,'short');"
            "INSERT INTO mechanism_observations VALUES"
            "('m','prediction','pl','hit','align=1'),"
            "('m','prediction','ps1','hit','align=1'),"
            "('m','prediction','ps2','hit','align=1');"
        )
        result = calibrate.mechanism_expectancy(conn)
        self.assertEqual(result[0]["n"], 3)
        self.assertAlmostEqual(result[0]["expectancy_pct"], 3.0)


class RetrospectivePredictionReplayTests(unittest.TestCase):
    def test_replay_is_frozen_read_only_and_base_rate_beats_bad_champion(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            "CREATE TABLE hypotheses(id TEXT PRIMARY KEY,thesis_summary TEXT);"
            "CREATE TABLE predictions(id TEXT PRIMARY KEY,hypothesis_id TEXT,predicted_at TEXT,"
            "resolved_at TEXT,horizon TEXT,p_correct REAL,realized_outcome TEXT,"
            "realized_excess_pct REAL,brier_component REAL,mechanism_ids_json TEXT,"
            "thesis_direction TEXT,prediction_policy_version TEXT,prediction_policy_hash TEXT);"
        )
        for index in range(10):
            short = index >= 8
            correct = index % 2 == 0
            thesis = "Short ABC" if short else "Long ABC"
            raw_excess = (-2.0 if correct else 2.0) if short else (2.0 if correct else -2.0)
            outcome_bit = 1.0 if correct else 0.0
            probability = 0.8
            conn.execute("INSERT INTO hypotheses VALUES(?,?)", (f"h{index}", thesis))
            conn.execute(
                "INSERT INTO predictions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"p{index}", f"h{index}", f"2026-07-{index + 1:02d}T12:00:00Z",
                    f"2026-07-{index + 2:02d}T00:00:00Z", "position_1_4w", probability,
                    "correct" if correct else "incorrect", raw_excess,
                    (probability - outcome_bit) ** 2, "[]", "short" if short else "long",
                    "test-v1", f"hash-{index}",
                ),
            )
        before = conn.total_changes
        report = prediction_replay.build_report(conn)
        self.assertTrue(report["ok"])
        self.assertTrue(report["retrospective_diagnostic_only"])
        self.assertFalse(report["database_mutations"])
        self.assertEqual(conn.total_changes, before)
        self.assertEqual(report["cohort"]["n"], 10)
        self.assertEqual(report["cohort"]["directions"], {"long": 8, "short": 2})
        self.assertEqual(report["retrospective_finding"], "direction_base_rate_better")
        self.assertLess(
            report["variants"]["direction_base_rate"]["mean_brier"],
            report["variants"]["recorded_champion"]["mean_brier"],
        )


if __name__ == "__main__":
    unittest.main()
