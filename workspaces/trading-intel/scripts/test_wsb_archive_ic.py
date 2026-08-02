#!/usr/bin/env python3
"""Deterministic smoke tests for wsb_archive_ic.py utilities."""

from __future__ import annotations

from collections import Counter
import unittest

import wsb_archive_ic as mod


class WsbArchiveUtilityTests(unittest.TestCase):
    def test_cashtag_and_uppercase_matching(self) -> None:
        symbols = {"NVDA", "AAPL", "SPY", "AI"}
        found = mod.extract_tickers(
            "Rotating into $NVDA and AAPL. ai is not a ticker mention here, but AI is. SPY too.",
            symbols,
        )
        self.assertEqual(found, {"NVDA", "AAPL", "AI", "SPY"})

    def test_rolling_window_sum(self) -> None:
        days = ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-05"]
        counter = Counter({"2025-01-01": 1, "2025-01-03": 2, "2025-01-05": 3})
        out = mod.rolling_window_sum(counter, {d: i for i, d in enumerate(days)}, days, 3)
        self.assertEqual(out, [1, 1, 3, 5])

    def test_spearman_perfect_monotonic(self) -> None:
        self.assertAlmostEqual(mod.spearman([1, 2, 3], [10, 20, 30]), 1.0)


if __name__ == "__main__":
    unittest.main()
