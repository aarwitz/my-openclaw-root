#!/usr/bin/env python3
"""Catalyst-contagion alert (#1) — when a name moves on a catalyst, surface who is likely NEXT by
traversing the knowledge graph: co-narrative (`co_mentioned_with`, PMI-weighted), co-movement
(`correlated_with`), and shared themes (`catalyst_for`). Writes a contagion brief for the LLM
catalyst-research agent to investigate. The operator's pattern: Blue Origin fails → check ASTS;
Treasury buys MP → check LAC/ALB; Dell wins a Pentagon deal → check other defense names.

  python3 contagion_scan.py --tickers MP,DELL          # contagion neighbours of explicit movers
  python3 contagion_scan.py --auto                     # detect today's abnormal movers, then contagion
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspaces/trading-intel/scripts"))))
import feature_store as fs       # noqa: E402
import signal_scan as ss         # noqa: E402

FEAT = os.path.expanduser("~/.openclaw/state/features.sqlite")
OUT = os.path.expanduser("~/.openclaw/state/contagion_brief.json")


def contagion_neighbors(ticker, limit=10):
    """Rank the KG neighbours most exposed to `ticker`'s catalyst. Score blends co-narrative (PMI),
    co-movement (corr ×4 to scale into PMI range), and shared specific themes."""
    c = sqlite3.connect(FEAT)
    tid = f"ticker:{ticker.upper()}"
    score, why, theme_ct = {}, {}, {}

    def add(nb, s, reason):
        score[nb] = score.get(nb, 0.0) + s
        if reason:
            why.setdefault(nb, []).append(reason)

    # direct evidence dominates: co-narrative (PMI, ~1.5-6) + co-movement (corr×4, ~1.8-4)
    for src, dst, w in c.execute("SELECT src,dst,weight FROM kg_edges WHERE rel='co_mentioned_with' "
                                 "AND (src=? OR dst=?)", (tid, tid)):
        add((dst if src == tid else src).split(":")[1], float(w), f"co-narrative(PMI {float(w):.1f})")
    for src, dst, w in c.execute("SELECT src,dst,weight FROM kg_edges WHERE rel='correlated_with' "
                                 "AND (src=? OR dst=?)", (tid, tid)):
        add((dst if src == tid else src).split(":")[1], float(w) * 4.0, f"co-move(corr {float(w):.2f})")
    # shared themes are WEAKER evidence — count them but cap total contribution so hub names with many
    # generic-ish shared themes don't dominate names with strong direct co-move/co-narrative links.
    themes = [r[0] for r in c.execute("SELECT src FROM kg_edges WHERE dst=? AND rel='catalyst_for'", (tid,))]
    for th in themes:
        for (dst,) in c.execute("SELECT dst FROM kg_edges WHERE src=? AND rel='catalyst_for' AND dst!=?",
                                (th, tid)):
            nb = dst.split(":")[1]
            theme_ct[nb] = theme_ct.get(nb, 0) + 1
            if len(why.get(nb, [])) < 4:
                why.setdefault(nb, []).append(f"theme:{th.split(':')[1]}")
    for nb, ct in theme_ct.items():
        add(nb, min(0.5 * ct, 3.0), None)            # capped theme contribution
    c.close()
    ranked = sorted(score.items(), key=lambda x: -x[1])[:limit]
    return [{"ticker": t, "score": round(s, 2), "why": sorted(set(why[t]))[:4]} for t, s in ranked]


def detect_movers(names, ret_thresh=0.06):
    """Abnormal 5-day movers = |5d return − SPY 5d return| >= threshold (the catalyst candidates)."""
    spy = {b["t"]: b["c"] for b in fs._prices("SPY", 60) if b.get("c")}
    sd = sorted(spy)
    spy_ret = (spy[sd[-1]] / spy[sd[-6]] - 1) if len(sd) >= 6 else 0.0
    movers = []
    for t in names:
        try:
            px = [b["c"] for b in fs._prices(t, 60) if b.get("c")]
            if len(px) >= 6:
                rel = (px[-1] / px[-6] - 1) - spy_ret
                if abs(rel) >= ret_thresh:
                    movers.append((t, round(rel * 100, 1)))
        except Exception:
            pass
    return sorted(movers, key=lambda x: -abs(x[1]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default="")
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--watchlist", default=",".join(ss.DEFAULT_WATCHLIST))
    ap.add_argument("--ret-thresh", type=float, default=0.06)
    a = ap.parse_args()

    if a.tickers:
        movers = [(t.strip().upper(), None) for t in a.tickers.split(",") if t.strip()]
    elif a.auto:
        movers = detect_movers([s.strip().upper() for s in a.watchlist.split(",") if s.strip()], a.ret_thresh)
    else:
        movers = detect_movers([s.strip().upper() for s in a.watchlist.split(",") if s.strip()], a.ret_thresh)

    out = {"generated": datetime.now(timezone.utc).isoformat() + "Z", "movers": []}
    print(f"CATALYST-CONTAGION BRIEF ({len(movers)} mover(s))")
    for t, mv in movers:
        nbrs = contagion_neighbors(t)
        out["movers"].append({"ticker": t, "move_pct": mv, "contagion": nbrs})
        head = f"{t}" + (f" ({mv:+.1f}% vs SPY, 5d)" if mv is not None else "")
        print(f"\n  {head} → likely-affected neighbours (investigate for the same catalyst):")
        for n in nbrs:
            print(f"     {n['ticker']:6} score {n['score']:>5}  [{', '.join(n['why'])}]")
    json.dump(out, open(OUT, "w"), indent=2)
    print(f"\n-> {OUT}")


if __name__ == "__main__":
    main()
