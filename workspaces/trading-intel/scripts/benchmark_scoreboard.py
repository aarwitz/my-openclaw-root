#!/usr/bin/env python3
"""Deterministic benchmark scoreboard: portfolio vs SPY at every horizon.

The single source of truth for "are we beating the S&P". Reads daily account
equity from Alpaca portfolio history and SPY daily closes, aligns the two
series by trading date, and writes one `benchmarks` row per horizon:

  intraday        last trading day
  swing_1_5d      last 5 trading days
  position_1_4w   last 21 trading days
  trend_1_3m      last 63 trading days
  long_6m_plus    last 126 trading days
  all             since account inception

alpha_pct = portfolio_return_pct - spy_return_pct for the same window.
sharpe_estimate = annualised mean/std of daily (portfolio - SPY) excess
returns over the window (needs >= 5 points, else NULL).

--backfill additionally inserts one `portfolio_snapshots` row per historical
trading day that has no snapshot yet (source='alpaca_history_backfill') so the
app can chart the full equity curve. Idempotent: one row per trading date.

Usage:
  python3 benchmark_scoreboard.py [--backfill] [--dry-run] [--run-id ID]
"""

from __future__ import annotations

import argparse
import math
import sys
import uuid
from pathlib import Path

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
sys.path.insert(0, "/home/aaron/.openclaw/workspaces/developer/scripts")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _db import audit, connect, emit, now_iso  # noqa: E402
from connectors.alpaca import ConnectorError, daily_bars  # noqa: E402
sys.path.insert(0, "/home/aaron/.openclaw/workspaces/executor/scripts")
from broker import portfolio_history  # noqa: E402  (adapter, D52)

HORIZON_DAYS = [
    ("intraday", 1),
    ("swing_1_5d", 5),
    ("position_1_4w", 21),
    ("trend_1_3m", 63),
    ("long_6m_plus", 126),
    ("all", None),
]


def _aligned_series() -> list[dict]:
    """[{date, equity, spy_close}] for trading dates present in both series."""
    equity = portfolio_history(period="all", timeframe="1D")
    if not equity:
        raise ConnectorError("portfolio_history returned no points")
    spy = {b["t"][:10]: float(b["c"]) for b in daily_bars("SPY", days=400)}
    out = []
    for p in equity:
        close = spy.get(p["date"])
        if close:
            out.append({"date": p["date"], "equity": p["equity"], "spy_close": close})
    if len(out) < 2:
        raise ConnectorError(f"aligned series too short ({len(out)} points)")
    return out


def _ret_pct(first: float, last: float) -> float:
    return round((last / first - 1.0) * 100.0, 4)


def _sharpe(window: list[dict]) -> float | None:
    if len(window) < 6:
        return None
    daily_excess = []
    for prev, cur in zip(window, window[1:]):
        rp = cur["equity"] / prev["equity"] - 1.0
        rs = cur["spy_close"] / prev["spy_close"] - 1.0
        daily_excess.append(rp - rs)
    mean = sum(daily_excess) / len(daily_excess)
    var = sum((x - mean) ** 2 for x in daily_excess) / (len(daily_excess) - 1)
    std = math.sqrt(var)
    if std == 0:
        return None
    return round(mean / std * math.sqrt(252), 3)


def compute_rows(series: list[dict], run_id: str | None) -> list[dict]:
    captured_at = now_iso()
    rows = []
    for horizon, days in HORIZON_DAYS:
        window = series if days is None else series[-(days + 1):]
        if len(window) < 2:
            continue
        first, last = window[0], window[-1]
        port = _ret_pct(first["equity"], last["equity"])
        spy = _ret_pct(first["spy_close"], last["spy_close"])
        rows.append({
            "id": f"BMK-{uuid.uuid4().hex[:12]}",
            "captured_at": captured_at,
            "horizon": horizon,
            "period_start": first["date"],
            "period_end": last["date"],
            "portfolio_return_pct": port,
            "spy_return_pct": spy,
            "alpha_pct": round(port - spy, 4),
            "sharpe_estimate": _sharpe(window),
            "turnover_pct": None,
            "source_run_id": run_id,
        })
    return rows


def write_rows(conn, rows: list[dict]) -> None:
    for r in rows:
        conn.execute(
            "INSERT INTO benchmarks (id, captured_at, horizon, period_start, period_end, "
            "portfolio_return_pct, spy_return_pct, alpha_pct, sharpe_estimate, turnover_pct, source_run_id) "
            "VALUES (:id, :captured_at, :horizon, :period_start, :period_end, "
            ":portfolio_return_pct, :spy_return_pct, :alpha_pct, :sharpe_estimate, :turnover_pct, :source_run_id)",
            r,
        )
    inception = next((r for r in rows if r["horizon"] == "all"), None)
    audit(
        conn,
        actor="developer",
        entity_type="benchmark",
        entity_id=rows[0]["captured_at"],
        action="scoreboard_capture",
        rationale=(
            f"alpha_all={inception['alpha_pct']}% port={inception['portfolio_return_pct']}% "
            f"spy={inception['spy_return_pct']}%" if inception else f"{len(rows)} horizons"
        ),
    )


def backfill_snapshots(conn, series: list[dict]) -> int:
    """One portfolio_snapshots row per historical trading date missing one."""
    have = {
        r["captured_at"][:10]
        for r in conn.execute("SELECT captured_at FROM portfolio_snapshots")
    }
    inserted = 0
    for prev, cur in zip([None] + series[:-1], series):
        if cur["date"] in have:
            continue
        conn.execute(
            "INSERT INTO portfolio_snapshots (id, captured_at, equity, last_equity, day_pl, "
            "cash, buying_power, spy_close, spy_as_of, account_status, source) "
            "VALUES (?, ?, ?, ?, ?, NULL, NULL, ?, ?, NULL, 'alpaca_history_backfill')",
            (
                f"PSNAP-{uuid.uuid4().hex[:12]}",
                f"{cur['date']}T21:00:00Z",  # market close (approx, UTC)
                cur["equity"],
                prev["equity"] if prev else None,
                round(cur["equity"] - prev["equity"], 2) if prev else None,
                cur["spy_close"],
                f"{cur['date']}T21:00:00Z",
            ),
        )
        inserted += 1
    if inserted:
        audit(
            conn,
            actor="developer",
            entity_type="portfolio_snapshot",
            entity_id=f"backfill-{series[-1]['date']}",
            action="history_backfill",
            rationale=f"{inserted} daily rows from alpaca portfolio history",
        )
    return inserted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args(argv)

    try:
        series = _aligned_series()
    except ConnectorError as exc:
        emit({"ok": False, "error": str(exc)})
        return 1

    rows = compute_rows(series, args.run_id)
    summary = {
        "ok": True,
        "points": len(series),
        "inception": series[0]["date"],
        "latest": series[-1]["date"],
        "horizons": {
            r["horizon"]: {
                "portfolio_pct": r["portfolio_return_pct"],
                "spy_pct": r["spy_return_pct"],
                "alpha_pct": r["alpha_pct"],
                "sharpe": r["sharpe_estimate"],
            }
            for r in rows
        },
    }
    if args.dry_run:
        emit({**summary, "dry_run": True})
        return 0

    conn = connect()
    try:
        write_rows(conn, rows)
        if args.backfill:
            summary["backfilled_snapshots"] = backfill_snapshots(conn, series)
        conn.commit()
    finally:
        conn.close()
    emit(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
