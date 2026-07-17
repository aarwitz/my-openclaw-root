#!/usr/bin/env python3
import sqlite3
import sys
import unittest

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")

import drag_report  # noqa: E402
import gate_evaluator  # noqa: E402


LEGACY_REASON = (
    "gates_failed:evidence_freshness,provenance_completeness,"
    "counterargument_quality,stop_rule_present"
)


class GateAttributionTests(unittest.TestCase):
    def test_format_blocked_reason_includes_gate_inputs(self):
        result = {
            "failed_gates": ["evidence_freshness", "counterargument_quality"],
            "gates": [
                {"name": "evidence_freshness", "pass": False, "detail": "evidence=0 stale=0 max_h=72.0"},
                {"name": "counterargument_quality", "pass": False, "detail": "no_critic_review"},
            ],
        }
        blocked_reason = gate_evaluator._format_blocked_reason(result)
        self.assertIn("gates_failed:evidence_freshness,counterargument_quality", blocked_reason)
        self.assertIn("|inputs=", blocked_reason)
        self.assertIn("evidence_freshness=evidence=0stale=0max_h=72.0", blocked_reason)
        self.assertIn("counterargument_quality=no_critic_review", blocked_reason)

    def test_collect_signals_marks_legacy_exit_blocks_non_active(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE trade_intents (id TEXT, action TEXT, state TEXT, created_at TEXT, blocked_reason TEXT)"
        )
        cur.execute(
            "CREATE TABLE predictions (resolved_at TEXT, brier_component REAL, realized_outcome TEXT, predicted_at TEXT)"
        )
        for idx in range(3):
            cur.execute(
                "INSERT INTO trade_intents VALUES (?, 'exit', 'blocked', '2099-01-01T00:00:00Z', ?)",
                (f"ti-exit-{idx}", LEGACY_REASON),
            )
        conn.commit()

        original_evaluate = drag_report.gate_evaluator.evaluate
        drag_report.gate_evaluator.evaluate = lambda _conn, _intent_id: {
            "all_pass": True,
            "next_state": "risk_review",
            "failed_gates": [],
            "gates": [],
        }
        try:
            signals = drag_report.collect_signals(conn.cursor())
        finally:
            drag_report.gate_evaluator.evaluate = original_evaluate
            conn.close()

        summaries = [signal["summary"] for signal in signals]
        self.assertFalse(any("same class: gates_failed:evidence_freshness,provenance_completeness,counterargument_quality,stop_rule_present" in s for s in summaries))
        self.assertTrue(any("legacy false positives" in s for s in summaries))

    def test_collect_signals_keeps_active_open_blocks_grouped(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        cur.execute(
            "CREATE TABLE trade_intents (id TEXT, action TEXT, state TEXT, created_at TEXT, blocked_reason TEXT)"
        )
        cur.execute(
            "CREATE TABLE predictions (resolved_at TEXT, brier_component REAL, realized_outcome TEXT, predicted_at TEXT)"
        )
        for idx in range(3):
            cur.execute(
                "INSERT INTO trade_intents VALUES (?, 'open', 'blocked', '2099-01-01T00:00:00Z', ?)",
                (f"ti-open-{idx}", LEGACY_REASON),
            )
        conn.commit()

        original_evaluate = drag_report.gate_evaluator.evaluate
        drag_report.gate_evaluator.evaluate = lambda _conn, intent_id: {
            "all_pass": False,
            "next_state": "blocked",
            "failed_gates": ["evidence_freshness", "counterargument_quality"],
            "gates": [
                {"name": "evidence_freshness", "pass": False, "detail": f"intent={intent_id} evidence=0 stale=0 max_h=72.0"},
                {"name": "counterargument_quality", "pass": False, "detail": "no_critic_review"},
            ],
        }
        try:
            signals = drag_report.collect_signals(conn.cursor())
        finally:
            drag_report.gate_evaluator.evaluate = original_evaluate
            conn.close()

        summaries = [signal["summary"] for signal in signals]
        self.assertTrue(any("same class: gates_failed:evidence_freshness,counterargument_quality" in s for s in summaries))
        matching = next(signal for signal in signals if "same class: gates_failed:evidence_freshness,counterargument_quality" in signal["summary"])
        self.assertTrue(any("counterargument_quality -> no_critic_review" in item for item in matching["evidence"]))


if __name__ == "__main__":
    unittest.main()
