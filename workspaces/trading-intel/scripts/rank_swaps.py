#!/usr/bin/env python3
"""rank_swaps.py — ranked queue-vs-holdings SWAP logic (overseer item pq-2ec10abce13e).

When the book is at the concurrent-names cap, instead of BLOCKING every new idea, rank current HOLDINGS
against the ready CANDIDATES by CURRENT conviction (signal_scan's regime/redundancy/correlation-adjusted
edge) and — only if a ready candidate clears the weakest holding by a margin AND that holding is genuinely
weak (its thesis has decayed) AND the holding isn't a fresh entry — propose a SWAP: exit the weak holding
to free a slot for the stronger idea. Conservative by default; the Risk gate still gates every leg.

  evaluate_swaps(conn) -> [{exit_ticker, exit_qty, exit_pos_id, open_ticker, exit_conv, open_conv, reason}]
  python3 rank_swaps.py        # dry report of what it would swap right now
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
import signal_scan as ss  # noqa: E402

LIVE = os.path.expanduser("~/.openclaw/state/trading-intel.sqlite")
NAME_CAP = int(os.environ.get("SWAP_NAME_CAP", "12"))            # engage only at/over this many names
MAX_SWAPS = int(os.environ.get("SWAP_MAX_PER_PASS", "1"))        # churn guard
MARGIN = float(os.environ.get("SWAP_MARGIN", "4.0"))            # candidate edge must beat holding by >= this
WEAK_FLOOR = float(os.environ.get("SWAP_WEAK_FLOOR", "3.0"))     # only swap OUT holdings below this edge
MIN_HOLD_DAYS = float(os.environ.get("SWAP_MIN_HOLD_DAYS", "3")) # don't churn entries younger than this
HELD_STATES = ("open", "opening", "scaling", "trimming", "closing")


def _conviction(names):
    """Current diversification-aware edge per name (adj_edge from signal_scan; 0 if it fires nothing now)."""
    if not names:
        return {}
    rows, _ = ss.scan(sorted({n.upper() for n in names}), min_fired=1)
    return {r["ticker"].upper(): float(r.get("adj_edge") or 0.0) for r in rows}


def _age_days(opened):
    try:
        dt = datetime.fromisoformat(str(opened).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return 999


def evaluate_swaps(conn):
    held = [(r[0].upper(), r[1], r[2], r[3]) for r in conn.execute(
        f"SELECT ticker, qty, opened_at, id FROM positions WHERE state IN {HELD_STATES}")]
    if len(held) < NAME_CAP:
        return []  # room in the book -> normal flow opens new names; no swap needed
    held_tk = {t for t, _, _, _ in held}
    cands = []
    for (tj,) in conn.execute("SELECT tickers FROM hypotheses WHERE state='ready'"):
        try:
            t = (json.loads(tj) or [None])[0]
        except Exception:
            t = None
        if t and t.upper() not in held_tk:
            cands.append(t.upper())
    cands = list(dict.fromkeys(cands))
    if not cands:
        return []

    conv = _conviction([t for t, _, _, _ in held] + cands)
    holds = sorted(held, key=lambda h: conv.get(h[0], 0.0))            # weakest holding first
    cand_rank = sorted(cands, key=lambda c: -conv.get(c, 0.0))         # strongest candidate first

    swaps, used = [], set()
    for cand in cand_rank:
        if len(swaps) >= MAX_SWAPS:
            break
        cc = conv.get(cand, 0.0)
        for htk, hqty, opened, pid in holds:
            if htk in used:
                continue
            hc = conv.get(htk, 0.0)
            if hc <= WEAK_FLOOR and (cc - hc) >= MARGIN and _age_days(opened) >= MIN_HOLD_DAYS and (hqty or 0) > 0:
                swaps.append({"exit_ticker": htk, "exit_qty": int(hqty), "exit_pos_id": pid,
                              "open_ticker": cand, "exit_conv": round(hc, 2), "open_conv": round(cc, 2),
                              "reason": f"swap {htk} (edge {hc:.1f}) -> {cand} (edge {cc:.1f}, +{cc-hc:.1f})"})
                used.add(htk)
                break
    return swaps


def main():
    conn = sqlite3.connect(LIVE)
    n_held = conn.execute(f"SELECT COUNT(*) FROM positions WHERE state IN {HELD_STATES}").fetchone()[0]
    n_ready = conn.execute("SELECT COUNT(*) FROM hypotheses WHERE state='ready'").fetchone()[0]
    print(f"book: {n_held} names (cap {NAME_CAP}) | ready queue: {n_ready}")
    sw = evaluate_swaps(conn)
    if not sw:
        print("no swaps: " + ("book below cap (room to add directly)" if n_held < NAME_CAP
                              else "no ready candidate clears a weak holding by the margin"))
    for s in sw:
        print("  SWAP:", s["reason"], f"(exit {s['exit_qty']} {s['exit_ticker']})")
    conn.close()


if __name__ == "__main__":
    main()
