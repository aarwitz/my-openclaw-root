#!/usr/bin/env python3
"""Fundamental forecasting loop (D61) — predict FCF/EPS, grade against reported
actuals, calibrate. The recursive pattern applied to fundamentals.

Operator directive (2026-07-10): "we can predict FCF and then learn from how
right/wrong we are and get better over time." Today the desk's DCF uses static
growth assumptions that are never graded. This loop makes fundamentals a
first-class forecast object:

  forecast  — for each name, predict next unreported quarter's FCF and EPS
              (baseline: trailing-4q-median qoq growth applied to the trailing
              quarter, bands from the name's own trailing growth dispersion)
  grade     — when the actual reports (FMP cash-flow/income, acceptedDate =
              point-in-time), resolve: pct error, band hit/miss. Audited.
  backfill  — apply the SAME rule as-of each past quarter and grade instantly:
              bootstraps a real calibration history today instead of waiting
              a quarter for the first report card.
  calibrate — the cost function: per method/sector, median |error| and band
              coverage. When this beats the static assumption, it earns its
              way into valuation.py's growth input (rule-proposal gated).

  python3 fundamental_forecast.py forecast [--names A,B|--book]
  python3 fundamental_forecast.py grade
  python3 fundamental_forecast.py backfill --top-n 100 [--quarters 8]
  python3 fundamental_forecast.py calibrate
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import statistics
import sys
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from connectors import fmp  # noqa: E402

DB = os.path.expanduser("~/.openclaw/state/trading-intel.sqlite")
DDL = """CREATE TABLE IF NOT EXISTS fundamental_forecasts (
  id            TEXT PRIMARY KEY,
  ticker        TEXT NOT NULL,
  as_of         TEXT NOT NULL,           -- when the forecast was made (point-in-time)
  metric        TEXT NOT NULL,           -- 'fcf_q' | 'eps_q'
  period_end    TEXT NOT NULL,           -- fiscal quarter end being forecast
  p10           REAL, p50 REAL NOT NULL, p90 REAL,
  method        TEXT NOT NULL,
  basis_json    TEXT,
  resolved_at   TEXT,
  actual        REAL,
  pct_error     REAL,                    -- (p50-actual)/|actual|
  band_hit      INTEGER,                 -- actual within [p10,p90]
  experiment_id TEXT DEFAULT 'world_model_v1',
  UNIQUE (ticker, metric, period_end, method)
);"""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _conn():
    c = sqlite3.connect(DB, timeout=60)
    c.row_factory = sqlite3.Row
    c.execute(DDL)
    return c


def _series(ticker: str):
    """Quarterly (period_end, accepted_at, fcf, eps), oldest first."""
    cf = fmp._get("/cash-flow-statement", {"symbol": ticker, "period": "quarter", "limit": 40}) or []
    inc = {r["date"]: r for r in (fmp.income_statement(ticker, period="quarter", limit=40) or [])}
    rows = []
    for r in sorted(cf, key=lambda x: x.get("date") or ""):
        d = r.get("date")
        if not d or r.get("freeCashFlow") is None:
            continue
        rows.append({"end": d, "accepted": (r.get("acceptedDate") or "")[:10],
                     "fcf": float(r["freeCashFlow"]),
                     "eps": (float(inc[d]["epsDiluted"]) if d in inc and inc[d].get("epsDiluted") is not None else None)})
    return rows


def _baseline(hist_vals: list[float]) -> tuple[float, float, float, dict] | None:
    """Median-growth continuation with dispersion bands. hist oldest-first, needs >=5."""
    if len(hist_vals) < 5:
        return None
    yoy = []  # same-quarter-last-year growth avoids seasonality
    for i in range(4, len(hist_vals)):
        prev = hist_vals[i - 4]
        if abs(prev) > 1e-6:
            yoy.append(hist_vals[i] / prev - 1.0)
    if len(yoy) < 3:
        return None
    g = statistics.median(yoy[-8:])
    sd = statistics.pstdev(yoy[-8:]) or abs(g) * 0.5 or 0.1
    base = hist_vals[-4]  # same quarter last year
    p50 = base * (1 + g)
    return (base * (1 + g - 1.28 * sd), p50, base * (1 + g + 1.28 * sd),
            {"yoy_median_g": round(g, 4), "yoy_sd": round(sd, 4), "n_obs": len(yoy)})


def _store(conn, ticker, metric, period_end, tup, as_of, dry=False):
    p10, p50, p90, basis = tup
    if dry:
        return
    conn.execute(
        "INSERT OR IGNORE INTO fundamental_forecasts (id, ticker, as_of, metric, period_end, "
        "p10, p50, p90, method, basis_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (f"ff-{uuid.uuid4().hex[:14]}", ticker, as_of, metric, period_end,
         p10, p50, p90, "yoy_median_v1", json.dumps(basis)))


def cmd_forecast(names: list[str]) -> dict:
    conn = _conn()
    made = 0
    for t in names:
        rows = _series(t)
        if len(rows) < 6:
            continue
        # next unreported quarter-end ≈ last end + ~91d
        from datetime import date, timedelta
        nxt = (date.fromisoformat(rows[-1]["end"]) + timedelta(days=91)).isoformat()
        for metric in ("fcf_q", "eps_q"):
            vals = [r["fcf"] if metric == "fcf_q" else r["eps"] for r in rows]
            vals = [v for v in vals if v is not None]
            tup = _baseline(vals)
            if tup:
                _store(conn, t, metric, nxt, tup, _now())
                made += 1
    conn.commit()
    return {"forecasts_made": made, "names": len(names)}


def cmd_backfill(names: list[str], quarters: int) -> dict:
    conn = _conn()
    made = graded = 0
    for t in names:
        rows = _series(t)
        for k in range(quarters, 0, -1):
            cut = len(rows) - k
            if cut < 6:
                continue
            hist, target = rows[:cut], rows[cut]
            as_of = hist[-1]["accepted"] or hist[-1]["end"]  # knowable-at: last report's acceptance
            for metric in ("fcf_q", "eps_q"):
                vals = [r["fcf"] if metric == "fcf_q" else r["eps"] for r in hist]
                vals = [v for v in vals if v is not None]
                tup = _baseline(vals)
                actual = target["fcf"] if metric == "fcf_q" else target["eps"]
                if not tup or actual is None:
                    continue
                p10, p50, p90, basis = tup
                if abs(actual) < 1e-6:
                    continue
                err = (p50 - actual) / abs(actual)
                conn.execute(
                    "INSERT OR IGNORE INTO fundamental_forecasts (id, ticker, as_of, metric, period_end, "
                    "p10, p50, p90, method, basis_json, resolved_at, actual, pct_error, band_hit) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (f"ff-{uuid.uuid4().hex[:14]}", t, as_of, metric, target["end"],
                     p10, p50, p90, "yoy_median_v1", json.dumps(basis),
                     target["accepted"] or target["end"], actual, round(err, 4),
                     1 if (p10 <= actual <= p90) else 0))
                made += 1
                graded += 1
    conn.commit()
    return {"backfilled": made, "graded": graded}


def cmd_grade() -> dict:
    conn = _conn()
    open_fx = conn.execute(
        "SELECT id, ticker, metric, period_end, p10, p50, p90 FROM fundamental_forecasts "
        "WHERE resolved_at IS NULL").fetchall()
    graded = 0
    by_ticker: dict[str, list] = {}
    for r in open_fx:
        by_ticker.setdefault(r["ticker"], []).append(r)
    for t, fxs in by_ticker.items():
        rows = {r["end"]: r for r in _series(t)}
        for fx in fxs:
            # match reported quarter within ±21d of forecast period_end
            from datetime import date, timedelta
            tgt = date.fromisoformat(fx["period_end"])
            match = next((rows[e] for e in rows
                          if abs((date.fromisoformat(e) - tgt).days) <= 21), None)
            if not match:
                continue
            actual = match["fcf"] if fx["metric"] == "fcf_q" else match["eps"]
            if actual is None or abs(actual) < 1e-6:
                continue
            err = (fx["p50"] - actual) / abs(actual)
            conn.execute(
                "UPDATE fundamental_forecasts SET resolved_at=?, actual=?, pct_error=?, band_hit=? WHERE id=?",
                (_now(), actual, round(err, 4),
                 1 if (fx["p10"] or -1e18) <= actual <= (fx["p90"] or 1e18) else 0, fx["id"]))
            conn.execute(
                "INSERT INTO audits (id, timestamp, actor, entity_type, entity_id, action, "
                "before_state, after_state, rationale_concise) VALUES (?,?,'archivist',"
                "'fundamental_forecast',?,'grade_forecast',NULL,'resolved',?)",
                (f"AUDIT-{_now().replace(':','').replace('-','')}-{uuid.uuid4().hex[:8]}", _now(), fx["id"],
                 f"{t} {fx['metric']} {fx['period_end']}: forecast {fx['p50']:.3g} vs actual {actual:.3g} "
                 f"({err:+.1%} error)"))
            graded += 1
    conn.commit()
    return {"open": len(open_fx), "graded": graded}


def cmd_calibrate() -> dict:
    conn = _conn()
    out = {}
    for metric in ("fcf_q", "eps_q"):
        rows = conn.execute(
            "SELECT pct_error, band_hit FROM fundamental_forecasts "
            "WHERE resolved_at IS NOT NULL AND metric=?", (metric,)).fetchall()
        errs = [abs(r["pct_error"]) for r in rows if r["pct_error"] is not None]
        if not errs:
            out[metric] = None
            continue
        out[metric] = {
            "n": len(errs),
            "median_ape": round(statistics.median(errs), 3),
            "band_coverage": round(sum(r["band_hit"] or 0 for r in rows) / len(rows), 3),
            "target_coverage": 0.80,
        }
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("forecast"); f.add_argument("--names"); f.add_argument("--book", action="store_true")
    sub.add_parser("grade")
    b = sub.add_parser("backfill"); b.add_argument("--top-n", type=int, default=100); b.add_argument("--quarters", type=int, default=8)
    sub.add_parser("calibrate")
    a = ap.parse_args()
    if a.cmd == "forecast":
        if a.book:
            conn = _conn()
            names = [r[0] for r in conn.execute(
                "SELECT DISTINCT ticker FROM positions WHERE state IN ('opening','open','scaling')")]
        else:
            names = [s.strip().upper() for s in (a.names or "").split(",") if s.strip()]
        print(json.dumps(cmd_forecast(names)))
    elif a.cmd == "grade":
        print(json.dumps(cmd_grade()))
    elif a.cmd == "backfill":
        feat = sqlite3.connect(os.path.expanduser("~/.openclaw/state/features.sqlite"))
        names = [r[0] for r in feat.execute(
            "SELECT symbol FROM universe WHERE status='active' AND market_cap IS NOT NULL "
            "ORDER BY market_cap DESC LIMIT ?", (a.top_n,))]
        print(json.dumps(cmd_backfill(names, a.quarters)))
    elif a.cmd == "calibrate":
        print(json.dumps(cmd_calibrate(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
