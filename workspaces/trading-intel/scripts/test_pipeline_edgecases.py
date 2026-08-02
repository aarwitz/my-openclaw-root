#!/usr/bin/env python3
"""Hermetic market-open execution edge cases.

These used to live in a hand-rolled ``main()`` function, so ``unittest
discover`` imported the file but silently ran zero assertions.  Keeping them as
real TestCase methods makes the release gate exercise the failure paths on every
merge and nightly pass.
"""

from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/executor/scripts")
sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
import execute_intent as ex  # noqa: E402


def _intent(*, entry_ref: float = 100.0, age_min: int = 3) -> dict:
    created = (datetime.now(timezone.utc) - timedelta(minutes=age_min)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    return {
        "id": "TEST",
        "hypothesis_id": "HYP-TEST",
        "state": "approved",
        "action": "open",
        "direction": "long",
        "size": 2,
        "vehicle": "direct_equity",
        "ticker": "TESTX",
        "entry_price_target": entry_ref,
        "created_at": created,
    }


class ExecutionFailurePathTests(unittest.TestCase):
    def setUp(self) -> None:
        self._originals = ex.latest_trade, ex._recent_daily_vol

    def tearDown(self) -> None:
        ex.latest_trade, ex._recent_daily_vol = self._originals

    def run_path(
        self,
        live_price: float | None,
        daily_vol: float,
        intent: dict,
        *,
        quote_error: bool = False,
    ) -> dict:
        if quote_error:
            def fail_quote(_ticker):
                raise RuntimeError("feed down")
            ex.latest_trade = fail_quote
        else:
            ex.latest_trade = lambda _ticker: (
                {"price": live_price, "ts": "fixture"}
                if live_price is not None else None
            )
        ex._recent_daily_vol = lambda _ticker: daily_vol
        return ex.process(intent, dry_run=True, conn=None)

    def test_fresh_signal_uses_marketable_limit(self) -> None:
        result = self.run_path(100.0, 0.02, _intent())
        self.assertEqual(result["would_submit"]["order_type"], "limit")

    def test_stale_reasoning_is_rejected(self) -> None:
        self.assertTrue(
            self.run_path(100.0, 0.02, _intent(age_min=600))["rejected_stale"]
        )

    def test_calm_name_price_drift_is_rejected(self) -> None:
        self.assertTrue(
            self.run_path(108.0, 0.01, _intent())["rejected_stale"]
        )

    def test_volatile_name_tolerates_proportional_drift(self) -> None:
        result = self.run_path(108.0, 0.09, _intent())
        self.assertIn("would_submit", result)

    def test_absolute_drift_cap_always_binds(self) -> None:
        self.assertTrue(
            self.run_path(125.0, 0.09, _intent())["rejected_stale"]
        )

    def test_missing_quote_skips_without_blind_order(self) -> None:
        result = self.run_path(None, 0.02, _intent())
        self.assertTrue(result["skipped_no_quote"])
        self.assertNotIn("would_submit", result)

    def test_missing_quote_eventually_becomes_terminal_stale_rejection(self) -> None:
        self.assertTrue(
            self.run_path(None, 0.02, _intent(age_min=600))["rejected_stale"]
        )

    def test_quote_connector_failure_is_fail_closed(self) -> None:
        result = self.run_path(None, 0.02, _intent(), quote_error=True)
        self.assertTrue(result["skipped_no_quote"])


if __name__ == "__main__":
    unittest.main()
