#!/usr/bin/env python3
"""Point-in-time contract shared by every writer to ``features.sqlite``.

The existing store uses ``as_of`` as *first usable date*, not the economic
period represented by the value.  Therefore ``as_of`` and ``knowable_at`` must
be identical.  Economic-period/vintage lineage belongs in a future sidecar,
never in an earlier ``as_of`` that would make a backtest read unavailable data.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
from datetime import date
from pathlib import Path
from typing import Iterable, Sequence


DEFAULT_DB = Path("/home/aaron/.openclaw/state/features.sqlite")


def validate_feature_row(row: Sequence[object]) -> tuple[str, str, str, float, str, str]:
    if len(row) != 6:
        raise ValueError("feature row must have exactly six fields")
    ticker, as_of, name, value, knowable_at, source = row
    ticker = str(ticker or "").strip().upper()
    as_of = str(as_of or "").strip()
    name = str(name or "").strip()
    knowable_at = str(knowable_at or "").strip()
    source = str(source or "").strip()
    if not ticker or not name or not source:
        raise ValueError("ticker, feature name, and source are required")
    try:
        date.fromisoformat(as_of)
        date.fromisoformat(knowable_at)
    except ValueError as exc:
        raise ValueError("as_of and knowable_at must be ISO calendar dates") from exc
    if as_of != knowable_at:
        raise ValueError(
            "as_of is the first usable date and must equal knowable_at; "
            "store economic-period lineage separately"
        )
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("feature value must be finite")
    return ticker, as_of, name, number, knowable_at, source


def validated_feature_rows(rows: Iterable[Sequence[object]]) -> list[tuple]:
    return [validate_feature_row(row) for row in rows]


TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS features_point_in_time_insert
BEFORE INSERT ON features
WHEN NEW.ticker IS NULL OR trim(NEW.ticker) = ''
  OR NEW.as_of IS NULL OR length(NEW.as_of) != 10 OR date(NEW.as_of) IS NULL
  OR NEW.name IS NULL OR trim(NEW.name) = ''
  OR NEW.value IS NULL OR abs(NEW.value) > 1.7976931348623157e308
  OR NEW.knowable_at IS NULL OR length(NEW.knowable_at) != 10 OR date(NEW.knowable_at) IS NULL
  OR NEW.as_of != NEW.knowable_at
  OR NEW.source IS NULL OR trim(NEW.source) = ''
BEGIN
  SELECT RAISE(ABORT, 'features point-in-time contract violated');
END;

CREATE TRIGGER IF NOT EXISTS features_point_in_time_update
BEFORE UPDATE ON features
WHEN NEW.ticker IS NULL OR trim(NEW.ticker) = ''
  OR NEW.as_of IS NULL OR length(NEW.as_of) != 10 OR date(NEW.as_of) IS NULL
  OR NEW.name IS NULL OR trim(NEW.name) = ''
  OR NEW.value IS NULL OR abs(NEW.value) > 1.7976931348623157e308
  OR NEW.knowable_at IS NULL OR length(NEW.knowable_at) != 10 OR date(NEW.knowable_at) IS NULL
  OR NEW.as_of != NEW.knowable_at
  OR NEW.source IS NULL OR trim(NEW.source) = ''
BEGIN
  SELECT RAISE(ABORT, 'features point-in-time contract violated');
END;
"""


def audit_connection(conn: sqlite3.Connection) -> dict[str, int]:
    row = conn.execute(
        "SELECT COUNT(*), "
        "SUM(ticker IS NULL OR trim(ticker)=''), "
        "SUM(as_of IS NULL OR length(as_of)!=10 OR date(as_of) IS NULL), "
        "SUM(name IS NULL OR trim(name)=''), "
        "SUM(value IS NULL OR abs(value)>1.7976931348623157e308), "
        "SUM(knowable_at IS NULL OR length(knowable_at)!=10 OR date(knowable_at) IS NULL), "
        "SUM(as_of!=knowable_at), "
        "SUM(source IS NULL OR trim(source)='') FROM features"
    ).fetchone()
    keys = (
        "rows", "bad_ticker", "bad_as_of", "bad_name", "bad_value",
        "bad_knowable_at", "availability_mismatch", "bad_source",
    )
    report = {key: int(value or 0) for key, value in zip(keys, row)}
    report["guard_count"] = int(conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' "
        "AND name IN ('features_point_in_time_insert','features_point_in_time_update')"
    ).fetchone()[0])
    return report


def install_guards(conn: sqlite3.Connection) -> dict[str, int]:
    audit = audit_connection(conn)
    if any(
        value for key, value in audit.items()
        if key not in ("rows", "guard_count")
    ):
        raise ValueError(f"refusing to guard a nonconforming feature store: {audit}")
    conn.executescript(TRIGGERS)
    return audit_connection(conn)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("audit", "install"))
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    conn = sqlite3.connect(args.db, timeout=60.0)
    try:
        report = audit_connection(conn)
        if args.command == "install":
            report = install_guards(conn)
            conn.commit()
        violations = {
            key: value for key, value in report.items()
            if key not in ("rows", "guard_count") and value
        }
        ok = not violations and report["guard_count"] == 2
        print(json.dumps({"ok": ok, **report, "violations": violations}, sort_keys=True))
        return 0 if ok else 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
