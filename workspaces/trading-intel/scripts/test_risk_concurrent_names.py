#!/usr/bin/env python3
"""Deterministic harness for concurrent-name attribution in the risk gate."""

from __future__ import annotations

import sqlite3
import sys
import unittest

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/risk/scripts")
import gate_risk_intents as risk_gate  # noqa: E402


def _make_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE positions (
          ticker TEXT,
          state TEXT,
          current_value REAL,
          qty REAL,
          cost_basis REAL
        );
        CREATE TABLE trade_intents (
          id TEXT,
          hypothesis_id TEXT,
          ticker TEXT,
          size REAL,
          entry_price_target REAL,
          state TEXT,
          action TEXT
        );
        """
    )
    return conn


class ConcurrentNameAttributionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.orig_same_day = risk_gate._same_day_opposition_block
        self.orig_max_positions = risk_gate.MAX_POSITIONS
        self.orig_max_name_pct = risk_gate.MAX_NAME_PCT
        self.orig_max_gross_pct = risk_gate.MAX_GROSS_PCT
        risk_gate._same_day_opposition_block = lambda *_args, **_kwargs: None
        risk_gate.MAX_NAME_PCT = 1.0
        risk_gate.MAX_GROSS_PCT = 1.0

    def tearDown(self) -> None:
        risk_gate._same_day_opposition_block = self.orig_same_day
        risk_gate.MAX_POSITIONS = self.orig_max_positions
        risk_gate.MAX_NAME_PCT = self.orig_max_name_pct
        risk_gate.MAX_GROSS_PCT = self.orig_max_gross_pct

    def test_block_reason_carries_concurrent_slot_attribution(self):
        conn = _make_conn()
        conn.executemany(
            "INSERT INTO positions VALUES (?, 'open', ?, ?, ?)",
            [
                ("ABBV", 1000.0, 10.0, 100.0),
                ("BAC", 1000.0, 20.0, 50.0),
            ],
        )
        risk_gate.MAX_POSITIONS = 2

        decision = risk_gate.gate(
            conn,
            {
                "id": "TI-NEW",
                "hypothesis_id": "H-1",
                "ticker": "CMG",
                "size": 1,
                "entry_price_target": 50.0,
                "action": "open",
            },
            equity=100000.0,
            day_pl=0.0,
            regime="neutral",
        )

        self.assertEqual(decision["verdict"], "blocked")
        self.assertIn("concurrent_names=2 >= cap=2", decision["reason"])
        self.assertIn("ABBV[position:open]", decision["reason"])
        self.assertIn("BAC[position:open]", decision["reason"])
        self.assertEqual(
            decision["limits"]["concurrent_name_slots"],
            [
                {"ticker": "ABBV", "sources": ["position:open"]},
                {"ticker": "BAC", "sources": ["position:open"]},
            ],
        )

    def test_existing_name_does_not_consume_new_slot(self):
        conn = _make_conn()
        conn.execute("INSERT INTO positions VALUES ('ABBV', 'open', 1000.0, 10.0, 100.0)")
        risk_gate.MAX_POSITIONS = 1

        decision = risk_gate.gate(
            conn,
            {
                "id": "TI-ADD",
                "hypothesis_id": "H-2",
                "ticker": "ABBV",
                "size": 1,
                "entry_price_target": 50.0,
                "action": "open",
            },
            equity=100000.0,
            day_pl=0.0,
            regime="neutral",
        )

        self.assertNotEqual(decision["verdict"], "blocked")
        self.assertNotIn("max_positions", decision["breaches"])

    def test_pending_exit_does_not_double_count_gross_headroom(self):
        conn = _make_conn()
        conn.execute("INSERT INTO positions VALUES ('AAA', 'open', 59000.0, 590.0, 100.0)")
        conn.execute(
            "INSERT INTO trade_intents VALUES "
            "('TI-EXIT', 'H-EXIT', 'AAA', 100.0, 100.0, 'approved', 'exit')"
        )
        risk_gate.MAX_GROSS_PCT = 0.60

        decision = risk_gate.gate(
            conn,
            {
                "id": "TI-NEW",
                "hypothesis_id": "H-NEW",
                "ticker": "BBB",
                "size": 5,
                "entry_price_target": 100.0,
                "action": "open",
            },
            equity=100000.0,
            day_pl=0.0,
            regime="neutral",
        )

        self.assertEqual(decision["verdict"], "approved")
        self.assertEqual(decision["limits"]["current_gross"], 59000.0)
        gross_attr = decision["limits"]["gross_exposure_attribution"]
        self.assertEqual(gross_attr["pending_new_risk_notional"], 0.0)
        self.assertEqual(gross_attr["pending_risk_reducing_notional"], 10000.0)

    def test_pending_new_risk_still_reserves_gross_headroom(self):
        conn = _make_conn()
        conn.execute("INSERT INTO positions VALUES ('AAA', 'open', 59000.0, 590.0, 100.0)")
        conn.execute(
            "INSERT INTO trade_intents VALUES "
            "('TI-OPEN', 'H-OPEN', 'BBB', 9.0, 100.0, 'approved', 'open')"
        )
        risk_gate.MAX_GROSS_PCT = 0.60

        decision = risk_gate.gate(
            conn,
            {
                "id": "TI-NEW",
                "hypothesis_id": "H-NEW",
                "ticker": "CCC",
                "size": 2,
                "entry_price_target": 200.0,
                "action": "open",
            },
            equity=100000.0,
            day_pl=0.0,
            regime="neutral",
        )

        self.assertEqual(decision["verdict"], "blocked")
        self.assertIn("gross_exposure_cap", decision["breaches"])
        self.assertEqual(decision["limits"]["current_gross"], 59900.0)
        self.assertEqual(
            decision["limits"]["sizing_block_attribution"]["min_one_share_notional"],
            200.0,
        )


if __name__ == "__main__":
    unittest.main()
