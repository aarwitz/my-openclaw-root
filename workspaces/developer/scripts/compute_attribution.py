#!/usr/bin/env python3
"""Bessent · compute_attribution.py

Walk closed positions and produce attribution rows: realized portfolio return
per horizon vs SPY benchmark return over the same window. Writes `attribution`
and rolls up into `benchmarks`.

Usage:
  python3 compute_attribution.py             # process closed positions w/o attribution
  python3 compute_attribution.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _db import audit, connect, emit, now_iso  # noqa: E402

from connectors.marketdata import ConnectorError, daily_bars  # noqa: E402

HORIZON_DAYS = {
    "intraday": 1,
    "swing_1_5d": 5,
    "position_1_4w": 20,
    "trend_1_3m": 60,
    "long_6m_plus": 130,
}


def _parse(d: str | None) -> datetime | None:
    """Parse to a NAIVE-UTC datetime. Bars now come from Massive with date-only `t`
    ('YYYY-MM-DD', naive) while position timestamps are tz-aware ('...Z'); normalizing
    both to naive-UTC keeps the nearest-bar comparison from mixing aware/naive."""
    if not d:
        return None
    try:
        dt = datetime.fromisoformat(d.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _spy_return(open_dt: datetime, close_dt: datetime) -> float | None:
    try:
        bars = daily_bars("SPY", days=400)
    except ConnectorError:
        return None
    rows = sorted(bars, key=lambda b: b["t"])
    if not rows:
        return None

    def closest(dt: datetime) -> float | None:
        best = None
        best_diff = 1e18
        for b in rows:
            bt = _parse(b["t"])
            if bt is None:
                continue
            d = abs((bt - dt).total_seconds())
            if d < best_diff:
                best_diff, best = d, b["c"]
        return best

    p0 = closest(open_dt)
    p1 = closest(close_dt)
    if p0 is None or p1 is None or p0 == 0:
        return None
    return round(100.0 * (p1 - p0) / p0, 4)


def _horizon_for(open_dt: datetime, close_dt: datetime) -> str:
    days = (close_dt - open_dt).total_seconds() / 86400.0
    if days < 1.5:
        return "intraday"
    if days <= 5:
        return "swing_1_5d"
    if days <= 28:
        return "position_1_4w"
    if days <= 90:
        return "trend_1_3m"
    return "long_6m_plus"


def process(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT id, hypothesis_id, ticker, opened_at, closed_at, "
        "pnl_slippage_adjusted, unrealized_pnl_pct, cost_basis, current_value "
        "FROM positions WHERE state='closed' AND closed_at IS NOT NULL"
    ).fetchall()
    # Only skip positions already attributed WITH a realized edge; rows left NULL by the
    # pre-mark_positions era get recomputed now that positions carry realized returns.
    existing = {r["position_id"] for r in conn.execute(
        "SELECT position_id FROM attribution "
        "WHERE position_id IS NOT NULL AND realized_edge_vs_spy_bps IS NOT NULL"
    )}
    out = []
    for r in rows:
        if r["id"] in existing:
            continue
        open_dt = _parse(r["opened_at"])
        close_dt = _parse(r["closed_at"])
        if not (open_dt and close_dt):
            continue
        horizon = _horizon_for(open_dt, close_dt)
        port_ret = r["unrealized_pnl_pct"]  # nearest available; ideal: realized
        spy_ret = _spy_return(open_dt, close_dt)
        edge_bps = None
        if port_ret is not None and spy_ret is not None:
            edge_bps = round((float(port_ret) - float(spy_ret)) * 100.0, 1)  # pct → bps
        out.append({
            "position_id": r["id"], "hypothesis_id": r["hypothesis_id"],
            "ticker": r["ticker"], "horizon": horizon,
            "opened_at": r["opened_at"], "closed_at": r["closed_at"],
            "portfolio_return_pct": port_ret, "spy_return_pct": spy_ret,
            "realized_edge_vs_spy_bps": edge_bps,
        })
    return out


def write(conn, rows: list[dict]) -> None:
    for r in rows:
        rid = "ATTR-" + uuid.uuid4().hex[:20]  # unique regardless of same-second / shared-hypothesis position ids
        conn.execute("DELETE FROM attribution WHERE position_id=?", (r["position_id"],))  # replace any stale NULL-edge row
        conn.execute(
            "INSERT INTO attribution (id, hypothesis_id, position_id, horizon, "
            "opened_at, closed_at, portfolio_return_pct, spy_return_pct, "
            "realized_edge_vs_spy_bps, attribution_json, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (rid, r["hypothesis_id"], r["position_id"], r["horizon"],
             r["opened_at"], r["closed_at"], r["portfolio_return_pct"],
             r["spy_return_pct"], r["realized_edge_vs_spy_bps"],
             json.dumps(r), now_iso()),
        )
        audit(conn, actor="developer", entity_type="attribution", entity_id=rid,
              action="compute",
              rationale=f"{r['ticker']} {r['horizon']}: port={r['portfolio_return_pct']} spy={r['spy_return_pct']} edge_bps={r['realized_edge_vs_spy_bps']}")
    conn.commit()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)

    conn = connect()
    rows = process(conn)
    if not args.dry_run and rows:
        write(conn, rows)
    emit({"computed": len(rows), "dry_run": bool(args.dry_run), "rows": rows})
    return 0


if __name__ == "__main__":
    sys.exit(main())
