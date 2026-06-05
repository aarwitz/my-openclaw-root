"""Yahoo Finance connector — keyless backup for VIX, SPY, and treasury yields.

We use the public v8/finance/chart endpoint with a browser User-Agent. This is
intentionally a *backup* path: the primary data source for the regime classifier
remains FRED + Alpaca. Yahoo is used when those fail.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any

from ._http import ConnectorError, cache_read, cache_write, http_get, now_iso

YAHOO_CHART = "https://query2.finance.yahoo.com/v8/finance/chart/{symbol}?range={rng}&interval=1d"
BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/123.0"
CBOE_CDN = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{index}_History.csv"


def _cboe_last_close(index: str) -> float:
    """Fetch the latest daily close from Cboe's official CDN.

    Uses stdlib urllib — confirmed reachable from this host even when Yahoo
    Finance is fingerprint-throttled. CSV format: DATE,OPEN,HIGH,LOW,CLOSE.
    """
    url = CBOE_CDN.format(index=index)
    raw = http_get(url, headers={"User-Agent": BROWSER_UA}, timeout=10.0, retries=1)
    lines = [ln.strip() for ln in raw.decode("utf-8", errors="replace").splitlines() if ln.strip()]
    if len(lines) < 2:
        raise ConnectorError(f"cboe {index}: empty CSV")
    parts = lines[-1].split(",")
    if len(parts) < 5:
        raise ConnectorError(f"cboe {index}: malformed row {lines[-1]!r}")
    try:
        return float(parts[4])
    except ValueError as exc:
        raise ConnectorError(f"cboe {index}: non-numeric close {parts[4]!r}") from exc


def _fetch_chart(symbol: str, rng: str = "1y") -> dict[str, Any]:
    url = YAHOO_CHART.format(symbol=urllib.parse.quote(symbol, safe=""), rng=rng)
    # Yahoo aggressively fingerprints stdlib urllib (returns 429). Prefer curl
    # which goes through the same TLS path browsers use.
    curl = shutil.which("curl")
    if curl:
        result = subprocess.run(
            [curl, "--http1.1", "-sS", "-A", BROWSER_UA, "-H", "Accept: */*",
             "--max-time", "12", url],
            capture_output=True, timeout=15,
        )
        if result.returncode != 0:
            raise ConnectorError(f"yahoo curl failed for {symbol}: {result.stderr.decode(errors='replace')[:200]}")
        body = result.stdout
    else:
        body = http_get(url, headers={"User-Agent": BROWSER_UA, "Accept": "*/*"}, timeout=12.0, retries=2)
    try:
        data = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ConnectorError(f"yahoo chart non-json for {symbol}: {exc}") from exc
    err = (data.get("chart") or {}).get("error")
    if err:
        raise ConnectorError(f"yahoo chart error for {symbol}: {err}")
    res = (data.get("chart") or {}).get("result") or []
    if not res:
        raise ConnectorError(f"yahoo chart empty result for {symbol}")
    return res[0]


def closes(symbol: str, rng: str = "1y") -> list[float]:
    """Return daily closes (oldest first)."""
    cache_key = f"yahoo_{symbol.lower().replace('^','idx_')}_{rng}"
    cached = cache_read(cache_key, max_age_h=6.0)
    if cached:
        return cached.get("closes", [])
    res = _fetch_chart(symbol, rng=rng)
    closes_raw = (((res.get("indicators") or {}).get("quote") or [{}])[0]).get("close") or []
    cleaned = [c for c in closes_raw if c is not None]
    if not cleaned:
        raise ConnectorError(f"yahoo {symbol}: no closes")
    cache_write(cache_key, {"closes": cleaned, "symbol": symbol})
    # gentle pacing to avoid 429 when chaining multiple symbols
    time.sleep(0.4)
    return cleaned


def vix_term_structure() -> dict[str, Any]:
    """VIX + VIX3M term structure ratio.

    Primary: Cboe official CDN CSV (cdn.cboe.com) via stdlib urllib — confirmed
    reachable from this host even when Yahoo Finance throttles Python processes.
    Fallback: yfinance ^VIX / ^VIX3M (available on this host as of June 2026).
    """
    cache_key = "vix_term_cboe"
    cached = cache_read(cache_key, max_age_h=4.0)
    if cached:
        return {k: v for k, v in cached.items() if k != "cached_at"}

    cboe_err: Exception | None = None
    try:
        v_spot = _cboe_last_close("VIX")
        v3_spot = _cboe_last_close("VIX3M")
        if v3_spot <= 0:
            raise ConnectorError("VIX3M non-positive from Cboe")
        result = {
            "vix_spot": round(v_spot, 2),
            "vix3m": round(v3_spot, 2),
            "ratio": round(v_spot / v3_spot, 4),
            "retrieved_at": now_iso(),
            "source": "cboe:VIX_History,VIX3M_History",
        }
        cache_write(cache_key, result)
        return result
    except ConnectorError as exc:
        cboe_err = exc

    # Fallback: yfinance (bypasses Yahoo Finance TLS fingerprinting)
    try:
        import yfinance as yf  # noqa: PLC0415

        def _yf_last(sym: str) -> float:
            h = yf.Ticker(sym).history(period="5d")
            if h.empty:
                raise ConnectorError(f"yfinance {sym}: no data")
            return float(h["Close"].iloc[-1])

        v_spot = _yf_last("^VIX")
        v3_spot = _yf_last("^VIX3M")
        if v3_spot <= 0:
            raise ConnectorError("VIX3M non-positive from yfinance")
        result = {
            "vix_spot": round(v_spot, 2),
            "vix3m": round(v3_spot, 2),
            "ratio": round(v_spot / v3_spot, 4),
            "retrieved_at": now_iso(),
            "source": "yfinance:^VIX,^VIX3M",
        }
        cache_write(cache_key, result)
        return result
    except Exception as exc2:
        raise ConnectorError(
            f"vix_term_structure: cboe failed ({cboe_err}); yfinance also failed ({exc2})"
        ) from exc2


def yield_curve_proxy() -> dict[str, Any]:
    """Approximate T10Y2Y using ^TNX (10y) and ^FVX (5y).

    Primary: yfinance (confirmed working — bypasses Yahoo Finance TLS
    fingerprinting that throttles stdlib urllib and subprocess curl).
    Fallback: Yahoo Finance curl via closes().
    NOTE: Uses 10y minus 5y spread — APPROXIMATION for 10y-2y. Source tag
    marks this so the dashboard can surface the caveat.
    """
    yf_err: Exception | None = None
    try:
        import yfinance as yf  # noqa: PLC0415

        def _yf_series(sym: str) -> list[float]:
            h = yf.Ticker(sym).history(period="3mo")
            if h.empty:
                raise ConnectorError(f"yfinance {sym}: no data")
            return h["Close"].dropna().tolist()

        tnx = _yf_series("^TNX")
        fvx = _yf_series("^FVX")
        if not tnx or not fvx:
            raise ConnectorError("yield_curve_proxy: yfinance empty series")
        spread_bps = round((tnx[-1] - fvx[-1]) * 100.0, 2)
        slope_60d = None
        if len(tnx) >= 60 and len(fvx) >= 60:
            slope_60d = round(((tnx[-1] - fvx[-1]) - (tnx[-60] - fvx[-60])) * 100.0, 2)
        return {
            "level_bps": spread_bps,
            "slope_60d_bps": slope_60d,
            "retrieved_at": now_iso(),
            "source": "yfinance_proxy:^TNX-^FVX (10y-5y, NOT 10y-2y)",
            "is_proxy": True,
        }
    except Exception as exc:
        yf_err = exc

    # Final fallback: Yahoo Finance via shell curl
    tnx = closes("^TNX", rng="3mo")
    fvx = closes("^FVX", rng="3mo")
    if not tnx or not fvx:
        raise ConnectorError(f"yield_curve_proxy: yfinance failed ({yf_err}); yahoo also empty")
    spread_bps = round((tnx[-1] - fvx[-1]) * 100.0, 2)
    slope_60d = None
    if len(tnx) >= 60 and len(fvx) >= 60:
        slope_60d = round(((tnx[-1] - fvx[-1]) - (tnx[-60] - fvx[-60])) * 100.0, 2)
    return {
        "level_bps": spread_bps,
        "slope_60d_bps": slope_60d,
        "retrieved_at": now_iso(),
        "source": "yahoo_proxy:^TNX-^FVX (10y-5y, NOT 10y-2y)",
        "is_proxy": True,
    }
