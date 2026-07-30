#!/usr/bin/env python3
"""Internal paper-broker adapter — the only trading-state seam (D52).

Presents the legacy broker-shaped surface (get_account / list_positions /
list_orders / get_order / place_order / portfolio_history) backed only by the
internal simulator (sim_broker ledger, book='desk'). There is deliberately no
runtime switch to an external broker.

Market DATA (daily_bars, latest_trade, market_clock, spy_trend) is NOT this
module's job — data stays in connectors and gets its own multi-source story.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, "/home/aaron/.openclaw/workspaces/trading-intel/scripts")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from connectors import massive as _massive  # noqa: E402  (data feed for the sim backend)


class ConnectorError(RuntimeError):
    """Internal paper-broker boundary failure."""

DESK_BOOK = "desk"


def backend() -> str:
    """The execution backend is intentionally non-configurable."""
    return "sim"


def _conn():
    from _db import connect
    return connect()


def _sim():
    import sim_broker
    return sim_broker


# ---------------------------------------------------------------- account

def get_account() -> dict:
    sb = _sim()
    conn = _conn()
    cash = sb.get_cash(conn, DESK_BOOK)
    equity = cash
    positions = sb.positions(conn, DESK_BOOK)
    for sym, pos in positions.items():
        px = pos.get("current_price") or pos.get("cost_basis") or 0.0
        equity += pos["qty"] * px
    row = conn.execute(
        "SELECT equity FROM book_equity WHERE book=? ORDER BY date DESC LIMIT 1 OFFSET 1",
        (DESK_BOOK,)).fetchone()
    last_equity = float(row[0]) if row else equity
    return {
        "status": "ACTIVE",
        "account_number": "SIM-DESK",
        "equity": f"{equity:.2f}",
        "portfolio_value": f"{equity:.2f}",
        "last_equity": f"{last_equity:.2f}",
        "cash": f"{cash:.2f}",
        "buying_power": f"{cash:.2f}",
        "source": "sim",
    }


def list_positions() -> list[dict]:
    sb = _sim()
    conn = _conn()
    out = []
    for sym, pos in sb.positions(conn, DESK_BOOK).items():
        px = pos.get("current_price") or (pos.get("cost_basis") or 0.0)
        qty = pos["qty"]
        basis = pos.get("cost_basis") or 0.0
        mv = qty * px
        upl = (px - basis) * qty
        out.append({
            "symbol": sym,
            "qty": str(qty),
            "avg_entry_price": str(basis),
            "current_price": str(px),
            "market_value": str(mv),
            "cost_basis": str(basis * qty),
            "unrealized_pl": str(upl),
            "unrealized_plpc": str((upl / (basis * abs(qty))) if basis and qty else 0.0),
            "unrealized_intraday_pl": "0",
            "side": "long" if qty >= 0 else "short",
        })
    return out


def list_orders(status: str = "open", limit: int = 100) -> list[dict]:
    if status == "open":
        return []  # sim fills are immediate
    conn = _conn()
    rows = conn.execute(
        "SELECT order_id, symbol, side, qty, fill_price, filled_at FROM sim_orders "
        "WHERE book=? ORDER BY filled_at DESC LIMIT ?", (DESK_BOOK, limit)).fetchall()
    return [{
        "id": oid, "client_order_id": oid, "symbol": sym, "side": side,
        "qty": str(q), "type": "sim_fill", "status": "filled",
        "filled_avg_price": str(px), "submitted_at": ts, "filled_at": ts,
        "created_at": ts,
    } for oid, sym, side, q, px, ts in rows]


def get_order(order_id: str) -> dict:
    conn = _conn()
    r = conn.execute(
        "SELECT order_id, symbol, side, qty, fill_price, filled_at FROM sim_orders "
        "WHERE order_id=? AND book=?", (order_id, DESK_BOOK)).fetchone()
    if not r:
        raise ConnectorError(f"sim order not found: {order_id}")
    oid, sym, side, q, px, ts = r
    return {"id": oid, "symbol": sym, "side": side, "qty": str(q), "status": "filled",
            "filled_avg_price": str(px), "filled_at": ts, "submitted_at": ts}


def portfolio_history(period: str = "all", timeframe: str = "1D") -> list[dict]:
    conn = _conn()
    rows = conn.execute(
        "SELECT date, equity FROM book_equity WHERE book=? ORDER BY date", (DESK_BOOK,)).fetchall()
    return [{"date": d, "equity": float(e)} for d, e in rows]


# ---------------------------------------------------------------- orders

def place_order(symbol: str, qty: float, side: str, order_type: str = "market",
                limit_price: float | None = None, time_in_force: str = "day",
                client_order_id: str | None = None) -> dict:
    sb = _sim()
    conn = _conn()
    live = _massive.latest_trade(symbol)  # data feed (Massive), not broker state
    ref = float(live["price"]) if live and live.get("price") else None
    if ref is None and limit_price is not None:
        ref = float(limit_price)
    if ref is None or ref <= 0:
        raise ConnectorError(f"sim place_order: no reference price for {symbol}")
    fill = sb.fill_price(symbol, side, ref)
    # The current simulator supports immediate marketable-limit fills only. A
    # non-marketable limit must not be fabricated at the limit price.
    if limit_price is not None:
        lp = float(limit_price)
        marketable = fill <= lp if side == "buy" else fill >= lp
        if not marketable:
            raise ConnectorError(
                f"non-marketable internal-paper limit: {side} {symbol} simulated_fill={fill:.4f} limit={lp:.4f}"
            )
    oid = client_order_id or f"sim-{uuid.uuid4().hex[:16]}"
    sb.ensure_book(conn, DESK_BOOK, cash=0.0)
    sb.apply_fill(conn, DESK_BOOK, symbol, side, float(qty), float(fill),
                  order_id=oid, source="desk-execute")
    ts = conn.execute("SELECT filled_at FROM sim_orders WHERE order_id=?", (oid,)).fetchone()[0]
    return {"id": oid, "client_order_id": oid, "symbol": symbol, "side": side,
            "qty": str(qty), "type": "sim_fill", "status": "filled",
            "filled_avg_price": str(fill), "submitted_at": ts, "filled_at": ts}
