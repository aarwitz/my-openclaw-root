#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path("/home/aaron/.openclaw")
sys.path.insert(0, str(ROOT / "workspaces/developer/scripts"))

import validation_corpus  # noqa: E402


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""CREATE TABLE validation_cases(
        id TEXT PRIMARY KEY, masked_case_json TEXT NOT NULL, case_class TEXT NOT NULL,
        fake_date_variant TEXT, model_decision_json TEXT, resolved_outcome_json TEXT,
        passed INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, experiment_id TEXT)""")
    conn.execute("""CREATE TABLE audits(
        id TEXT PRIMARY KEY,timestamp TEXT,actor TEXT,entity_type TEXT,entity_id TEXT,
        action TEXT,before_state TEXT,after_state TEXT,rationale_concise TEXT,
        journal_ref TEXT,experiment_id TEXT)""")
    return conn


def _case(cid: str, case_class: str, *, decision: str = "open", direction: str = "long",
          expected_decision: str = "open", expected_direction: str = "long",
          fake_date_variant: str | None = None, passed: int = 1,
          masked_override: dict | None = None) -> tuple:
    masked = masked_override or {
        "world_change": "an anonymized issuer reports a durable demand inflection",
        "sector_or_theme": "industrial demand",
        "structural_features": ["raised outlook", "capacity constraint"],
        "primary_source_class": "regulatory filing",
    }
    rationale = hashlib.sha256(cid.encode()).hexdigest()
    model = {
        "decision": decision, "direction": direction, "confidence": 1.0 if passed else 0.0,
        "rationale_hash": f"sha256:{rationale}",
        "model_id": "fixture-model",
        "policy_hash": "sha256:" + hashlib.sha256(b"fixture-policy").hexdigest(),
        "masked_case_hash": validation_corpus._masked_hash(masked),
        "decided_at": "2026-07-31T11:00:00Z",
        "knowledge_cutoff": "2026-06-01T00:00:00Z",
    }
    outcome = {
        "outcome": "thesis_confirmed", "horizon_days": 21,
        "external_mechanism_check": "forward relative return cleared the frozen threshold",
        "expected_decision": expected_decision, "expected_direction": expected_direction,
        "resolved_at": "2026-08-01T20:00:00Z",
    }
    return (
        cid, json.dumps(masked), case_class, fake_date_variant, json.dumps(model),
        json.dumps(outcome), passed, "2026-07-31T12:00:00Z", "test-policy-v1",
    )


def _insert(conn: sqlite3.Connection, row: tuple) -> None:
    conn.execute("INSERT INTO validation_cases VALUES(?,?,?,?,?,?,?,?,?)", row)


class ValidationCorpusTests(unittest.TestCase):
    def _batch_file(self, payload: dict) -> Path:
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        with handle:
            json.dump(payload, handle)
        self.addCleanup(Path(handle.name).unlink, missing_ok=True)
        return Path(handle.name)

    def test_empty_corpus_is_honest_not_broken(self) -> None:
        report = validation_corpus.audit_corpus(_conn())
        self.assertTrue(report["structural_ok"])
        self.assertFalse(report["reasoning_gate"])
        self.assertEqual(report["eligible_resolved_counts"]["post_cutoff"], 0)

    def test_unresolved_rows_cannot_game_sample_threshold(self) -> None:
        conn = _conn()
        for idx in range(60):
            row = list(_case(f"vc_negative_control_pending_case_{idx:03d}", "negative_control"))
            row[5] = None
            row[6] = 0
            _insert(conn, tuple(row))
        report = validation_corpus.audit_corpus(conn)
        self.assertTrue(report["structural_ok"])
        self.assertEqual(report["pending_rows"], 60)
        self.assertEqual(report["eligible_resolved_counts"]["negative_control"], 0)

    def test_resolved_post_cutoff_requires_invariant_fake_date_pair(self) -> None:
        conn = _conn()
        base_id = "vc_post_cutoff_demand_inflection_001"
        _insert(conn, _case(base_id, "post_cutoff", passed=0))
        _insert(conn, _case(
            base_id + "_fakedate", "post_cutoff", decision="no_trade", direction="none",
            fake_date_variant="2099-01-15", passed=0,
        ))
        report = validation_corpus.audit_corpus(conn)
        self.assertTrue(report["structural_ok"])
        self.assertEqual(report["metrics"]["fake_date_invariance"], 0.0)
        self.assertEqual(report["metrics"]["post_cutoff_accuracy"], 0.0)

    def test_masked_payload_rejects_exact_identifiers(self) -> None:
        conn = _conn()
        masked = {
            "world_change": "MSFT reported an exact event on 2026-07-31 worth $50 million",
            "sector_or_theme": "software",
            "structural_features": ["demand", "pricing"],
            "primary_source_class": "filing",
            "ticker": "MSFT",
        }
        _insert(conn, _case(
            "vc_negative_control_identifier_leak_001", "negative_control",
            decision="no_trade", direction="none", expected_decision="no_trade",
            expected_direction="none", masked_override=masked,
        ))
        report = validation_corpus.audit_corpus(conn)
        self.assertFalse(report["structural_ok"])
        self.assertTrue(any("identifier keys" in error for error in report["errors"]))
        self.assertTrue(any("exact date" in error for error in report["errors"]))
        self.assertTrue(any("money amount" in error for error in report["errors"]))

    def test_stored_pass_cannot_disagree_with_frozen_decision_and_outcome(self) -> None:
        conn = _conn()
        _insert(conn, _case(
            "vc_negative_control_bad_grade_001", "negative_control",
            decision="open", direction="long", expected_decision="no_trade",
            expected_direction="none", passed=1,
        ))
        report = validation_corpus.audit_corpus(conn)
        self.assertFalse(report["structural_ok"])
        self.assertTrue(any("disagrees with derived" in error for error in report["errors"]))

    def test_freeze_then_resolve_preserves_decision_and_derives_grade(self) -> None:
        conn = _conn()
        row = _case(
            "vc_negative_control_forward_trap_001", "negative_control",
            decision="no_trade", direction="none", expected_decision="no_trade",
            expected_direction="none",
        )
        freeze_case = {
            "id": row[0], "masked_case_json": json.loads(row[1]), "case_class": row[2],
            "fake_date_variant": row[3], "model_decision_json": json.loads(row[4]),
        }
        freeze_path = self._batch_file({
            "approved_by": "human", "experiment_id": "test-policy-v1", "cases": [freeze_case],
        })
        frozen = validation_corpus._freeze(conn, freeze_path)
        self.assertEqual(frozen["freeze"]["inserted"], 1)
        before = conn.execute(
            "SELECT masked_case_json,model_decision_json FROM validation_cases WHERE id=?", (row[0],)
        ).fetchone()

        outcome = json.loads(row[5])
        outcome["resolved_at"] = "2099-08-03T20:00:00Z"
        resolve_path = self._batch_file({
            "approved_by": "human", "experiment_id": "test-policy-v1",
            "cases": [{"id": row[0], "resolved_outcome_json": outcome}],
        })
        resolved = validation_corpus._resolve(conn, resolve_path)
        self.assertEqual(resolved["resolve"]["resolved"], 1)
        after = conn.execute(
            "SELECT masked_case_json,model_decision_json,passed FROM validation_cases WHERE id=?",
            (row[0],),
        ).fetchone()
        self.assertEqual(tuple(before), tuple(after)[:2])
        self.assertEqual(after["passed"], 1)

    def test_resolve_without_prior_freeze_is_rejected(self) -> None:
        conn = _conn()
        row = _case("vc_winner_not_frozen_001", "winner")
        resolve_path = self._batch_file({
            "approved_by": "human", "experiment_id": "test-policy-v1",
            "cases": [{"id": row[0], "resolved_outcome_json": json.loads(row[5])}],
        })
        with self.assertRaisesRegex(ValueError, "frozen before resolve"):
            validation_corpus._resolve(conn, resolve_path)


if __name__ == "__main__":
    unittest.main()
