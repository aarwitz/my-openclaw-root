#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

ROOT = Path("/home/aaron/.openclaw")
SPEC = importlib.util.spec_from_file_location(
    "audit_app_snapshot_under_test",
    ROOT / "workspaces/developer/scripts/audit_app_snapshot.py",
)
assert SPEC and SPEC.loader
auditor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(auditor)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE regime(id TEXT,current TEXT,determined_at TEXT);
        CREATE TABLE hypotheses(id TEXT);
        CREATE TABLE sim_accounts(book TEXT,cash REAL);
        CREATE TABLE sim_positions(book TEXT,ticker TEXT,qty REAL,current_value REAL,state TEXT);
        CREATE TABLE predictions(brier_component REAL);
        CREATE TABLE selection_funnel_outcomes(outcome_status TEXT);
        INSERT INTO regime VALUES('rg','caution','2026-08-02T12:00:00Z');
        INSERT INTO hypotheses VALUES('h1');
        INSERT INTO sim_accounts VALUES('desk',900.0);
        INSERT INTO sim_positions VALUES('desk','ABC',2.0,100.0,'open');
        INSERT INTO predictions VALUES(0.25);
        INSERT INTO selection_funnel_outcomes VALUES('matured');
    """)
    return conn


def _snapshot() -> dict:
    agents = [{"id": name} for name in ("executor", "developer", "overseer")]
    return {
        "generated_at": "2099-08-02T12:00:00Z",
        "retail_insights": {}, "system_health": {"color": "green", "issues": []},
        "agents": agents, "topology": [row["id"] for row in agents],
        "regime": {"id": "rg", "current": "caution"},
        "counts": {"hypotheses_total": 1},
        "broker": {
            "source": "sim", "name": "internal_paper", "status": "ACTIVE",
            "available": True, "cash": 900.0, "equity": 1000.0,
        },
        "brokerPositions": [{"symbol": "ABC", "qty": "2", "market_value": "100"}],
        "selectionFunnel": {"coverage": {"matured": 1}},
        "predictionReplay": {"cohort": {"n": 1}},
        "inventoryLineage": {"available": True, "gross_value_by_status": {
            "modern_lineage": 100.0, "legacy_pre_cutover": 0, "post_cutover_violation": 0,
        }, "status_counts": {
            "modern_lineage": 1, "legacy_pre_cutover": 0, "post_cutover_violation": 0,
        }},
        "capital_attribution": {"daily": {"equity": 1000.0}},
    }


class AppSnapshotContractTests(unittest.TestCase):
    INVENTORY = lambda self, _conn: {  # noqa: E731
        "gross_value_by_status": {
            "modern_lineage": 100.0, "legacy_pre_cutover": 0, "post_cutover_violation": 0,
        },
        "status_counts": {
            "modern_lineage": 1, "legacy_pre_cutover": 0, "post_cutover_violation": 0,
        },
    }

    def _path(self, payload: dict) -> Path:
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        with handle:
            json.dump(payload, handle)
        path = Path(handle.name)
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def test_current_v2_contract_reconciles_without_retired_blocks(self) -> None:
        report = auditor.check(
            _conn(), self._path(_snapshot()), health_checker=lambda _conn: [],
            inventory_checker=self.INVENTORY,
        )
        self.assertEqual(report["color"], "green")
        self.assertEqual(report["issues"], [])

    def test_legacy_topology_object_is_red_not_a_crash(self) -> None:
        payload = _snapshot()
        payload["topology"] = {"broker": "Internal paper"}
        report = auditor.check(
            _conn(), self._path(payload), health_checker=lambda _conn: [],
            inventory_checker=self.INVENTORY,
        )
        self.assertEqual(report["color"], "red")
        self.assertTrue(any(issue["area"] == "topology" for issue in report["issues"]))

    def test_health_color_cannot_render_green_when_pipeline_is_yellow(self) -> None:
        report = auditor.check(
            _conn(), self._path(_snapshot()),
            health_checker=lambda _conn: [{"severity": "yellow", "area": "validation_corpus"}],
            inventory_checker=self.INVENTORY,
        )
        self.assertEqual(report["color"], "red")
        self.assertTrue(any(issue["area"] == "health_drift" for issue in report["issues"]))

    def test_inventory_gross_drift_is_red(self) -> None:
        payload = _snapshot()
        payload["inventoryLineage"]["gross_value_by_status"]["modern_lineage"] = 99.0
        report = auditor.check(
            _conn(), self._path(payload), health_checker=lambda _conn: [],
            inventory_checker=self.INVENTORY,
        )
        self.assertEqual(report["color"], "red")
        self.assertTrue(any(issue["area"] == "inventory_lineage" for issue in report["issues"]))


if __name__ == "__main__":
    unittest.main()
