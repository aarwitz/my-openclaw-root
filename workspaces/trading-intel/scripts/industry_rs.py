#!/usr/bin/env python3
"""Offline industry-group relative-strength scanner over point-in-time features."""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import statistics
from pathlib import Path

import theme_model as tm

GROUPS_PATH = tm.ROOT / "workspaces/trading-intel/config/industry_groups.json"


def window_return(conn: sqlite3.Connection, ticker: str, as_of: str,
                  sessions: int) -> float | None:
    rows = conn.execute(
        "SELECT value FROM features WHERE ticker=? AND name='ret_1d' AND as_of<=? "
        "ORDER BY as_of DESC LIMIT ?", (ticker, as_of, sessions),
    ).fetchall()
    if len(rows) != sessions or any(row[0] is None for row in rows):
        return None
    return (math.prod(1.0 + float(row[0]) / 100.0 for row in rows) - 1.0) * 100.0


def group_snapshot(conn: sqlite3.Connection, groups: dict[str, list[str]],
                   as_of: str) -> list[dict]:
    output = []
    for group, tickers in sorted(groups.items()):
        windows: dict[int, list[float]] = {}
        for horizon in (5, 21, 63):
            windows[horizon] = [value for ticker in tickers
                                if (value := window_return(conn, ticker, as_of, horizon)) is not None]
        if len(windows[21]) < 3:
            continue
        output.append({
            "group": group,
            "tickers": tickers,
            "as_of": as_of,
            "return_5d_pct": round(statistics.mean(windows[5]), 6) if windows[5] else None,
            "return_21d_pct": round(statistics.mean(windows[21]), 6),
            "return_63d_pct": round(statistics.mean(windows[63]), 6) if windows[63] else None,
            "breadth_21d_pct": round(sum(value > 0 for value in windows[21]) / len(windows[21]) * 100.0, 3),
            "constituents": len(windows[21]),
        })
    ranked = sorted(output, key=lambda row: row["return_21d_pct"])
    denom = max(1, len(ranked) - 1)
    for index, row in enumerate(ranked):
        row["rank_percentile_21d"] = round(index / denom, 6)
    return ranked


def previous_session(conn: sqlite3.Connection, as_of: str, sessions: int = 21) -> str | None:
    rows = conn.execute(
        "SELECT DISTINCT as_of FROM features WHERE name='ret_1d' AND as_of<=? "
        "ORDER BY as_of DESC LIMIT ?", (as_of, sessions + 1),
    ).fetchall()
    return str(rows[-1][0]) if len(rows) == sessions + 1 else None


def scan(feature_path: Path | str, as_of: str, groups: dict[str, list[str]]) -> dict:
    conn = sqlite3.connect(f"file:{feature_path}?mode=ro", uri=True)
    try:
        previous = previous_session(conn, as_of)
        current_rows = group_snapshot(conn, groups, as_of)
        prior_rows = group_snapshot(conn, groups, previous) if previous else []
    finally:
        conn.close()
    current = {row["group"]: row for row in current_rows}
    prior = {row["group"]: row for row in prior_rows}
    inflections = []
    for group, row in current.items():
        old = prior.get(group)
        if not old:
            continue
        if old["rank_percentile_21d"] <= 0.25 and row["rank_percentile_21d"] >= 0.75:
            inflections.append({
                **row,
                "prior_as_of": previous,
                "prior_return_21d_pct": old["return_21d_pct"],
                "prior_rank_percentile_21d": old["rank_percentile_21d"],
                "rank_change": round(row["rank_percentile_21d"] - old["rank_percentile_21d"], 6),
            })
    return {"as_of": as_of, "prior_as_of": previous,
            "groups": current_rows, "inflections": inflections}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--as-of")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--features", default=str(tm.FEATURE_DB))
    args = p.parse_args(argv)
    groups = json.loads(GROUPS_PATH.read_text())
    fconn = sqlite3.connect(f"file:{args.features}?mode=ro", uri=True)
    as_of = args.as_of or fconn.execute(
        "SELECT MAX(as_of) FROM features WHERE name='ret_1d'"
    ).fetchone()[0]
    fconn.close()
    result = scan(args.features, as_of, groups)
    if not args.dry_run and result["inflections"]:
        conn = tm.connect()
        try:
            bottom = min(result["groups"], key=lambda row: row["return_21d_pct"])
            filed = []
            for row in result["inflections"]:
                theme_id = "theme-rs-" + row["group"].replace("_", "-")
                evidence = {key: value for key, value in row.items() if key != "tickers"}
                observation = {
                    "source_type": "scanner",
                    "source_id": f"industry-rs:{row['group']}:{as_of}",
                    "outcome": "support",
                    "beneficiary_return_pct": row["return_21d_pct"],
                    "victim_return_pct": bottom["return_21d_pct"],
                    "spread_pct": round(row["return_21d_pct"] - bottom["return_21d_pct"], 6),
                    "breadth_pct": row["breadth_21d_pct"],
                    "as_of": as_of,
                    "evidence": evidence,
                    "notes": "bottom-quartile to top-quartile 21-session group RS inflection",
                }
                filed.append(tm.file_theme(
                    conn, theme_id=theme_id,
                    statement=f"{row['group'].replace('_',' ').title()} relative strength inflected from the bottom to the top quartile.",
                    beneficiaries=row["tickers"], victims=bottom["tickers"],
                    status="watch", source="scanner", created_by="system",
                    observation=observation,
                ))
            conn.commit()
            result["filed"] = filed
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    result["dry_run"] = bool(args.dry_run)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
