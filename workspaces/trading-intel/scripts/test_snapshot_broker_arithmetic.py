#!/usr/bin/env python3
"""Regression tests for public snapshot paper-ledger arithmetic."""

from __future__ import annotations

import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


SNAPSHOT_BUILDER = (
    Path(__file__).resolve().parents[2] / "developer" / "scripts" / "snapshot_builder.py"
)


def _load_builder():
    spec = importlib.util.spec_from_file_location("snapshot_builder_under_test", SNAPSHOT_BUILDER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SnapshotBrokerArithmeticTests(unittest.TestCase):
    def test_agent_roster_matches_topology_v5(self):
        builder = _load_builder()
        self.assertEqual(
            [a["id"] for a in builder.AGENTS],
            [
                "researcher",
                "quant",
                "critic",
                "trader",
                "risk",
                "executor",
                "archivist",
                "overseer",
                "developer",
            ],
        )

    def test_enrichment_preserves_owned_ledger_pnl(self):
        builder = _load_builder()
        fake_marketdata = types.ModuleType("connectors.marketdata")
        fake_marketdata.daily_bars = lambda _symbol, days=6: [
            {"t": "2026-07-29", "c": 95.0},
        ]
        row = {
            "symbol": "TEST",
            "qty": -2.0,
            "current_price": 90.0,
            "market_value": -180.0,
            "cost_basis": -200.0,
            "unrealized_pl": 20.0,
            "unrealized_plpc": 0.1,
        }
        with patch.dict(sys.modules, {"connectors.marketdata": fake_marketdata}):
            result = builder._enrich_positions_canonical([row])[0]

        self.assertEqual(result["market_value"], -180.0)
        self.assertEqual(result["cost_basis"], -200.0)
        self.assertEqual(result["unrealized_pl"], 20.0)
        self.assertEqual(result["unrealized_plpc"], 0.1)
        self.assertEqual(result["direction"], "short")
        self.assertEqual(result["unrealized_intraday_pl"], 10.0)


if __name__ == "__main__":
    unittest.main()
