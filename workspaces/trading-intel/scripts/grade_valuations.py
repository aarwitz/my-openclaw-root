#!/usr/bin/env python3
"""grade_valuations.py — outcome-grade the valuation engine. No organ ungraded.

Walk-forward grades the desk's own stored valuations: non-overlapping snapshots per ticker
(>=14 calendar days apart), 21td forward excess vs SPY, bucketed by zone. The standing
question: does CHEAP actually outperform RICH? Writes state/valuation-grades.json for the
integrity scoreboard (judgment:valuation_quality).

First grading (2026-07-24, n=63): cheap +6.14%/21td (70% win) vs rich -3.39% (45%) —
spread +9.52pp. CAVEAT recorded: single-regime window (the value-rotation tape); partial
sample overlap (14d spacing < 21td horizon) inflates significance. Measurement, not a
parameter mandate.
"""
from __future__ import annotations
import json, os, sqlite3, statistics, sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspaces/trading-intel/scripts"))))
from connectors.marketdata import daily_bars  # noqa: E402

DB = os.path.expanduser("~/.openclaw/state/trading-intel.sqlite")
OUT = os.path.expanduser("~/.openclaw/state/valuation-grades.json")
HORIZON_TD, MATURITY_TD, SPACING_D = 21, 23, 14
_b: dict[str, list] = {}

def bars(t):
    if t not in _b:
        try: _b[t] = [x for x in daily_bars(t) if x.get("c")]
        except Exception: _b[t] = []
    return _b[t]

def fwd(t, d):
    tb, sb = bars(t), bars("SPY")
    ti = next((i for i, x in enumerate(tb) if x["t"] >= d), None)
    si = next((i for i, x in enumerate(sb) if x["t"] >= d), None)
    if ti is None or si is None or ti + MATURITY_TD >= len(tb) or si + MATURITY_TD >= len(sb):
        return None
    return (float(tb[ti+HORIZON_TD]["c"])/float(tb[ti]["c"]) - float(sb[si+HORIZON_TD]["c"])/float(sb[si]["c"])) * 100

def main() -> int:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True); conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT ticker, substr(as_of,1,10) d, zone FROM valuations WHERE applicable=1 "
        "AND zone IS NOT NULL AND as_of<=datetime('now','-32 days') ORDER BY ticker, as_of").fetchall()
    conn.close()
    last: dict[str, str] = {}; byz: dict[str, list] = {}
    n = 0
    for r in rows:
        if r["ticker"] in last and r["d"] <= last[r["ticker"]]:
            continue
        x = fwd(r["ticker"], r["d"])
        if x is None:
            continue
        last[r["ticker"]] = (date.fromisoformat(r["d"]) + timedelta(days=SPACING_D)).isoformat()
        byz.setdefault(r["zone"], []).append(x); n += 1
    summ = {z: {"n": len(v), "mean_fwd21_excess_pct": round(statistics.mean(v), 2),
                "win_rate": round(sum(1 for x in v if x > 0)/len(v), 2)}
            for z, v in byz.items() if v}
    spread = None
    if byz.get("cheap") and byz.get("rich"):
        spread = round(statistics.mean(byz["cheap"]) - statistics.mean(byz["rich"]), 2)
    report = {"generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "n_graded": n, "by_zone": summ, "cheap_minus_rich_spread_pp": spread,
              "caveat": "single-regime window so far; partial sample overlap inflates significance"}
    print(json.dumps(report, indent=1))
    with open(OUT, "w") as fh:
        json.dump(report, fh, indent=1)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
