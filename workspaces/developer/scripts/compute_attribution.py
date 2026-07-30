#!/usr/bin/env python3
"""Developer · compute_attribution.py

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
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from developer_db import audit, connect, emit, now_iso  # noqa: E402

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
        # Several early broker timestamps contain nanoseconds, while Python's
        # datetime parser accepts microseconds. Truncate only the fractional
        # component; preserve the timezone suffix.
        normalized = re.sub(r"(\.\d{6})\d+(?=Z|[+-]\d\d:\d\d|$)", r"\1", d)
        dt = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def _spy_return(
    open_dt: datetime,
    close_dt: datetime,
    bars: list[dict] | None = None,
) -> float | None:
    if bars is None:
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


def _realized_return_from_fills(conn, position) -> tuple[float | None, str]:
    """Reconstruct a closed position's realized return from its actual fills.

    ``positions.unrealized_pnl_pct`` is a mark-time field. Early close paths
    sometimes left it at zero or at the entry mark, so using it for realized
    attribution silently rewrote losses as flat trades.
    """
    open_dt = _parse(position["opened_at"])
    close_dt = _parse(position["closed_at"])
    if not (open_dt and close_dt):
        return None, "unparseable_position_window"
    rows = conn.execute(
        """
        SELECT o.side, o.qty, o.avg_fill_price, o.filled_at
        FROM orders o
        JOIN trade_intents ti ON ti.id=o.trade_intent_id
        WHERE ti.hypothesis_id=?
          AND UPPER(o.symbol)=UPPER(?)
          AND o.status='filled'
          AND o.avg_fill_price IS NOT NULL
        ORDER BY o.filled_at, o.broker_order_id
        """,
        (position["hypothesis_id"], position["ticker"]),
    ).fetchall()
    # Reconciliation can lag an immediate simulated fill by a few minutes.
    grace_s = 10 * 60
    fills = []
    for row in rows:
        filled_at = _parse(row["filled_at"])
        if filled_at is None:
            continue
        if (filled_at - open_dt).total_seconds() < -grace_s:
            continue
        if (filled_at - close_dt).total_seconds() > grace_s:
            continue
        fills.append(row)

    buy_qty = buy_cost = sell_qty = sell_proceeds = 0.0
    for row in fills:
        qty = float(row["qty"] or 0)
        price = float(row["avg_fill_price"] or 0)
        if qty <= 0 or price <= 0:
            continue
        if str(row["side"]).lower() == "buy":
            buy_qty += qty
            buy_cost += qty * price
        elif str(row["side"]).lower() == "sell":
            sell_qty += qty
            sell_proceeds += qty * price
    if buy_cost > 0 and sell_qty + 1e-6 >= buy_qty > 0:
        return round((sell_proceeds / buy_cost - 1.0) * 100.0, 6), "filled_orders"

    # Compatibility fallback for rows whose historical broker order was never
    # captured. Require a real exit mark distinct from basis; a zero stale
    # unrealized field is not admissible realized evidence.
    basis = position["cost_basis"]
    exit_price = position["current_price"]
    if basis not in (None, 0) and exit_price is not None and float(exit_price) != float(basis):
        return round((float(exit_price) / float(basis) - 1.0) * 100.0, 6), "exit_mark_fallback"
    return None, "missing_terminal_fills"


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
        "pnl_slippage_adjusted, unrealized_pnl_pct, cost_basis, current_price, current_value "
        "FROM positions WHERE state='closed' AND closed_at IS NOT NULL"
    ).fetchall()
    # Only skip positions already attributed WITH a realized edge; rows left NULL by the
    # pre-mark_positions era get recomputed now that positions carry realized returns.
    existing = {r["position_id"] for r in conn.execute(
        "SELECT position_id FROM attribution "
        "WHERE position_id IS NOT NULL AND realized_edge_vs_spy_bps IS NOT NULL"
    )}
    try:
        spy_bars = daily_bars("SPY", days=400)
    except ConnectorError:
        spy_bars = []
    out = []
    for r in rows:
        if r["id"] in existing:
            continue
        open_dt = _parse(r["opened_at"])
        close_dt = _parse(r["closed_at"])
        if not (open_dt and close_dt):
            continue
        horizon = _horizon_for(open_dt, close_dt)
        port_ret, return_source = _realized_return_from_fills(conn, r)
        spy_ret = _spy_return(open_dt, close_dt, spy_bars)
        edge_bps = None
        if port_ret is not None and spy_ret is not None:
            edge_bps = round((float(port_ret) - float(spy_ret)) * 100.0, 1)  # pct → bps
        out.append({
            "position_id": r["id"], "hypothesis_id": r["hypothesis_id"],
            "ticker": r["ticker"], "horizon": horizon,
            "opened_at": r["opened_at"], "closed_at": r["closed_at"],
            "portfolio_return_pct": port_ret, "spy_return_pct": spy_ret,
            "realized_edge_vs_spy_bps": edge_bps,
            "return_source": return_source,
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
