#!/usr/bin/env python3
"""Deterministic harness for drag_report block-reason rollups.

Usage:
  python3 test_drag_report_multigate.py
"""

from __future__ import annotations

import sqlite3
import sys

import drag_report  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    suffix = f" :: {detail}" if detail else ""
    print(("  PASS " if cond else "  FAIL ") + name + suffix)


def main() -> int:
    reason = "gates_failed:evidence_freshness[stale:EVID-1|missing_ts:EVID-2],provenance_completeness"
    normalized = drag_report.normalize_block_reason(reason)
    check(
        "normalize strips per-artifact freshness detail",
        normalized == "gates_failed:evidence_freshness,provenance_completeness",
        normalized,
    )

    conn = sqlite3.connect(":memory:")
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE trade_intents (
          id TEXT PRIMARY KEY,
          state TEXT,
          blocked_reason TEXT,
          created_at TEXT
        );
        CREATE TABLE predictions (
          id TEXT PRIMARY KEY,
          resolved_at TEXT,
          brier_component REAL,
          realized_outcome TEXT,
          predicted_at TEXT
        );
        """
    )
    rows = [
        ("TI-1", "blocked", "gates_failed:evidence_freshness[stale:EVID-1]", "2026-07-16T10:00:00Z"),
        ("TI-2", "blocked", "gates_failed:evidence_freshness[missing_ts:EVID-2]", "2026-07-16T11:00:00Z"),
        (
            "TI-3",
            "blocked",
            "gates_failed:evidence_freshness[stale:EVID-3|missing_ts:EVID-4]",
            "2026-07-16T12:00:00Z",
        ),
    ]
    cur.executemany(
        "INSERT INTO trade_intents (id, state, blocked_reason, created_at) VALUES (?, ?, ?, ?)",
        rows,
    )
    signals = drag_report.collect_signals(cur)
    blocked = next((s for s in signals if s["id"] == "blocked-gates-failed-evidence-freshness"), None)
    check(
        "collect_signals groups detailed freshness blocks into one recurring class",
        blocked is not None and "count=3" in blocked["evidence"][0],
        blocked["evidence"][0] if blocked else "missing signal",
    )

    print(f"\n{'GREEN' if not FAIL else 'RED'}: {len(PASS)} passed, {len(FAIL)} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
