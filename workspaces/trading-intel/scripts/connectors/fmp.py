#!/usr/bin/env python3
"""Financial Modeling Prep connector — `/stable` API, stdlib only, cached.

Credentials: ~/.openclaw/credentials/financial-modeling-prep-api.json (key 'api key').
Premium tier (verified 2026-06-18): 30yr history, full quarterly fundamentals + ratios,
historical S&P constituents (back to 1957), analyst estimates, earnings surprise, delisted
companies, EOD prices incl. delisted names. The v3 `/api/v3` endpoints are dead — use /stable.

Point-in-time discipline lives with the CALLER: fundamentals carry `filingDate`/`acceptedDate`
— stamp features with that, never the fiscal-period `date`, or you leak the future.
"""

from __future__ import annotations

import json
import os
import urllib.parse
from pathlib import Path
from typing import Any

from ._http import ConnectorError, cache_read, cache_write, http_get

CRED_PATH = Path(os.path.expanduser("~/.openclaw/credentials/financial-modeling-prep-api.json"))
BASE = "https://financialmodelingprep.com/stable"


def _key() -> str:
    if not CRED_PATH.exists():
        raise ConnectorError(f"FMP credentials missing at {CRED_PATH}")
    d = json.loads(CRED_PATH.read_text())
    k = d.get("api key") or d.get("apikey") or d.get("api_key")
    if not k:
        raise ConnectorError("FMP credentials missing 'api key'")
    return k


def _get(path: str, params: dict | None = None, cache_h: float = 168.0) -> Any:
    """GET /stable/<path>?... with on-disk caching. Historical data is stable → long TTL."""
    params = dict(params or {})
    ck = ("fmp_" + path.replace("/", "_") + "_"
          + "_".join(f"{k}-{v}" for k, v in sorted(params.items()))).replace(" ", "")[:180]
    hit = cache_read(ck, max_age_h=cache_h)
    if hit is not None and "data" in hit:
        return hit["data"]
    params["apikey"] = _key()
    url = f"{BASE}/{path}?" + urllib.parse.urlencode(params)
    raw = http_get(url, timeout=20.0, retries=2)
    try:
        data = json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ConnectorError(f"FMP non-json from {path}: {exc}") from exc
    if isinstance(data, dict) and ("Error Message" in data or "Error" in data):
        raise ConnectorError(f"FMP error {path}: {str(data)[:140]}")
    cache_write(ck, {"data": data})
    return data


# --- fundamentals / valuation inputs (each row carries filingDate = knowable_at) ---
def income_statement(symbol: str, period: str = "quarter", limit: int = 80) -> list[dict]:
    return _get("income-statement", {"symbol": symbol, "period": period, "limit": limit})


def ratios(symbol: str, period: str = "quarter", limit: int = 80) -> list[dict]:
    return _get("ratios", {"symbol": symbol, "period": period, "limit": limit})


def ratios_ttm(symbol: str) -> list[dict]:
    return _get("ratios-ttm", {"symbol": symbol}, cache_h=24.0)


def key_metrics_ttm(symbol: str) -> list[dict]:
    return _get("key-metrics-ttm", {"symbol": symbol}, cache_h=24.0)


def analyst_estimates(symbol: str, period: str = "annual", limit: int = 20) -> list[dict]:
    return _get("analyst-estimates", {"symbol": symbol, "period": period, "limit": limit}, cache_h=24.0)


def earnings(symbol: str, limit: int = 120) -> list[dict]:
    """Earnings calendar incl. epsActual vs epsEstimated (surprise). Future rows have null actuals."""
    return _get("earnings", {"symbol": symbol, "limit": limit}, cache_h=24.0)


def profile(symbol: str) -> list[dict]:
    return _get("profile", {"symbol": symbol}, cache_h=24.0)


def insider_trading(symbol: str, limit: int = 200) -> list[dict]:
    """Form-4 insider transactions (filingDate, transactionDate, transactionType, owner). The
    Thiel-sold-NVDA signal. Point-in-time = stamp features at filingDate (when it became public)."""
    # 24h TTL: the live desk reads this daily; the 168h default left features a week stale.
    return _get("insider-trading/search", {"symbol": symbol, "limit": limit}, cache_h=24.0)


def upgrades_downgrades(symbol: str, limit: int = 400) -> list[dict]:
    """Dated analyst rating actions (gradingCompany, previousGrade, newGrade, action) — revision breadth."""
    return _get("grades", {"symbol": symbol, "limit": limit}, cache_h=24.0)


def grades_historical(symbol: str, limit: int = 1000) -> list[dict]:
    """Dated analyst rating DISTRIBUTION (date + strongBuy/buy/hold/sell/strongSell counts) — a clean
    point-in-time consensus series. Each row's `date` is knowable_at."""
    return _get("grades-historical", {"symbol": symbol, "limit": limit}, cache_h=24.0)


# --- prices (works for delisted names too) ---
def historical_price(symbol: str, frm: str | None = None, to: str | None = None) -> list[dict]:
    p: dict[str, Any] = {"symbol": symbol}
    if frm:
        p["from"] = frm
    if to:
        p["to"] = to
    d = _get("historical-price-eod/full", p)
    return d if isinstance(d, list) else (d.get("historical", []) if isinstance(d, dict) else [])


# --- universe / survivorship ---
def sp500_current() -> list[dict]:
    return _get("sp500-constituent")


def sp500_historical_changes() -> list[dict]:
    """Index add/remove events (back to 1957). Reconstruct membership-as-of by replaying these
    backward from the current list — that is the survivorship-bias-free universe."""
    return _get("historical-sp500-constituent")


def delisted_companies(page: int = 0) -> list[dict]:
    return _get("delisted-companies", {"page": page})


def screener(market_cap_min: float = 1e9, exchanges: str = "NASDAQ,NYSE",
             price_min: float = 3.0, volume_min: int = 50000, limit: int = 5000) -> list[dict]:
    """Broad active-equity discovery (all-cap, not just S&P). Fields incl. symbol, marketCap,
    sector, industry, isEtf, isFund. Caller filters out ETFs/funds."""
    return _get("company-screener", {
        "marketCapMoreThan": int(market_cap_min), "exchange": exchanges,
        "priceMoreThan": price_min, "volumeMoreThan": volume_min,
        "isActivelyTrading": "true", "limit": limit}, cache_h=168.0)
