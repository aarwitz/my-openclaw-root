#!/usr/bin/env python3
"""Deterministic smoke tests for wsb_archive_ic.py utilities."""

from __future__ import annotations

from collections import Counter

import wsb_archive_ic as mod


def check(name: str, cond: bool) -> None:
    print(("PASS " if cond else "FAIL ") + name)
    if not cond:
        raise SystemExit(1)


def main() -> int:
    symbols = {"NVDA", "AAPL", "SPY", "AI"}
    found = mod.extract_tickers(
        "Rotating into $NVDA and AAPL. ai is not a ticker mention here, but AI is. SPY too.",
        symbols,
    )
    check("cashtag + uppercase matching", found == {"NVDA", "AAPL", "AI", "SPY"})

    days = ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-05"]
    counter = Counter({"2025-01-01": 1, "2025-01-03": 2, "2025-01-05": 3})
    out = mod.rolling_window_sum(counter, {d: i for i, d in enumerate(days)}, days, 3)
    check("rolling window sum", out == [1, 1, 3, 5])

    rho = mod.spearman([1, 2, 3], [10, 20, 30])
    check("spearman perfect monotonic", abs(rho - 1.0) < 1e-9)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
