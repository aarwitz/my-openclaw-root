#!/usr/bin/env python3
from __future__ import annotations

import os
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path("/home/aaron/.openclaw")
os.environ.setdefault("OPENCLAW_RUN_WITH_TRACE", "1")
sys.path.insert(0, str(ROOT / "workspaces/trader/scripts"))
sys.path.insert(0, str(ROOT / "workspaces/trading-intel/scripts"))
sys.path.insert(0, str(ROOT / "workspaces/developer/scripts"))
sys.path.insert(0, str(ROOT / "scripts/lib"))
import enforce_inventory_lineage as enforcer  # noqa: E402


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE trade_intents(
          id TEXT PRIMARY KEY,hypothesis_id TEXT,expression_candidate_id TEXT,
          created_by TEXT,created_at TEXT,action TEXT,tranche_type TEXT,ticker TEXT,
          vehicle TEXT,size REAL,entry_price_target REAL,stop_rule TEXT,
          time_horizon TEXT,triggered_by TEXT,modeled_slippage_bps REAL,
          state TEXT,direction TEXT);
        CREATE TABLE audits(
          id TEXT PRIMARY KEY,timestamp TEXT,actor TEXT,entity_type TEXT,
          entity_id TEXT,action TEXT,before_state TEXT,after_state TEXT,
          rationale_concise TEXT);
        INSERT INTO trade_intents(
          id,hypothesis_id,expression_candidate_id,created_by,created_at,action,
          ticker,state,direction
        ) VALUES('ti-old','h-old','ec-old','trader','2026-08-01T10:00:00Z',
          'open','OLD','filled','long');
    """)
    return conn


def _report(status: str = "legacy_pre_cutover") -> dict:
    return {"positions": [{
        "position_id": "p-old", "hypothesis_id": "h-old", "ticker": "OLD",
        "qty": 2.0, "gross_value": 200.0, "status": status,
        "gaps": ["prediction_before_intent"],
    }]}


class InventoryLineageEnforcerTests(unittest.TestCase):
    def test_open_market_authors_one_idempotent_risk_reducing_exit(self) -> None:
        conn = _conn()
        with mock.patch.object(enforcer, "build_report", return_value=_report()):
            first = enforcer.enforce(
                conn, clock={"is_open": True},
                quote_loader=lambda _tickers: {"OLD": {"price": 101.0}},
            )
            second = enforcer.enforce(
                conn, clock={"is_open": True},
                quote_loader=lambda _tickers: {"OLD": {"price": 102.0}},
            )
        self.assertEqual(first["authored"], 1)
        self.assertEqual(second["authored"], 0)
        self.assertEqual(second["existing_exits"], 1)
        row = conn.execute(
            "SELECT action,size,triggered_by,state,direction FROM trade_intents "
            "WHERE triggered_by='inventory_lineage_quarantine_v1'"
        ).fetchone()
        self.assertEqual(tuple(row), (
            "exit", 2.0, "inventory_lineage_quarantine_v1", "proposed", "long",
        ))
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM audits WHERE action='author_lineage_exit'"
        ).fetchone()[0], 1)

    def test_closed_market_defers_without_writing(self) -> None:
        conn = _conn()
        def should_not_quote(_tickers):
            raise AssertionError("closed-market enforcement fetched a quote")
        with mock.patch.object(enforcer, "build_report", return_value=_report()):
            result = enforcer.enforce(
                conn, clock={"is_open": False}, quote_loader=should_not_quote,
            )
        self.assertEqual(result["deferred_market_closed"], 1)
        self.assertEqual(result["authored"], 0)

    def test_dry_run_plans_while_closed_but_never_writes(self) -> None:
        conn = _conn()
        with mock.patch.object(enforcer, "build_report", return_value=_report()):
            result = enforcer.enforce(
                conn, dry_run=True, clock={"is_open": False},
                quote_loader=lambda _tickers: {"OLD": {"price": 99.0}},
            )
        self.assertEqual(result["would_author"], 1)
        self.assertEqual(conn.execute(
            "SELECT COUNT(*) FROM trade_intents WHERE action='exit'"
        ).fetchone()[0], 0)

    def test_complete_modern_inventory_is_untouched(self) -> None:
        conn = _conn()
        with mock.patch.object(
            enforcer, "build_report", return_value=_report("modern_lineage")
        ):
            result = enforcer.enforce(
                conn, clock={"is_open": True}, quote_loader=lambda _tickers: {},
            )
        self.assertEqual(result["legacy_or_invalid_positions"], 0)
        self.assertEqual(result["authored"], 0)

    def test_missing_open_market_quote_remains_unresolved(self) -> None:
        conn = _conn()
        with mock.patch.object(enforcer, "build_report", return_value=_report()):
            result = enforcer.enforce(
                conn, clock={"is_open": True}, quote_loader=lambda _tickers: {},
            )
        self.assertEqual(result["unresolved"], 1)
        self.assertEqual(result["authored"], 0)


if __name__ == "__main__":
    unittest.main()
