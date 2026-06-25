#!/usr/bin/env python3
"""Build X social mention-VOLUME features into features.sqlite (source='x').

Per-ticker, point-in-time (count for day D is knowable at EOD D → as_of = knowable_at = D), with a
TRAILING-only z-score (no look-ahead). The signal is `x_mention_vol_z` = abnormal attention spike.
Validation-first: this only populates the names you pass; a feature reaches sized trading only after
the FDR backtest confirms edge (then add `x_mention_vol_z` to GEN_FEATURES + full rollout).

  python3 x_features.py --names AAPL,NVDA,... --start 2024-01-01 --end 2026-06-24 [--win 30]
"""
from __future__ import annotations
import argparse, math, os, sqlite3, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from connectors import x  # noqa: E402

FEAT = os.path.expanduser("~/.openclaw/state/features.sqlite")


def build(names, start, end, win=30):
    conn = sqlite3.connect(FEAT)
    total = 0
    for sym in names:
        try:
            series = x.daily_mention_counts("$" + sym.lstrip("$"), start, end)
        except Exception as e:
            print(f"  {sym}: skip ({str(e)[:60]})"); continue
        dates = [d for d, _ in series]
        vals = [v for _, v in series]
        rows = []
        for i in range(win, len(series)):
            w = vals[i - win:i]                 # PRIOR window only → no look-ahead
            mu = sum(w) / len(w)
            sd = (sum((v - mu) ** 2 for v in w) / len(w)) ** 0.5
            z = (vals[i] - mu) / sd if sd > 0 else 0.0
            d = dates[i]
            rows.append((sym, d, "x_mention_vol_z", round(z, 4), d, "x"))
            rows.append((sym, d, "x_mention_vol_log", round(math.log1p(vals[i]), 4), d, "x"))
        conn.executemany(
            "INSERT OR REPLACE INTO features(ticker,as_of,name,value,knowable_at,source) VALUES(?,?,?,?,?,?)", rows)
        conn.commit()
        total += len(rows)
        print(f"  {sym}: {len(series)} days -> {len(rows)} feature rows", flush=True)
    print(f"done: {total} x-feature rows across {len(names)} names")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--names", required=True)
    ap.add_argument("--start", default="2024-01-01")
    ap.add_argument("--end", default="2026-06-24")
    ap.add_argument("--win", type=int, default=30)
    a = ap.parse_args()
    build([s.strip().upper() for s in a.names.split(",") if s.strip()], a.start, a.end, a.win)


if __name__ == "__main__":
    main()
