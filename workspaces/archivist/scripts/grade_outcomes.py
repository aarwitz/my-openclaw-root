#!/usr/bin/env python3
"""Close the live learning loop (the keystone).

Nothing in the desk set `hypotheses.resolved_state`, so `calibrate.resolve_predictions` (which gates
on it) was idle and mechanism posteriors never updated from live trades. This grader fixes that:
for each unresolved hypothesis whose prediction horizon has elapsed, it computes the realized
MARKET-RELATIVE return (name vs SPY) from prices, grades the thesis, and sets `resolved_state`
+ `resolved_at` + `archivist_grade`. `calibrate.py` then folds the outcome into per-mechanism
observations and Beta posteriors. Deterministic; pure price math; safe to re-run.

  python3 grade_outcomes.py [--dry-run] [--db PATH]
"""

from __future__ import annotations

import argparse
import bisect
import os
import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspaces/trading-intel/scripts"))))
sys.path.insert(0, str(Path(os.path.expanduser("~/.openclaw/workspaces/quant/scripts"))))
import worldmodel as wm          # noqa: E402
import feature_store as fs       # noqa: E402
import predict                   # noqa: E402

DEAD = 0.0                       # market-relative dead-band (excess beyond this counts directionally)


def _series(sym):
    # Grading runs right after the close and needs today's bar; fs._prices serves
    # a morning-warmed 12h cache that still ends yesterday, silently deferring
    # every maturity by a day. Demand near-fresh bars, fall back to the cache.
    px = None
    try:
        from connectors import massive
        if massive.available():
            px = massive.daily_bars(sym, cache_h=0.25)
    except Exception:
        px = None
    if not px:
        px = fs._prices(sym, 4000)
    return [b["t"] for b in px], {b["t"]: b["c"] for b in px}


def _window_ret(dates, close, entry_iso, H):
    """Return (entry_date, exit_date, return) for entry = first trading day >= entry_iso, exit = +H."""
    i = bisect.bisect_left(dates, entry_iso)
    if i >= len(dates):
        return None
    j = i + H
    if j >= len(dates):
        return None
    c0, c1 = close[dates[i]], close[dates[j]]
    if not c0:
        return None
    return dates[i], dates[j], c1 / c0 - 1


def grade(db, dry_run=False):
    conn = sqlite3.connect(db, timeout=60.0)
    conn.row_factory = sqlite3.Row
    today = date.today().isoformat()
    spy_dates, spy_close = _series("SPY")
    price_cache: dict[str, tuple] = {}

    hyps = conn.execute(
        "SELECT DISTINCT h.id, h.tickers, h.thesis_summary FROM hypotheses h "
        "JOIN predictions p ON p.hypothesis_id = h.id "
        "WHERE h.resolved_state IS NULL AND p.resolved_at IS NULL").fetchall()

    graded, skipped = [], 0
    for h in hyps:
        preds = conn.execute(
            "SELECT id, predicted_at, horizon, return_p10, return_p90 FROM predictions "
            "WHERE hypothesis_id=? AND resolved_at IS NULL ORDER BY predicted_at", (h["id"],)).fetchall()
        if not preds:
            continue
        ticker = predict._first_ticker(h["tickers"])
        direction = predict.thesis_direction(h["thesis_summary"] or "")
        if not ticker:
            skipped += 1
            continue
        if ticker not in price_cache:
            try:
                price_cache[ticker] = _series(ticker)
            except Exception:
                price_cache[ticker] = None
        if not price_cache[ticker]:
            skipped += 1
            continue
        t_dates, t_close = price_cache[ticker]

        p0 = preds[0]
        H = wm.HORIZON_DAYS.get(p0["horizon"], 15)
        entry_iso = p0["predicted_at"][:10]
        tr = _window_ret(t_dates, t_close, entry_iso, H)
        sr = _window_ret(spy_dates, spy_close, entry_iso, H)
        if tr is None or sr is None or tr[1] > today:     # not matured (or no data through exit)
            continue
        _, d_ex, t_ret = tr
        s_ret = sr[2]
        excess = t_ret - s_ret
        hit = (excess < -DEAD) if direction == "short" else (excess > DEAD)
        within = (p0["return_p10"] is not None and p0["return_p90"] is not None
                  and p0["return_p10"] <= t_ret <= p0["return_p90"])
        rs = "wrong" if not hit else ("correct_right_reasons" if within else "correct_wrong_reasons")
        note = f"ret={t_ret:+.3f} spy={s_ret:+.3f} excess={excess:+.3f} dir={direction} -> {rs}"
        if not dry_run:
            conn.execute("UPDATE hypotheses SET resolved_state=?, resolved_at=?, archivist_grade=?, "
                         "state='resolved' WHERE id=?", (rs, f"{d_ex}T00:00:00Z", note, h["id"]))
            for pr in preds:
                # realized_excess_pct feeds calibrate's dead-band (|excess|<=50bps
                # -> 'inconclusive', no mechanism observation) and per-mechanism
                # expectancy (rp-payoff-aware-grading-20260715).
                conn.execute("UPDATE predictions SET realized_return_pct=?, realized_excess_pct=? "
                             "WHERE id=?",
                             (round(100 * t_ret, 3), round(100 * excess, 3), pr["id"]))
            conn.commit()
        graded.append((h["id"], ticker, direction, rs, round(excess, 3)))

    print(f"grade_outcomes: graded {len(graded)} hypotheses ({skipped} skipped: no ticker/price); "
          f"{'DRY-RUN' if dry_run else 'committed'}")
    for g in graded[:25]:
        print("  ", g)
    conn.close()
    return graded


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--db", default=os.path.expanduser("~/.openclaw/state/trading-intel.sqlite"))
    a = ap.parse_args()
    grade(a.db, a.dry_run)


if __name__ == "__main__":
    main()
