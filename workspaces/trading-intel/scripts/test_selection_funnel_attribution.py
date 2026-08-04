#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import sqlite3
import sys
import unittest
from pathlib import Path

ROOT = Path("/home/aaron/.openclaw")
sys.path.insert(0, str(ROOT / "workspaces/trading-intel/scripts"))
sys.path.insert(0, str(ROOT / "workspaces/developer/scripts"))

spec = importlib.util.spec_from_file_location(
    "selection_funnel_attribution",
    ROOT / "workspaces/developer/scripts/selection_funnel_attribution.py",
)
assert spec and spec.loader
sfa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sfa)


class SelectionFunnelTests(unittest.TestCase):
    def test_preclose_decision_enters_at_same_close_without_using_same_day_return(self) -> None:
        ticker = {
            "2026-07-01": 50.0,  # must not enter the outcome
            "2026-07-02": 1.0,
            "2026-07-06": 1.0,
            "2026-07-07": 1.0,
            "2026-07-08": 1.0,
            "2026-07-09": 1.0,
        }
        spy = {
            "2026-07-01": 100.0,
            "2026-07-02": 100.0,
            "2026-07-06": 100.0,
            "2026-07-07": 100.0,
            "2026-07-08": 100.0,
            "2026-07-09": 100.0,
        }
        outcome = sfa.counterfactual_outcome(
            decision_at="2026-07-01T13:00:00Z",
            direction="long",
            sessions=5,
            ticker_returns=ticker,
            spy_closes=spy,
        )
        self.assertEqual(outcome["entry_date"], "2026-07-01")
        self.assertEqual(outcome["exit_date"], "2026-07-09")
        self.assertAlmostEqual(outcome["raw_return_pct"], 5.101005, places=6)

    def test_after_close_decision_waits_for_next_close(self) -> None:
        dates = {
            "2026-07-01": 99.0,
            "2026-07-02": 99.0,
            "2026-07-06": 1.0,
            "2026-07-07": 1.0,
            "2026-07-08": 1.0,
            "2026-07-09": 1.0,
            "2026-07-10": 1.0,
        }
        spy = {day: 100.0 for day in dates}
        outcome = sfa.counterfactual_outcome(
            decision_at="2026-07-01T21:00:00Z",
            direction="short",
            sessions=5,
            ticker_returns=dates,
            spy_closes=spy,
        )
        self.assertEqual(outcome["entry_date"], "2026-07-02")
        self.assertEqual(outcome["exit_date"], "2026-07-10")
        self.assertAlmostEqual(outcome["directional_excess_pct"], -5.101005, places=6)

    def test_fresh_ticker_close_waiting_on_spy_cache_is_pending_not_blocked(self) -> None:
        outcome = sfa.counterfactual_outcome(
            decision_at="2026-07-31T13:00:00Z",
            direction="long",
            sessions=5,
            ticker_returns={"2026-07-31": 1.0},
            spy_closes={"2026-07-30": 700.0},
        )
        self.assertEqual(outcome["status"], "pending")
        self.assertEqual(outcome["reason"], "awaiting_spy_entry_close")

    def test_historical_spy_entry_gap_remains_data_blocked(self) -> None:
        outcome = sfa.counterfactual_outcome(
            decision_at="2026-07-30T13:00:00Z",
            direction="long",
            sessions=5,
            ticker_returns={"2026-07-30": 1.0},
            spy_closes={"2026-07-29": 699.0, "2026-07-31": 701.0},
        )
        self.assertEqual(outcome["status"], "data_blocked")
        self.assertEqual(outcome["reason"], "missing_spy_entry_close")

    def test_stage_snapshot_rejects_post_entry_critic_lookahead(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            """
            CREATE TABLE critic_reviews(
              target_type TEXT,target_id TEXT,reviewed_at TEXT,reviewed_by TEXT,
              all_challenges_addressed INTEGER
            );
            CREATE TABLE predictions(
              id TEXT,hypothesis_id TEXT,predicted_at TEXT,p_correct REAL,
              return_p50 REAL,evidence_quality REAL,horizon TEXT,mechanism_ids_json TEXT
            );
            CREATE TABLE trade_intents(
              id TEXT,hypothesis_id TEXT,created_at TEXT,state TEXT,direction TEXT,
              size REAL,blocked_reason TEXT,action TEXT
            );
            CREATE TABLE risk_reviews(
              target_type TEXT,target_id TEXT,reviewed_at TEXT,verdict TEXT,
              approved_size REAL,breaches_json TEXT
            );
            CREATE TABLE orders(
              trade_intent_id TEXT,filled_at TEXT,avg_fill_price REAL,qty REAL,status TEXT
            );
            """
        )
        conn.executemany(
            "INSERT INTO critic_reviews VALUES('hypothesis','h',?,'critic',?)",
            [
                ("2026-07-01T14:00:00Z", 0),
                ("2026-07-01T21:00:00Z", 1),
            ],
        )
        hyp = {
            "id": "h", "created_by": "researcher", "tickers": '["ABC"]',
            "quant_score": 70.0, "scored_at": "2026-07-01T13:30:00Z",
        }
        snapshot = sfa.stage_snapshot(conn, hyp, "2026-07-01")
        self.assertEqual(snapshot["quant_scored"], 1)
        self.assertEqual(snapshot["critic_substantive_passed"], 0)

    def test_matured_outcome_is_immutable(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            (ROOT / "workspaces/trading-intel/sql/schema.sql").read_text()
            + (ROOT / "workspaces/trading-intel/sql/migrations/0024_selection_funnel_attribution.sql").read_text()
        )
        base = {
            "id": "sfo-1", "hypothesis_id": "h", "ticker": "ABC", "direction": "long",
            "evaluation_horizon": "5d", "sessions": 5, "decision_at": "2026-07-01T13:00:00Z",
            "entry_date": "2026-07-01", "exit_date": "2026-07-09", "outcome_status": "matured",
            "data_reason": None, "raw_return_pct": 2.0, "spy_return_pct": 1.0,
            "directional_excess_pct": 1.0, "quant_scored": 1, "critic_passed": 1,
            "critic_substantive_passed": 1, "predicted": 1, "intent_authored": 0,
            "risk_approved": 0, "filled": 0, "stage_snapshot_json": "{}",
            "computed_at": "2026-07-10T00:00:00Z",
        }
        conn.execute(
            "INSERT INTO hypotheses(id,created_at,created_by,tickers,thesis_summary,state) "
            "VALUES('h','2026-07-01T13:00:00Z','researcher','[\"ABC\"]','test','resolved')"
        )
        self.assertEqual(sfa.write_rows(conn, [base])["written"], 1)
        changed = dict(base)
        changed["raw_return_pct"] = 3.0
        report = sfa.write_rows(conn, [changed])
        self.assertEqual(report["matured_revision_refused"], 1)
        self.assertEqual(report["revision_samples"], ["h:ABC:5d"])
        frozen = conn.execute(
            "SELECT raw_return_pct FROM selection_funnel_outcomes WHERE id='sfo-1'"
        ).fetchone()[0]
        self.assertEqual(frozen, 2.0)

    def test_report_distinguishes_latency_and_only_labels_negative_harm(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE selection_funnel_outcomes("
            "evaluation_horizon TEXT,outcome_status TEXT,entry_date TEXT,"
            "hypothesis_id TEXT,directional_excess_pct REAL,quant_scored INTEGER,"
            "critic_substantive_passed INTEGER,predicted INTEGER,intent_authored INTEGER,"
            "risk_approved INTEGER,filled INTEGER,stage_snapshot_json TEXT)"
        )
        conn.execute("CREATE TABLE hypotheses(id TEXT,quant_score REAL,scored_at TEXT)")
        for index in range(20):
            selected = index < 10
            conn.execute(
                "INSERT INTO selection_funnel_outcomes VALUES"
                "('5d','matured',?,?,?,?,?,?,?,?,?,?)",
                (
                    f"2026-07-{index % 10 + 1:02d}", f"h{index}",
                    -1.0 if selected else (1.0 if index < 15 else 2.0),
                    int(selected), int(selected),
                    int(selected), int(selected), int(selected), int(selected),
                    '{"created_by":"researcher"}',
                ),
            )
            if selected or index < 15:
                conn.execute(
                    "INSERT INTO hypotheses VALUES(?,?,?)",
                    (f"h{index}", 70.0, "2026-07-20T00:00:00Z"),
                )
            else:
                conn.execute("INSERT INTO hypotheses VALUES(?,NULL,NULL)", (f"h{index}",))
        report = sfa.funnel_report(conn, "5d")
        quant = next(stage for stage in report["stages"] if stage["stage"] == "quant_scored")
        self.assertIn("not_selected_at_cutoff", quant)
        self.assertNotIn("rejected", quant)
        self.assertEqual(quant["reached_after_cutoff"]["n"], 5)
        self.assertEqual(quant["not_reached_as_of_report"]["n"], 5)
        self.assertEqual(quant["latency_spread_bps"], -200.0)
        self.assertEqual(quant["selection_spread_bps"], -300.0)
        self.assertTrue(quant["selection_inference_eligible"])
        self.assertEqual(report["most_harmful_measured_stage"]["stage"], "quant_scored")
        self.assertIsNone(report["most_helpful_measured_stage"])

    def test_single_entry_date_cannot_rank_a_stage(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE selection_funnel_outcomes("
            "evaluation_horizon TEXT,outcome_status TEXT,entry_date TEXT,"
            "hypothesis_id TEXT,directional_excess_pct REAL,quant_scored INTEGER,"
            "critic_substantive_passed INTEGER,predicted INTEGER,intent_authored INTEGER,"
            "risk_approved INTEGER,filled INTEGER,stage_snapshot_json TEXT)"
        )
        conn.execute("CREATE TABLE hypotheses(id TEXT,quant_score REAL,scored_at TEXT)")
        for index in range(12):
            selected = index < 6
            conn.execute(
                "INSERT INTO selection_funnel_outcomes VALUES"
                "('21d','matured','2026-07-01',?,?,?,?,?,?,?,?,?)",
                (
                    f"h{index}", -5.0 if selected else 5.0, int(selected), int(selected),
                    int(selected), int(selected), int(selected), int(selected),
                    '{"created_by":"researcher"}',
                ),
            )
            conn.execute("INSERT INTO hypotheses VALUES(?,NULL,NULL)", (f"h{index}",))
        report = sfa.funnel_report(conn, "21d")
        quant = next(stage for stage in report["stages"] if stage["stage"] == "quant_scored")
        self.assertEqual(quant["selection_spread_bps"], -1000.0)
        self.assertFalse(quant["selection_inference_eligible"])
        self.assertIsNone(report["most_harmful_measured_stage"])


if __name__ == "__main__":
    unittest.main()
