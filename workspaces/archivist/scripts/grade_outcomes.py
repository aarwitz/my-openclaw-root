#!/usr/bin/env python3
"""Compatibility entrypoint for prediction-level outcome grading.

The old implementation graded a hypothesis from its earliest prediction and
copied that one return window onto every unresolved prediction for the
hypothesis. That attached the wrong entry time and sometimes the wrong horizon
to later forecasts.

Prediction calibration is now owned by ``resolve_prediction_backlog.py``. Each
forecast is matured from its own ``predicted_at`` and ``horizon``, compared with
SPY over the matching window, assigned its own Brier component, and emits at
most one total unit of mechanism-learning credit. Hypothesis/trade lifecycle
resolution remains separate.

This filename remains as a wrapper because host jobs and operator tooling call
``grade_outcomes.py``.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path

TI_SCRIPTS = Path(
    os.path.expanduser("~/.openclaw/workspaces/trading-intel/scripts")
)
sys.path.insert(0, str(TI_SCRIPTS))

from resolve_prediction_backlog import resolve_prediction_backlog  # noqa: E402

DB_PATH = Path(os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args(argv)

    conn = sqlite3.connect(args.db, timeout=60.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        result = resolve_prediction_backlog(
            conn,
            dry_run=args.dry_run,
            actor="archivist",
        )
    finally:
        conn.close()

    result["grader"] = "prediction_level_v2"
    result["hypothesis_lifecycle_mutated"] = False
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
