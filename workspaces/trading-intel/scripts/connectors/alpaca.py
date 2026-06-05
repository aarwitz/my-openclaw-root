"""Alpaca paper-trading + market-data connector.

Stdlib-only HTTP client. Credentials loaded from
~/.openclaw/credentials/alpaca-api.json (keys: 'api key', 'secret', 'endpoint',
'paper account').

This module is used by:
  - classify_regime.read_spy_trend (1D bars for SMA50/SMA200)
  - executor/execute_intent (place_order, get_account)
  - executor/reconcile (list_positions, list_orders)
  - snapshot_builder (broker block)
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from ._http import ConnectorError, cache_read, cache_write, http_get, now_iso

CRED_PATH = Path(os.path.expanduser("~/.openclaw/credentials/alpaca-api.json"))
PAPER_API = "https://paper-api.alpaca.markets"
DATA_API = "https://data.alpaca.markets"


def _creds() -> dict[str, str]:
    if not CRED_PATH.exists():
        raise ConnectorError(f"alpaca credentials missing at {CRED_PATH}")
    d = json.loads(CRED_PATH.read_text())
    key = d.get("api key") or d.get("api_key") or d.get("APCA_API_KEY_ID")
    secret = d.get("secret") or d.get("APCA_API_SECRET_KEY")
    if not key or not secret:
        raise ConnectorError("alpaca credentials missing key/secret")
    return {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}


def _get(base: str, path: str, params: dict[str, Any] | None = None) -> Any:
    qs = ("?" + urlencode(params)) if params else ""
    url = f"{base}{path}{qs}"
    raw = http_get(url, headers=_creds(), timeout=15.0)
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ConnectorError(f"alpaca non-json response from {url}: {exc}") from exc


# ----- account / positions / orders -----

def get_account() -> dict[str, Any]:
    return _get(PAPER_API, "/v2/account")


def list_positions() -> list[dict[str, Any]]:
    return _get(PAPER_API, "/v2/positions") or []


def list_orders(status: str = "open", limit: int = 100) -> list[dict[str, Any]]:
    return _get(PAPER_API, "/v2/orders", {"status": status, "limit": limit}) or []


# ----- market data -----

def daily_bars(symbol: str, days: int = 260) -> list[dict[str, Any]]:
    """Return up to `days` daily bars for `symbol`, oldest first."""
    cache_key = f"alpaca_{symbol.lower()}_1d_{days}"
    cached = cache_read(cache_key, max_age_h=6.0)
    if cached:
        return cached.get("bars", [])
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days * 2 + 10)  # extra buffer for weekends/holidays
    params = {
        "timeframe": "1Day",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "limit": 10000,
        "adjustment": "raw",
        "feed": "iex",
    }
    raw = _get(DATA_API, f"/v2/stocks/{symbol}/bars", params)
    bars = raw.get("bars") or []
    if not bars:
        raise ConnectorError(f"alpaca daily_bars: no bars for {symbol}")
    cache_write(cache_key, {"bars": bars, "symbol": symbol})
    return bars


def spy_trend() -> dict[str, Any]:
    bars = daily_bars("SPY", days=260)
    closes = [b["c"] for b in bars]
    if len(closes) < 200:
        raise ConnectorError(f"spy_trend: only {len(closes)} bars, need >=200")
    sma50 = sum(closes[-50:]) / 50.0
    sma200 = sum(closes[-200:]) / 200.0
    last = closes[-1]
    close_vs_sma200_pct = round((last / sma200 - 1.0) * 100.0, 3)
    return {
        "close": round(last, 2),
        "sma50": round(sma50, 2),
        "sma200": round(sma200, 2),
        "close_vs_sma200_pct": close_vs_sma200_pct,
        "sma50_gt_sma200": sma50 > sma200,
        "as_of": bars[-1].get("t"),
        "retrieved_at": now_iso(),
        "source": "alpaca_market_data:SPY:1Day",
    }


# ----- order placement -----

def place_order(
    symbol: str,
    qty: float,
    side: str,
    order_type: str = "market",
    limit_price: float | None = None,
    time_in_force: str = "day",
    client_order_id: str | None = None,
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "symbol": symbol,
        "qty": str(qty),
        "side": side,
        "type": order_type,
        "time_in_force": time_in_force,
    }
    if limit_price is not None:
        body["limit_price"] = str(limit_price)
    if client_order_id:
        body["client_order_id"] = client_order_id
    import urllib.request

    data = json.dumps(body).encode("utf-8")
    headers = {**_creds(), "Content-Type": "application/json"}
    req = urllib.request.Request(
        f"{PAPER_API}/v2/orders", data=data, headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15.0) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 — surfaced as ConnectorError
        raise ConnectorError(f"alpaca place_order failed: {exc}") from exc
