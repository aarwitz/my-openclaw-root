"""Alpaca paper adapter — live quote/orders/positions confirmation.

Authority: paper account state, near-open live quote, intraday spread.
Not used for: catalysts (Finnhub), historical structure (Massive),
real-book exposure (Schwab).

PAPER ONLY. Endpoint must contain `paper-api`.
"""
from __future__ import annotations

from typing import Any, Optional
import requests

from ..credentials import alpaca
from ..http_util import write_cache, http_get_json


def _hdrs() -> dict[str, str]:
    _, key, sec = alpaca()
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": sec}


def _ep() -> str:
    e, _, _ = alpaca()
    if "paper-api" not in e:
        raise RuntimeError(f"refusing non-paper Alpaca endpoint: {e}")
    return e.rstrip("/")


# Trading API (account / positions / orders) — uses ALPACA endpoint
def account() -> dict:
    out = http_get_json(f"{_ep()}/account", headers=_hdrs())
    write_cache("alpaca", "_account", "account", out)
    return out


def positions() -> list[dict]:
    out = http_get_json(f"{_ep()}/positions", headers=_hdrs())
    write_cache("alpaca", "_account", "positions", out)
    return out if isinstance(out, list) else []


def open_orders(status: str = "open") -> list[dict]:
    out = http_get_json(f"{_ep()}/orders", headers=_hdrs(), params={"status": status, "limit": 100})
    write_cache("alpaca", "_account", f"orders_{status}", out)
    return out if isinstance(out, list) else []


def clock() -> dict:
    out = http_get_json(f"{_ep()}/clock", headers=_hdrs())
    write_cache("alpaca", "_account", "clock", out)
    return out


# Market data API — uses data.alpaca.markets
DATA_BASE = "https://data.alpaca.markets/v2"


def latest_quote(ticker: str) -> Optional[dict]:
    try:
        out = http_get_json(f"{DATA_BASE}/stocks/{ticker}/quotes/latest", headers=_hdrs())
    except Exception:
        return None
    write_cache("alpaca", ticker, "latest_quote", out)
    return out.get("quote")


def submit_order(symbol: str, qty: float, side: str, type_: str = "market",
                 time_in_force: str = "day", limit_price: Optional[float] = None,
                 client_order_id: Optional[str] = None) -> dict:
    """Place a paper order. Caller is responsible for policy gating."""
    body: dict[str, Any] = {
        "symbol": symbol, "qty": str(qty), "side": side,
        "type": type_, "time_in_force": time_in_force,
    }
    if limit_price is not None:
        body["limit_price"] = str(limit_price)
    if client_order_id:
        body["client_order_id"] = client_order_id
    r = requests.post(f"{_ep()}/orders", headers=_hdrs(), json=body, timeout=20)
    r.raise_for_status()
    out = r.json()
    write_cache("alpaca", symbol, f"order_{out.get('id', 'submit')}", out)
    return out
