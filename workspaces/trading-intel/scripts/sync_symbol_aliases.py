#!/usr/bin/env python3
"""Apply configured live symbol aliases to feature-universe routing.

Historical feature rows remain under the historical ticker. Only the current
universe and explicit live watchlist are redirected.
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
from pathlib import Path

import symbol_lifecycle

FEATURE_DB = Path(os.path.expanduser("~/.openclaw/state/features.sqlite"))


def sync(db_path: Path = FEATURE_DB, *, dry_run: bool = False) -> dict:
    conn = sqlite3.connect(db_path, timeout=60)
    conn.row_factory = sqlite3.Row
    changes = []
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS universe("
            "symbol TEXT PRIMARY KEY, market_cap REAL, sector TEXT, status TEXT, "
            "ipo_date TEXT, delisted_date TEXT)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS live_watch(symbol TEXT PRIMARY KEY)"
        )
        for alias in symbol_lifecycle.aliases():
            old, new = alias["old"], alias["new"]
            old_universe = conn.execute(
                "SELECT * FROM universe WHERE symbol=?", (old,)
            ).fetchone()
            new_universe = conn.execute(
                "SELECT 1 FROM universe WHERE symbol=?", (new,)
            ).fetchone()
            watched = conn.execute(
                "SELECT 1 FROM live_watch WHERE symbol=?", (old,)
            ).fetchone()
            if not old_universe and not watched:
                continue
            changes.append({
                "old": old,
                "new": new,
                "universe_redirect": bool(old_universe),
                "watchlist_redirect": bool(watched),
            })
            if dry_run:
                continue
            if old_universe:
                if not new_universe:
                    conn.execute(
                        "INSERT INTO universe(symbol,market_cap,sector,status,ipo_date,delisted_date) "
                        "VALUES(?,?,?,'active',?,NULL)",
                        (new, old_universe["market_cap"], old_universe["sector"],
                         alias["effective_date"]),
                    )
                conn.execute(
                    "UPDATE universe SET status='renamed', delisted_date=? WHERE symbol=?",
                    (alias["effective_date"], old),
                )
            if watched:
                conn.execute("INSERT OR IGNORE INTO live_watch(symbol) VALUES(?)", (new,))
                conn.execute("DELETE FROM live_watch WHERE symbol=?", (old,))
        if not dry_run:
            conn.commit()
    finally:
        conn.close()
    return {"dry_run": dry_run, "changes": changes, "changed": len(changes)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(FEATURE_DB))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    print(json.dumps(sync(Path(args.db), dry_run=args.dry_run), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
