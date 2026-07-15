#!/usr/bin/env python3
"""Market x-ray — the system audits its OWN blindness instead of waiting for
the operator to point at the tape (the pattern that motivated this, 2026-07-15:
every blind spot so far — the hw/sw seesaw, the fill hole, attribution — was
found by Aaron's eyes, never by the system).

Each night it decomposes what the market actually did along the standard,
well-known dimensions, flags the extreme ones, and then asks the only question
that matters: DID ANY PART OF THE DESK ENGAGE WITH THIS? A big realized
phenomenon that no hypothesis, debrief, or mechanism touched is a measured
BLIND SPOT — surfaced nightly in the chain and reviewed weekly by the Sunday
audit (which files improvement issues from it).

Metrics (deterministic, feature-store prices + point-in-time factor values):
  breadth_ew_minus_spy   equal-weight sample return minus SPY (concentration)
  dispersion_xs          cross-sectional stdev of daily returns (stock-picking tape?)
  avg_pairwise_corr_21d  correlation regime (macro-driven vs idiosyncratic)
  factor_mom_spread      daily top-minus-bottom mom_12_1 quintile return
  factor_sent_spread     same for news_sent_30d
  spy_vol_21d            realized index vol, annualized %

notable = |z| >= 2 vs the trailing 252d distribution.
blind   = notable AND no engagement (hypotheses/debriefs near the date, or an
          active observable that owns the dimension) matched the metric's
          keyword family.

CLI:
  python3 market_xray.py snapshot          # compute + persist recent window
  python3 market_xray.py report --days 7   # blind spots for the audit
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import feature_store as fs  # noqa: E402

DB = os.path.expanduser("~/.openclaw/state/trading-intel.sqlite")
FEAT_DB = os.path.expanduser("~/.openclaw/state/features.sqlite")

SAMPLE_N = 200            # top names by market cap — the "market" for x-ray purposes
LOOKBACK_D = 340          # trading days of metric history kept in memory
DIST_WIN = 252
CORR_WIN = 21
QUINTILE = 5
NOTABLE_Z = 2.0
PERSIST_DAYS = 30

# Engagement vocabulary: if a metric goes notable, we search recent hypotheses
# and market debriefs for these tokens. Crude on purpose — v1 measures whether
# the desk said ANYTHING about the dimension, not whether it was insightful.
KEYWORDS: dict[str, list[str]] = {
    "breadth_ew_minus_spy": ["breadth", "equal weight", "equal-weight", "concentration",
                             "megacap", "mega-cap", "small cap", "small-cap", "narrow"],
    "dispersion_xs": ["dispersion", "idiosyncratic", "stock-pick", "stock pick"],
    "avg_pairwise_corr_21d": ["correlation", "decoupl", "rotation", "seesaw", "macro-driven"],
    "factor_mom_spread": ["momentum", "trend follow", "winners", "losers", "mom_12_1", "reversal"],
    "factor_sent_spread": ["sentiment", "news-driven", "news flow"],
    "spy_vol_21d": ["volatility", "vix", "vol regime", "calm", "drawdown"],
    # second-loop metrics: high unexplained share = the ontology is missing a
    # driver of OUR OWN P&L. Engagement = a debrief/hypothesis discussing it.
    "unexplained_share_60d": ["unexplained", "residual", "attribution", "unknown driver"],
    "desk_resid_z": ["unexplained", "residual", "attribution", "idiosyncratic"],
}
# Dimensions a dedicated observable already owns (engagement by construction).
OWNED_BY_OBSERVABLE = {"avg_pairwise_corr_21d": "rotation_snapshots"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sample_tickers() -> list[str]:
    c = sqlite3.connect(FEAT_DB)
    rows = c.execute(
        "SELECT symbol FROM universe WHERE status='active' AND market_cap IS NOT NULL "
        "ORDER BY market_cap DESC LIMIT ?", (SAMPLE_N,)).fetchall()
    c.close()
    return [r[0].upper() for r in rows]


def _returns(tickers: list[str]) -> tuple[list[str], dict[str, dict[str, float]]]:
    """(common dates, ticker -> {date: daily return}) over the lookback."""
    per: dict[str, dict[str, float]] = {}
    for t in tickers:
        try:
            px = fs._prices(t, LOOKBACK_D + 60)
        except Exception:
            continue
        if len(px) < 120:
            continue
        dts = [b["t"][:10] for b in px]
        cls = {b["t"][:10]: float(b["c"]) for b in px}
        r = {}
        for i in range(1, len(dts)):
            c0 = cls[dts[i - 1]]
            if c0:
                r[dts[i]] = cls[dts[i]] / c0 - 1.0
        per[t] = r
    counts: dict[str, int] = {}
    for r in per.values():
        for d in r:
            counts[d] = counts.get(d, 0) + 1
    # a date counts if >=80% of the sample priced it (avoids IPO/holiday holes)
    dates = sorted(d for d, n in counts.items() if n >= 0.8 * len(per))[-LOOKBACK_D:]
    return dates, per


def _factor_values(name: str, tickers: list[str], start: str) -> dict[str, list[tuple[str, float]]]:
    """ticker -> [(as_of, value)] ascending, for point-in-time ranks."""
    c = sqlite3.connect(FEAT_DB)
    q = ("SELECT ticker, as_of, value FROM features WHERE name=? AND as_of>=? AND ticker IN ({}) "
         "ORDER BY ticker, as_of").format(",".join("?" * len(tickers)))
    out: dict[str, list[tuple[str, float]]] = {}
    for t, a, v in c.execute(q, [name, start] + list(tickers)):
        if v is not None:
            out.setdefault(t.upper(), []).append((a, float(v)))
    c.close()
    return out


def _latest_before(series: list[tuple[str, float]], d: str) -> float | None:
    val = None
    for a, v in series:
        if a < d:
            val = v
        else:
            break
    return val


def _factor_spread(dates, rets, fvals) -> dict[str, float]:
    """Daily top-minus-bottom quintile return using values known BEFORE the day."""
    out = {}
    for d in dates:
        ranked = []
        for t, r in rets.items():
            if d not in r or t not in fvals:
                continue
            v = _latest_before(fvals[t], d)
            if v is not None:
                ranked.append((v, r[d]))
        if len(ranked) < 50:
            continue
        ranked.sort(key=lambda x: x[0])
        k = len(ranked) // QUINTILE
        bot = sum(x[1] for x in ranked[:k]) / k
        top = sum(x[1] for x in ranked[-k:]) / k
        out[d] = (top - bot) * 100
    return out


def compute_metrics() -> dict[str, dict[str, float]]:
    tickers = _sample_tickers()
    dates, rets = _returns(tickers)
    spy_px = fs._prices("SPY", LOOKBACK_D + 60)
    spy_c = {b["t"][:10]: float(b["c"]) for b in spy_px}
    spy_dts = [b["t"][:10] for b in spy_px]
    spy_r = {spy_dts[i]: spy_c[spy_dts[i]] / spy_c[spy_dts[i - 1]] - 1.0
             for i in range(1, len(spy_dts)) if spy_c[spy_dts[i - 1]]}

    metrics: dict[str, dict[str, float]] = {k: {} for k in KEYWORDS}
    ew_hist: dict[str, float] = {}
    for d in dates:
        day = [r[d] for r in rets.values() if d in r]
        if len(day) < 50 or d not in spy_r:
            continue
        ew = sum(day) / len(day)
        ew_hist[d] = ew
        metrics["breadth_ew_minus_spy"][d] = (ew - spy_r[d]) * 100
        mu = ew
        metrics["dispersion_xs"][d] = math.sqrt(
            sum((x - mu) ** 2 for x in day) / (len(day) - 1)) * 100

    # correlation regime + index vol over rolling windows
    ds = [d for d in dates if d in ew_hist]
    for i in range(CORR_WIN, len(ds)):
        win = ds[i - CORR_WIN + 1:i + 1]
        pvar_series = [ew_hist[d] for d in win]
        pmu = sum(pvar_series) / len(pvar_series)
        pvar = sum((x - pmu) ** 2 for x in pvar_series) / (len(pvar_series) - 1)
        ind_vars = []
        for t, r in rets.items():
            xs = [r[d] for d in win if d in r]
            if len(xs) == len(win):
                m = sum(xs) / len(xs)
                ind_vars.append(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))
        if len(ind_vars) < 50:
            continue
        mvar = sum(ind_vars) / len(ind_vars)
        n = len(ind_vars)
        if mvar > 0:
            rho = (n * pvar / mvar - 1) / (n - 1)
            metrics["avg_pairwise_corr_21d"][ds[i]] = max(-1.0, min(1.0, rho))
        sxs = [spy_r[d] for d in win if d in spy_r]
        if len(sxs) >= CORR_WIN - 2:
            sm = sum(sxs) / len(sxs)
            svar = sum((x - sm) ** 2 for x in sxs) / (len(sxs) - 1)
            metrics["spy_vol_21d"][ds[i]] = math.sqrt(svar * 252) * 100

    start = dates[0] if dates else "2025-01-01"
    metrics["factor_mom_spread"] = _factor_spread(dates, rets, _factor_values("mom_12_1", tickers, start))
    metrics["factor_sent_spread"] = _factor_spread(dates, rets, _factor_values("news_sent_30d", tickers, start))
    return metrics


def _engaged(conn, metric: str, d: str) -> bool:
    if metric in OWNED_BY_OBSERVABLE:
        return True
    kws = KEYWORDS[metric]
    like = " OR ".join("lower(thesis_summary) LIKE ?" for _ in kws)
    row = conn.execute(
        f"SELECT 1 FROM hypotheses WHERE created_at >= date(?, '-3 days') "
        f"AND created_at <= date(?, '+3 days') AND ({like}) LIMIT 1",
        [d, d] + [f"%{k}%" for k in kws]).fetchone()
    if row:
        return True
    try:
        like2 = " OR ".join("lower(headline || ' ' || coalesce(lesson,'')) LIKE ?" for _ in kws)
        row = conn.execute(
            f"SELECT 1 FROM market_events WHERE event_date >= date(?, '-3 days') "
            f"AND event_date <= date(?, '+3 days') AND ({like2}) LIMIT 1",
            [d, d] + [f"%{k}%" for k in kws]).fetchone()
        return bool(row)
    except sqlite3.OperationalError:
        return False


def _ols(y: list[float], xs: list[list[float]]) -> tuple[list[float], float] | None:
    """Tiny OLS (intercept + k regressors) via normal equations. Returns
    (betas, r2) or None if the system is singular/underdetermined."""
    n, k = len(y), len(xs)
    if n < 3 * (k + 1):
        return None
    X = [[1.0] + [xs[j][i] for j in range(k)] for i in range(n)]
    m = k + 1
    A = [[sum(X[r][i] * X[r][j] for r in range(n)) for j in range(m)] for i in range(m)]
    b = [sum(X[r][i] * y[r] for r in range(n)) for i in range(m)]
    for col in range(m):                      # gaussian elimination, partial pivot
        piv = max(range(col, m), key=lambda r: abs(A[r][col]))
        if abs(A[piv][col]) < 1e-12:
            return None
        A[col], A[piv] = A[piv], A[col]
        b[col], b[piv] = b[piv], b[col]
        for r in range(col + 1, m):
            f = A[r][col] / A[col][col]
            for c in range(col, m):
                A[r][c] -= f * A[col][c]
            b[r] -= f * b[col]
    beta = [0.0] * m
    for i in range(m - 1, -1, -1):
        beta[i] = (b[i] - sum(A[i][j] * beta[j] for j in range(i + 1, m))) / A[i][i]
    yhat = [sum(beta[j] * X[r][j] for j in range(m)) for r in range(n)]
    my = sum(y) / n
    ss_tot = sum((v - my) ** 2 for v in y)
    ss_res = sum((y[r] - yhat[r]) ** 2 for r in range(n))
    if ss_tot <= 0:
        return None
    return beta, 1.0 - ss_res / ss_tot


def compute_residual(metrics: dict[str, dict[str, float]]) -> dict[str, dict[str, float]]:
    """The SECOND LOOP's fuel gauge: how much of the desk's own daily return the
    system's ontology (SPY beta + its measured factor/breadth dimensions) fails
    to explain. A persistently high unexplained share is measured evidence that
    something OUTSIDE the current frame is driving our P&L — the trigger for
    growing the frame, without knowing in advance what's missing."""
    conn = sqlite3.connect(DB, timeout=60.0)
    eq = dict(conn.execute(
        "SELECT date, equity FROM book_equity WHERE book='desk' ORDER BY date"))
    conn.close()
    spy_px = fs._prices("SPY", LOOKBACK_D + 60)
    spy_c = {b["t"][:10]: float(b["c"]) for b in spy_px}
    spy_dts = [b["t"][:10] for b in spy_px]
    spy_r = {spy_dts[i]: (spy_c[spy_dts[i]] / spy_c[spy_dts[i - 1]] - 1.0) * 100
             for i in range(1, len(spy_dts)) if spy_c[spy_dts[i - 1]]}

    eq_dates = sorted(eq)
    desk_r = {}
    for i in range(1, len(eq_dates)):
        d0, d1 = eq_dates[i - 1], eq_dates[i]
        if d1 in spy_r and eq[d0]:            # trading days only (weekend rows are flat)
            desk_r[d1] = (eq[d1] / eq[d0] - 1.0) * 100

    regressors = ["factor_mom_spread", "factor_sent_spread", "breadth_ew_minus_spy"]
    dates = sorted(d for d in desk_r
                   if d in spy_r and all(d in metrics[m] for m in regressors))
    out: dict[str, dict[str, float]] = {"unexplained_share_60d": {}, "desk_resid_z": {}}
    WIN = 60
    for i in range(len(dates)):
        win = dates[max(0, i - WIN + 1):i + 1]
        if len(win) < 30:
            continue
        y = [desk_r[d] for d in win]
        xs = [[spy_r[d] for d in win]] + [[metrics[m][d] for d in win] for m in regressors]
        fit = _ols(y, xs)
        if fit is None:
            continue
        beta, r2 = fit
        out["unexplained_share_60d"][dates[i]] = round((1.0 - r2) * 100, 3)
        # today's residual, standardized against the window's residuals
        resids = []
        for d in win:
            pred = beta[0] + beta[1] * spy_r[d] + sum(
                beta[2 + j] * metrics[m][d] for j, m in enumerate(regressors))
            resids.append(desk_r[d] - pred)
        mu = sum(resids) / len(resids)
        sd = math.sqrt(sum((x - mu) ** 2 for x in resids) / (len(resids) - 1))
        if sd > 0:
            out["desk_resid_z"][dates[i]] = round(resids[-1] / sd, 3)
    return out


def _ensure_table(conn) -> None:
    conn.execute("""
      CREATE TABLE IF NOT EXISTS market_xray (
        date        TEXT NOT NULL,
        metric      TEXT NOT NULL,
        value       REAL,
        z           REAL,
        notable     INTEGER NOT NULL DEFAULT 0,
        engaged     INTEGER,
        blind       INTEGER NOT NULL DEFAULT 0,
        computed_at TEXT NOT NULL,
        PRIMARY KEY (date, metric)
      )""")


def snapshot() -> dict:
    metrics = compute_metrics()
    metrics.update(compute_residual(metrics))
    conn = sqlite3.connect(DB, timeout=60.0)
    _ensure_table(conn)
    summary = {}
    for metric, series in metrics.items():
        ds = sorted(series)
        vals = [series[d] for d in ds]
        for i, d in enumerate(ds[-PERSIST_DAYS:], start=len(ds) - min(PERSIST_DAYS, len(ds))):
            hist = vals[max(0, i - DIST_WIN):i]
            z = None
            if len(hist) >= 60:
                mu = sum(hist) / len(hist)
                sd = math.sqrt(sum((x - mu) ** 2 for x in hist) / (len(hist) - 1))
                z = (vals[i] - mu) / sd if sd > 0 else None
            notable = int(z is not None and abs(z) >= NOTABLE_Z)
            engaged = _engaged(conn, metric, ds[i]) if notable else None
            blind = int(notable and not engaged)
            conn.execute(
                "INSERT OR REPLACE INTO market_xray (date, metric, value, z, notable, engaged, "
                "blind, computed_at) VALUES (?,?,?,?,?,?,?,?)",
                (ds[i], metric, round(vals[i], 4), round(z, 3) if z is not None else None,
                 notable, engaged if engaged is None else int(engaged), blind, _now()))
        last = ds[-1]
        summary[metric] = {"date": last, "value": round(series[last], 3)}
    conn.commit()
    blind_rows = conn.execute(
        "SELECT date, metric, value, z FROM market_xray WHERE blind=1 "
        "AND date >= date((SELECT MAX(date) FROM market_xray), '-7 days') ORDER BY date DESC").fetchall()
    conn.close()
    summary["_blind_spots_7d"] = [
        {"date": r[0], "metric": r[1], "value": r[2], "z": r[3]} for r in blind_rows]
    return summary


def report(days: int) -> dict:
    conn = sqlite3.connect(DB, timeout=60.0)
    conn.row_factory = sqlite3.Row
    _ensure_table(conn)
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM market_xray WHERE date >= date((SELECT MAX(date) FROM market_xray), ?) "
        "ORDER BY blind DESC, ABS(COALESCE(z,0)) DESC", (f"-{days} days",))]
    conn.close()
    return {"window_days": days,
            "blind_spots": [r for r in rows if r["blind"]],
            "notable_engaged": [r for r in rows if r["notable"] and not r["blind"]]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("snapshot")
    r = sub.add_parser("report")
    r.add_argument("--days", type=int, default=7)
    a = ap.parse_args(argv)
    if a.cmd == "report":
        print(json.dumps(report(a.days), indent=2))
    else:
        print(json.dumps(snapshot(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
