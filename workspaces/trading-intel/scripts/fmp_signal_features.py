#!/usr/bin/env python3
"""New FMP-derived, POINT-IN-TIME signals into features.sqlite (source='fmp'):
  - days_to_earnings    : trading-day distance to the next scheduled earnings (proximity drift).
                          Earnings dates are announced ahead -> knowable; capped at 63.
  - analyst_rating_score: weighted consensus from grades-historical (latest row <= D), range ~[-2,2].
  - analyst_rating_chg_63d : 3-mo change in the rating score (revision momentum).

All stamped as_of = knowable_at = the trading date D, using only data dated <= D (no look-ahead).
Validation-first: populates only the names you pass; a feature reaches sizing only after the FDR
backtest confirms edge (then add to GEN_FEATURES + full rollout via feature_store).

  python3 fmp_signal_features.py --names AAPL,NVDA,...
"""
from __future__ import annotations
import argparse, bisect, os, sqlite3, sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from connectors import fmp  # noqa: E402

FEAT = os.path.expanduser("~/.openclaw/state/features.sqlite")


def _score(r: dict):
    sb = r.get("analystRatingsStrongBuy") or 0; b = r.get("analystRatingsBuy") or 0
    h = r.get("analystRatingsHold") or 0; s = r.get("analystRatingsSell") or 0
    ss = r.get("analystRatingsStrongSell") or 0
    tot = sb + b + h + s + ss
    return (2 * sb + b - s - 2 * ss) / tot if tot else None


def build(names):
    conn = sqlite3.connect(FEAT); total = 0
    for sym in names:
        dates = [r[0] for r in conn.execute(
            "SELECT DISTINCT as_of FROM features WHERE ticker=? AND source='price' ORDER BY as_of", (sym,))]
        if not dates:
            print(f"  {sym}: no price dates, skip", flush=True); continue
        try:
            earn = sorted({(e.get("date") or "")[:10] for e in fmp.earnings(sym) if e.get("date")})
        except Exception:
            earn = []
        try:
            grades = sorted((g["date"][:10], _score(g)) for g in fmp.grades_historical(sym)
                            if g.get("date") and _score(g) is not None)
        except Exception:
            grades = []
        gd = [d for d, _ in grades]

        def rating_at(D):
            j = bisect.bisect_right(gd, D) - 1
            return grades[j][1] if j >= 0 else None

        rows = []
        for k, D in enumerate(dates):
            i = bisect.bisect_right(earn, D)
            if i < len(earn):
                dte = (date.fromisoformat(earn[i]) - date.fromisoformat(D)).days
                if dte >= 0:
                    rows.append((sym, D, "days_to_earnings", float(min(dte, 63)), D, "fmp"))
            sc = rating_at(D)
            if sc is not None:
                rows.append((sym, D, "analyst_rating_score", round(sc, 4), D, "fmp"))
                if k >= 63:
                    past = rating_at(dates[k - 63])
                    if past is not None:
                        rows.append((sym, D, "analyst_rating_chg_63d", round(sc - past, 4), D, "fmp"))
        conn.executemany(
            "INSERT OR REPLACE INTO features(ticker,as_of,name,value,knowable_at,source) VALUES(?,?,?,?,?,?)", rows)
        conn.commit(); total += len(rows)
        print(f"  {sym}: earnings={len(earn)} grades={len(grades)} -> {len(rows)} rows", flush=True)
    print(f"done: {total} fmp-signal rows across {len(names)} names")


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--names", required=True)
    a = ap.parse_args()
    build([s.strip().upper() for s in a.names.split(",") if s.strip()])


if __name__ == "__main__":
    main()
