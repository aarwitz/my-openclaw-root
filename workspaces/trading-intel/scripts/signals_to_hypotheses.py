#!/usr/bin/env python3
"""Wire the deterministic world-model scanner into the live pipeline.

Takes the top conviction signals from `signal_scan.scan()` and writes them as `hypotheses` (state
'raw') in the live trading-intel.sqlite, so the EXISTING pipeline trades them — score_hypotheses →
critic → predict → author_intents → **Risk gate (non-bypassable)** → executor. The thesis text names
the firing mechanisms so predict.py links them. Conservative: only strong longs, capped per run,
deduped against open hypotheses. The Risk gate remains the hard safety boundary.

  python3 signals_to_hypotheses.py [--max-new 4] [--p-min 0.62] [--min-fired 3] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspaces/trading-intel/scripts"))))
import signal_scan  # noqa: E402

LIVE = os.path.expanduser("~/.openclaw/state/trading-intel.sqlite")
HZN_TEXT = {"swing_1_5d": "1-5 days", "position_1_4w": "1-4 weeks", "trend_1_3m": "1-3 months"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--watchlist", default="")           # empty -> DYNAMIC live_watchlist() (grows with the universe)
    ap.add_argument("--max-new", type=int, default=4)
    ap.add_argument("--p-min", type=float, default=0.62)
    ap.add_argument("--min-fired", type=int, default=3)
    ap.add_argument("--scan-top-n", type=int, default=200,
                    help="liquidity-ranked names to scan (UNION live_watch); raise to surface more candidates")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    names = [s.strip().upper() for s in a.watchlist.split(",") if s.strip()] or signal_scan.live_watchlist(top_n=a.scan_top_n)

    rows, _ = signal_scan.scan(names, a.min_fired)
    cands = [r for r in rows if r["direction"] == "long" and r["p_long"] >= a.p_min][:a.max_new * 3]

    conn = sqlite3.connect(LIVE, timeout=60.0)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    written = []
    for r in cands:
        if len(written) >= a.max_new:
            break
        t = r["ticker"]
        # skip names too correlated with a stronger ranked candidate (don't pile into one bet)
        if r.get("novelty", 1.0) < 0.5:
            continue
        # dedupe: skip if an unresolved hypothesis already exists for this ticker
        dup = conn.execute("SELECT 1 FROM hypotheses WHERE tickers LIKE ? AND state NOT IN "
                           "('resolved','retired') LIMIT 1", (f'%"{t}"%',)).fetchone()
        if dup:
            continue
        top = sorted(r["fired"], key=lambda f: -f["posterior"])[:3]
        hzns = {HZN_TEXT.get(f["horizon"], f["horizon"]) for f in top}
        rationale = "; ".join(f["rationale"] for f in top)
        thesis = (f"Long {t}: deterministic world-model signal "
                  f"({r['n_fired']} calibrated mechanisms firing, p={r['p_long']:.2f}). "
                  f"Mechanisms: {rationale}.")
        conf = "high" if r["p_long"] >= 0.72 else ("medium" if r["p_long"] >= 0.65 else "low")
        hzn = "trend_1_3m" if any("1-3 months" in h for h in hzns) else "position_1_4w"
        hid = "hyp-sig-" + uuid.uuid4().hex[:16]
        if not a.dry_run:
            conn.execute("INSERT INTO hypotheses(id,created_at,created_by,tickers,thesis_summary,"
                         "state,confidence,time_horizon,rationale_concise) VALUES(?,?,?,?,?,?,?,?,?)",
                         (hid, now, "quant", json.dumps([t]), thesis, "raw", conf, hzn,
                          f"world-model scan: {len(top)} mechs, p_long={r['p_long']:.2f}"))
        written.append((t, round(r["p_long"], 3), r["n_fired"], conf))
    if not a.dry_run:
        conn.commit()
    print(f"signals_to_hypotheses: {'DRY-RUN ' if a.dry_run else ''}wrote {len(written)} new raw hypotheses "
          f"(from {len(cands)} long candidates p>={a.p_min}); the pipeline + Risk gate take it from here")
    for w in written:
        print("  ", w)
    conn.close()


if __name__ == "__main__":
    main()
