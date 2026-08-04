#!/usr/bin/env python3
"""Compact market-graded theme line for guard/audit digests."""
import sqlite3

import theme_model as tm


def main() -> int:
    conn = sqlite3.connect(f"file:{tm.DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id,status,score,score_as_of FROM themes WHERE status!='dead' "
        "ORDER BY score IS NULL, ABS(score) DESC, updated_at DESC LIMIT 3"
    ).fetchall()
    if not rows:
        print("themes: none")
    else:
        items = [f"{row['id'].removeprefix('theme-')} {row['status']} "
                 + ("ungraded" if row['score'] is None else f"{row['score']:+.1f}pp")
                 for row in rows]
        print("themes: " + "; ".join(items))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
