#!/usr/bin/env python3
"""Event-conditioned options flow test (D58 continuation, pre-registered).

Unconditional options flow died (0/7 constructions survived FDR, 2026-07-09).
Different hypothesis: flow is informative specifically INTO catalysts — smart
positioning concentrates before earnings. Design: for every earnings event in
the 4y panel, anchor on the report date E:

  signal  = avg opt_net_prem over [E-5, E-2]  (pre-announcement positioning;
            stops at E-2 to avoid any ambiguity about announce-day flow)
  outcome = SPY-relative return E+1 -> E+6 (5td) and E+1 -> E+22 (21td)

Pooled Spearman IC across all events, t from per-quarter clustering (events
cluster in earnings season — naive per-event t would overstate independence).
Bar: |IC| >= 0.03. These are tests #8-9 on this panel; BH-FDR applied across
all nine before any promotion.

  python3 options_event_test.py [--max-names N]
"""

from __future__ import annotations

import argparse
import bisect
import json
import math
import os
import sqlite3
import statistics
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from connectors import fmp  # noqa: E402
import mechanism_backtest as mb  # noqa: E402

FEAT = os.path.expanduser("~/.openclaw/state/features.sqlite")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-names", type=int, default=600)
    a = ap.parse_args()
    conn = sqlite3.connect(FEAT)

    tickers = [r[0] for r in conn.execute(
        "SELECT ticker, COUNT(*) n FROM options_daily GROUP BY ticker "
        "HAVING n > 400 ORDER BY n DESC LIMIT ?", (a.max_names,))]
    print(f"universe: {len(tickers)} names", file=sys.stderr)

    spy = mb.load_ticker(conn, "SPY")
    events = []  # (quarter_key, signal, ret5, ret21)
    skipped = {"no_flow": 0, "no_price": 0, "no_earnings": 0}
    for i, t in enumerate(tickers):
        flow = {r[0]: (r[1], r[2]) for r in conn.execute(
            "SELECT date, call_prem, put_prem FROM options_daily WHERE ticker=?", (t,))}
        fdates = sorted(flow)
        if len(fdates) < 200:
            skipped["no_flow"] += 1
            continue
        try:
            earn = [r["date"] for r in fmp.earnings(t, limit=40)
                    if r.get("date") and r.get("epsActual") is not None
                    and fdates[0] <= r["date"] <= fdates[-1]]
        except Exception:
            skipped["no_earnings"] += 1
            continue
        try:
            td = mb.load_ticker(conn, t)
        except Exception:
            skipped["no_price"] += 1
            continue
        dk = td["dates"]
        for E in earn:
            # signal window [E-5, E-2] calendar -> take flow rows present
            e = date.fromisoformat(E)
            win = [(flow[d]) for d in fdates
                   if (e - timedelta(days=5)).isoformat() <= d <= (e - timedelta(days=2)).isoformat()]
            cp = sum(x[0] or 0 for x in win)
            pp = sum(x[1] or 0 for x in win)
            if cp + pp <= 0:
                continue
            sig = (cp - pp) / (cp + pp)
            # outcome: E+1 trading day entry
            j = bisect.bisect_right(dk, E)
            if j + 22 >= len(dk):
                continue
            sj = bisect.bisect_right(spy["dates"], E)
            if sj + 22 >= len(spy["dates"]):
                continue
            c0 = td["close"][dk[j]]
            s0 = spy["close"][spy["dates"][sj]]
            if not c0 or not s0:
                continue
            r5 = (td["close"][dk[j + 5]] / c0 - 1) - (spy["close"][spy["dates"][sj + 5]] / s0 - 1)
            r21 = (td["close"][dk[j + 21]] / c0 - 1) - (spy["close"][spy["dates"][sj + 21]] / s0 - 1)
            events.append((E[:7], sig, r5, r21))
        if (i + 1) % 100 == 0:
            print(f"  {i+1}/{len(tickers)} names, {len(events)} events", file=sys.stderr, flush=True)

    def spearman(pairs):
        xs = [p[0] for p in pairs]; ys = [p[1] for p in pairs]
        rx = {v: k for k, v in enumerate(sorted(range(len(xs)), key=lambda i: xs[i]))}
        ry = {v: k for k, v in enumerate(sorted(range(len(ys)), key=lambda i: ys[i]))}
        xr = [rx[i] for i in range(len(xs))]; yr = [ry[i] for i in range(len(ys))]
        mx, my = sum(xr)/len(xr), sum(yr)/len(yr)
        num = sum((p-mx)*(q-my) for p, q in zip(xr, yr))
        den = math.sqrt(sum((p-mx)**2 for p in xr) * sum((q-my)**2 for q in yr))
        return num/den if den else 0.0

    out = {"events": len(events), "skipped": skipped}
    for label, idx in (("post_earnings_5d", 2), ("post_earnings_21d", 3)):
        # per-quarter (cluster) ICs
        byq = {}
        for q, sig, r5, r21 in events:
            byq.setdefault(q, []).append((sig, (r5, r21)[idx - 2]))
        qics = [spearman(v) for v in byq.values() if len(v) >= 30]
        if len(qics) < 8:
            out[label] = {"error": "insufficient clusters", "clusters": len(qics)}
            continue
        m = sum(qics)/len(qics); sd = statistics.pstdev(qics)
        out[label] = {"pooled_ic": round(m, 4),
                      "t": round(m/(sd/math.sqrt(len(qics))), 2) if sd else None,
                      "n_quarters": len(qics)}
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
