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


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute(
        "CREATE TABLE trade_intents (id TEXT, action TEXT, state TEXT, created_at TEXT, blocked_reason TEXT)"
    )
    cur.execute(
        "CREATE TABLE predictions (resolved_at TEXT, brier_component REAL, realized_outcome TEXT, predicted_at TEXT)"
    )
    return conn


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

    def test_format_blocked_reason_carries_freshness_attribution(self):
        result = {
            "failed_gates": ["evidence_freshness"],
            "gates": [
                {
                    "name": "evidence_freshness",
                    "pass": False,
                    "detail": "evidence=1 stale=1 max_h=72.0",
                    "attribution_code": "stale:EVID-1",
                },
            ],
        }
        blocked_reason = gate_evaluator._format_blocked_reason(result)
        self.assertIn("gates_failed:evidence_freshness[stale:EVID-1]", blocked_reason)
        self.assertLessEqual(len(blocked_reason), 240)

    def test_normalize_strips_per_artifact_freshness_detail(self):
        reason = "gates_failed:evidence_freshness[stale:EVID-1|missing_ts:EVID-2],provenance_completeness"
        self.assertEqual(
            drag_report.normalize_block_reason(reason),
            "gates_failed:evidence_freshness,provenance_completeness",
        )

    def test_parse_failed_gates_strips_attribution_brackets(self):
        reason = "gates_failed:evidence_freshness[stale:EVID-1],counterargument_quality|inputs=x=1"
        self.assertEqual(
            drag_report.parse_failed_gates(reason),
            ["evidence_freshness", "counterargument_quality"],
        )

    def test_collect_signals_groups_attributed_freshness_blocks_into_one_class(self):
        conn = _make_conn()
        cur = conn.cursor()
        rows = [
            ("TI-1", "open", "blocked", "2099-01-01T00:00:00Z", "gates_failed:evidence_freshness[stale:EVID-1]"),
            ("TI-2", "open", "blocked", "2099-01-01T00:00:00Z", "gates_failed:evidence_freshness[missing_ts:EVID-2]"),
            ("TI-3", "open", "blocked", "2099-01-01T00:00:00Z", "gates_failed:evidence_freshness[stale:EVID-3|missing_ts:EVID-4]"),
        ]
        cur.executemany("INSERT INTO trade_intents VALUES (?, ?, ?, ?, ?)", rows)
        conn.commit()

        original_evaluate = drag_report.gate_evaluator.evaluate
        drag_report.gate_evaluator.evaluate = lambda _conn, intent_id: {
            "all_pass": False,
            "next_state": "blocked",
            "failed_gates": ["evidence_freshness"],
            "gates": [
                {"name": "evidence_freshness", "pass": False, "detail": f"intent={intent_id} stale evidence"},
            ],
        }
        try:
            signals = drag_report.collect_signals(conn.cursor())
        finally:
            drag_report.gate_evaluator.evaluate = original_evaluate
            conn.close()

        blocked = next((s for s in signals if s["id"] == "blocked-gates-failed-evidence-freshness"), None)
        self.assertIsNotNone(blocked, "attributed freshness blocks must group into one recurring class")
        self.assertIn("count=3", blocked["evidence"][0])

    def test_collect_signals_marks_legacy_exit_blocks_non_active(self):
        conn = _make_conn()
        cur = conn.cursor()
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
        conn = _make_conn()
        cur = conn.cursor()
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
