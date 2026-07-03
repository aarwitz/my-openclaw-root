#!/usr/bin/env python3
"""Options-data AUDITION (ThetaData free tier) — decide whether options
positioning earns a paid slot, before paying anyone anything.

Pulls 1yr of bulk EOD option data per underlying (weekly chunks, free-tier
rate-limited), aggregates per (ticker, date):

  opt_pcr_vol      put/call volume ratio (smoothed 5d)
  opt_vol_z        total option volume vs its trailing 60d mean/σ (attention)
  opt_net_prem     (call premium − put premium) / total premium, 5d smoothed
                   premium = Σ close × volume × 100 (dollar-weighted direction)

Writes aggregates to features.sqlite::options_daily (raw, reusable if we later
buy deeper history) and runs the IC screen vs forward 21d SPY-relative returns
(same convention as mechanism_backtest: monthly grid, Spearman rank IC).

Decision rule (DATA_SOURCES.md): any |pooled IC| >= 0.03 -> buy 4yr history
(Massive Options Developer) and run the full FDR backtest. Otherwise walk away.

  python3 options_audit.py pull --top-n 64        # ~3h at free-tier rate
  python3 options_audit.py pull --names AAPL,MU
  python3 options_audit.py screen                  # IC screen on what's pulled
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from connectors import thetadata  # noqa: E402

FEAT = str(Path.home() / ".openclaw/state/features.sqlite")

DDL = """
CREATE TABLE IF NOT EXISTS options_daily (
  ticker TEXT NOT NULL,
  date TEXT NOT NULL,          -- YYYY-MM-DD
  call_vol INTEGER, put_vol INTEGER,
  call_prem REAL, put_prem REAL,
  n_contracts INTEGER,
  source TEXT NOT NULL DEFAULT 'thetadata-free',
  PRIMARY KEY (ticker, date)
);
"""


def _weeks(start: date, end: date):
    cur = start
    while cur <= end:
        wend = min(cur + timedelta(days=6), end)
        yield cur.strftime("%Y%m%d"), wend.strftime("%Y%m%d")
        cur = wend + timedelta(days=1)


def pull(names: list[str], months: int = 12) -> None:
    thetadata.ensure_terminal()
    conn = sqlite3.connect(FEAT)
    conn.execute(DDL)
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=months * 30)
    cols = thetadata.EOD_COLS
    i_close, i_vol, i_date = cols.index("close"), cols.index("volume"), cols.index("date")
    for n, root in enumerate(names, 1):
        # resume: skip weeks already stored
        have = {r[0] for r in conn.execute(
            "SELECT date FROM options_daily WHERE ticker=?", (root,))}
        for ws, we in _weeks(start, end):
            wk_dates = set()
            d0 = date(int(ws[:4]), int(ws[4:6]), int(ws[6:]))
            for k in range(7):
                wk_dates.add((d0 + timedelta(days=k)).isoformat())
            if wk_dates & have and len(wk_dates & have) >= 3:
                continue  # week (mostly) present
            try:
                rows = thetadata.bulk_eod(root, ws, we)
            except Exception as exc:
                print(f"  {root} {ws}: SKIP ({str(exc)[:80]})", flush=True)
                continue
            agg: dict[tuple, list] = {}
            for c in rows:
                right = c["contract"]["right"]
                for t in c["ticks"]:
                    vol = t[i_vol] or 0
                    if not vol:
                        continue
                    dt = str(t[i_date])
                    day = f"{dt[:4]}-{dt[4:6]}-{dt[6:]}"
                    prem = (t[i_close] or 0.0) * vol * 100.0
                    a = agg.setdefault(day, [0, 0, 0.0, 0.0, 0])
                    if right == "C":
                        a[0] += vol; a[2] += prem
                    else:
                        a[1] += vol; a[3] += prem
                    a[4] += 1
            for day, (cv, pv, cp, pp, nc) in agg.items():
                conn.execute(
                    "INSERT OR REPLACE INTO options_daily "
                    "(ticker, date, call_vol, put_vol, call_prem, put_prem, n_contracts) "
                    "VALUES (?,?,?,?,?,?,?)", (root, day, cv, pv, cp, pp, nc))
            conn.commit()
        done = conn.execute("SELECT COUNT(*) FROM options_daily WHERE ticker=?", (root,)).fetchone()[0]
        print(f"[{n}/{len(names)}] {root}: {done} daily rows", flush=True)
    conn.close()


def _series(conn, ticker):
    return conn.execute(
        "SELECT date, call_vol, put_vol, call_prem, put_prem FROM options_daily "
        "WHERE ticker=? ORDER BY date", (ticker,)).fetchall()


def _spearman(xs, ys):
    def rank(v):
        s = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for pos, i in enumerate(s):
            r[i] = pos
        return r
    rx, ry = rank(xs), rank(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    dx = math.sqrt(sum((a - mx) ** 2 for a in rx))
    dy = math.sqrt(sum((b - my) ** 2 for b in ry))
    return num / (dx * dy) if dx and dy else 0.0


def screen() -> None:
    """Pooled monthly cross-sectional IC of each option feature vs fwd 21d
    SPY-relative return (prices via the existing FMP/Alpaca-backed store)."""
    import mechanism_backtest as mb
    conn = sqlite3.connect(FEAT)
    tickers = [r[0] for r in conn.execute(
        "SELECT DISTINCT ticker FROM options_daily")]
    if not tickers:
        print("no options_daily data yet"); return
    print(f"screen universe: {len(tickers)} names")
    spy = mb.load_spy(conn) if hasattr(mb, "load_spy") else None

    feats: dict[str, dict[str, dict[str, float]]] = {}
    for t in tickers:
        rows = _series(conn, t)
        if len(rows) < 90:
            continue
        by = {}
        vols, k = [], 5
        for i, (day, cv, pv, cp, pp) in enumerate(rows):
            w = rows[max(0, i - k + 1): i + 1]
            scv = sum(x[1] or 0 for x in w); spv = sum(x[2] or 0 for x in w)
            scp = sum(x[3] or 0.0 for x in w); spp = sum(x[4] or 0.0 for x in w)
            tot = (cv or 0) + (pv or 0)
            vols.append(tot)
            hist = vols[-61:-1]
            vz = None
            if len(hist) >= 30:
                m = sum(hist) / len(hist)
                sd = math.sqrt(sum((x - m) ** 2 for x in hist) / len(hist))
                vz = (tot - m) / sd if sd else None
            by[day] = {
                "opt_pcr_vol": (spv / scv) if scv else None,
                "opt_vol_z": vz,
                "opt_net_prem": ((scp - spp) / (scp + spp)) if (scp + spp) else None,
            }
        feats[t] = by

    # forward 21d SPY-relative return via the price cache used everywhere else
    td = {t: mb.load_ticker(conn, t) for t in feats}
    spy_td = mb.load_ticker(conn, "SPY")
    import bisect
    def fwd_ret(tk, day):
        d = td[tk]
        dates = d["dates"]
        i = bisect.bisect_left(dates, day)
        if i >= len(dates) or i + 21 >= len(dates):
            return None
        c0, c1 = d["close"][dates[i]], d["close"][dates[i + 21]]
        j = bisect.bisect_left(spy_td["dates"], day)
        if j + 21 >= len(spy_td["dates"]):
            return None
        s0 = spy_td["close"][spy_td["dates"][j]]
        s1 = spy_td["close"][spy_td["dates"][j + 21]]
        if not (c0 and c1 and s0 and s1):
            return None
        return (c1 / c0 - 1) - (s1 / s0 - 1)

    # monthly grid over the audited year
    all_days = sorted({d for by in feats.values() for d in by})
    grid = all_days[30::21]
    results = {}
    for fname in ("opt_pcr_vol", "opt_vol_z", "opt_net_prem"):
        ics = []
        for day in grid:
            xs, ys = [], []
            for tk, by in feats.items():
                v = by.get(day, {}).get(fname)
                if v is None:
                    continue
                r = fwd_ret(tk, day)
                if r is None:
                    continue
                xs.append(v); ys.append(r)
            if len(xs) >= 15:
                ics.append(_spearman(xs, ys))
        if ics:
            m = sum(ics) / len(ics)
            sd = math.sqrt(sum((x - m) ** 2 for x in ics) / len(ics)) or 1e-9
            results[fname] = {"pooled_ic": round(m, 4), "t": round(m / (sd / math.sqrt(len(ics))), 2),
                              "n_rebalances": len(ics)}
    print(json.dumps(results, indent=2))
    verdict = any(abs(v["pooled_ic"]) >= 0.03 for v in results.values())
    msg = ("SIGNAL — worth buying 4yr history for the full FDR backtest" if verdict
           else "no audition signal — walk away, $0 spent")
    print("\nVERDICT: " + msg)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("pull")
    p.add_argument("--names")
    p.add_argument("--top-n", type=int)
    p.add_argument("--months", type=int, default=12)
    sub.add_parser("screen")
    a = ap.parse_args()
    if a.cmd == "pull":
        if a.top_n:
            conn = sqlite3.connect(FEAT)
            names = [r[0] for r in conn.execute(
                "SELECT symbol FROM universe WHERE status='active' AND market_cap IS NOT NULL "
                "ORDER BY market_cap DESC LIMIT ?", (a.top_n,))]
            conn.close()
        else:
            names = [s.strip().upper() for s in (a.names or "").split(",") if s.strip()]
        if not names:
            ap.error("pass --names or --top-n")
        pull(names, months=a.months)
    else:
        screen()


if __name__ == "__main__":
    main()
