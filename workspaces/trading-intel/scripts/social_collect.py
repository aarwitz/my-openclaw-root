#!/usr/bin/env python3
"""ApeWisdom social-mention collector (D60) — free forward feed for the
retail-attention feature family.

Research verdict 2026-07-10: Reddit archive dumps = the backtest spine
(2019→2025 point-in-time); ApeWisdom = the free live continuation (~17
subreddits + 4chan, keyless, ~2x/hour refresh). This collector snapshots the
cross-section a few times daily into features.sqlite::social_mentions so the
live series exists from today regardless of when the backtest lands.

  python3 social_collect.py            # one snapshot (cron-able)
  python3 social_collect.py status
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.request
from datetime import datetime, timezone

FEAT = os.path.expanduser("~/.openclaw/state/features.sqlite")
BASE = "https://apewisdom.io/api/v1.0/filter"
DDL = """CREATE TABLE IF NOT EXISTS social_mentions (
  ts        TEXT NOT NULL,             -- snapshot time (UTC iso)
  source    TEXT NOT NULL,             -- apewisdom filter name
  ticker    TEXT NOT NULL,
  rank      INTEGER,
  mentions  INTEGER,
  upvotes   INTEGER,
  rank_24h_ago     INTEGER,
  mentions_24h_ago INTEGER,
  PRIMARY KEY (ts, source, ticker)
);"""


def snapshot() -> dict:
    conn = sqlite3.connect(FEAT, timeout=60)
    conn.execute(DDL)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    total = 0
    for flt in ("all-stocks", "wallstreetbets"):
        page, wrote = 1, 0
        while page <= 6:  # top ~600 names is plenty
            try:
                req = urllib.request.Request(
                    f"{BASE}/{flt}/page/{page}",
                    headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                             "Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=30) as r:
                    d = json.loads(r.read())
            except Exception as e:
                print(f"WARN {flt} p{page}: {str(e)[:80]}", file=sys.stderr)
                break
            rows = d.get("results") or []
            if not rows:
                break
            for x in rows:
                conn.execute(
                    "INSERT OR REPLACE INTO social_mentions VALUES (?,?,?,?,?,?,?,?)",
                    (ts, flt, str(x.get("ticker", "")).upper(), x.get("rank"),
                     x.get("mentions"), x.get("upvotes"),
                     x.get("rank_24h_ago"), x.get("mentions_24h_ago")))
                wrote += 1
            if page >= int(d.get("pages") or 1):
                break
            page += 1
            time.sleep(1)
        total += wrote
    conn.commit()
    conn.close()
    return {"ts": ts, "rows": total}


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "status":
        conn = sqlite3.connect(FEAT)
        try:
            r = conn.execute("SELECT COUNT(*), COUNT(DISTINCT ts), MIN(ts), MAX(ts) FROM social_mentions").fetchone()
            print(json.dumps({"rows": r[0], "snapshots": r[1], "from": r[2], "to": r[3]}))
        except sqlite3.OperationalError:
            print(json.dumps({"rows": 0}))
        return 0
    print(json.dumps(snapshot()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
