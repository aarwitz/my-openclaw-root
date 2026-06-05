"""FRED connector — keyless via fredgraph.csv endpoint.

We pull recent observations as CSV, parse the most recent non-missing value,
and report basis-point levels for credit spreads (BAMLH0A0HYM2) and yield curve
spreads (T10Y2Y). FRED returns BAMLH0A0HYM2 in percent (e.g. 2.71 = 271 bps)
and T10Y2Y in percent (e.g. 0.55 = 55 bps).
"""

from __future__ import annotations

import csv
import io
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

from ._http import ConnectorError, cache_read, cache_write, http_get, now_iso

FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
# Treasury.gov daily yield curve XML (no API key; confirmed reachable via Python
# urllib on this host even when fred.stlouisfed.org is TCP-blocked)
TREASURY_YIELD_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/"
    "interest-rates/pages/xml?data=daily_treasury_yield_curve"
    "&field_tdr_date_value_month={ym}"
)
CBOE_CDN = "https://cdn.cboe.com/api/global/us_indices/daily_prices/{index}_History.csv"
BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) Gecko/20100101 Firefox/123.0"


def _treasury_gov_t10y2y() -> dict[str, object]:
    """Fetch the latest 10y-2y spread from Treasury.gov official XML feed.

    Parses BC_2YEAR and BC_10YEAR from the most recent entry. Returns
    {as_of, level_bps, slope_60d_bps, two_year_pct, ten_year_pct}.
    slope_60d_bps is None because the XML only covers one month at a time;
    callers that need slope can fetch a prior month separately or omit it.
    """
    ym = datetime.now(timezone.utc).strftime("%Y%m")
    url = TREASURY_YIELD_URL.format(ym=ym)
    raw = http_get(url, headers={"User-Agent": BROWSER_UA}, timeout=10.0, retries=1)
    root = ET.fromstring(raw.decode("utf-8", errors="replace"))
    ns = {
        "a": "http://www.w3.org/2005/Atom",
        "d": "http://schemas.microsoft.com/ado/2007/08/dataservices",
        "m": "http://schemas.microsoft.com/ado/2007/08/dataservices/metadata",
    }
    entries = root.findall("a:entry", ns)
    if not entries:
        raise ConnectorError("treasury.gov: no entries in XML response")
    props = entries[-1].find(".//m:properties", ns)
    if props is None:
        raise ConnectorError("treasury.gov: no properties element in last entry")

    def _get(tag: str) -> float | None:
        el = props.find(f"d:{tag}", ns)
        if el is None or not (el.text or "").strip() or el.text.strip() in {"NA", "."}:
            return None
        try:
            return float(el.text.strip())
        except ValueError:
            return None

    two_y = _get("BC_2YEAR")
    ten_y = _get("BC_10YEAR")
    date_el = props.find("d:NEW_DATE", ns)
    as_of = (date_el.text or "")[:10] if date_el is not None else "unknown"
    if two_y is None or ten_y is None:
        raise ConnectorError(f"treasury.gov: missing 2Y or 10Y data for {as_of}")
    level_bps = round((ten_y - two_y) * 100.0, 2)
    return {"as_of": as_of, "level_bps": level_bps, "two_year_pct": two_y, "ten_year_pct": ten_y}


def _cboe_last_close(index: str) -> float:
    """Fetch the latest daily close from Cboe CDN CSV (no auth, urllib works)."""
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


def fetch_series(series_id: str, use_cache: bool = True) -> list[tuple[str, float]]:
    """Return list of (date, value) tuples, oldest first."""
    cache_key = f"fred_{series_id.lower()}"
    if use_cache:
        cached = cache_read(cache_key, max_age_h=6.0)
        if cached:
            return [(d, v) for d, v in cached.get("series", [])]
    raw = http_get(FRED_URL.format(series=series_id), timeout=6.0, retries=0)
    text = raw.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if len(rows) < 2:
        raise ConnectorError(f"fred:{series_id} returned empty CSV")
    out: list[tuple[str, float]] = []
    for row in rows[1:]:
        if len(row) < 2:
            continue
        date, raw_val = row[0].strip(), row[1].strip()
        if not raw_val or raw_val in {".", "NA"}:
            continue
        try:
            out.append((date, float(raw_val)))
        except ValueError:
            continue
    if not out:
        raise ConnectorError(f"fred:{series_id} returned no numeric observations")
    cache_write(cache_key, {"series": out, "series_id": series_id})
    return out


def credit_spreads() -> dict[str, Any]:
    """ICE BofA US High Yield OAS (BAMLH0A0HYM2) — returned in basis points."""
    series = fetch_series("BAMLH0A0HYM2")
    latest_date, latest_pct = series[-1]
    level_bps = round(latest_pct * 100.0, 2)
    delta_20d_bps: float | None = None
    if len(series) >= 21:
        prior_pct = series[-21][1]
        delta_20d_bps = round((latest_pct - prior_pct) * 100.0, 2)
    return {
        "level_bps": level_bps,
        "delta_20d_bps": delta_20d_bps,
        "as_of": latest_date,
        "retrieved_at": now_iso(),
        "source": "fred:BAMLH0A0HYM2",
    }


def yield_curve() -> dict[str, Any]:
    """10y-2y Treasury spread — returned in basis points.

    Primary: Treasury.gov official XML feed (confirmed reachable via Python
    urllib even when fred.stlouisfed.org is TCP-blocked on this host).
    Fallback: FRED T10Y2Y CSV via fetch_series().
    """
    treasury_err: Exception | None = None
    try:
        d = _treasury_gov_t10y2y()
        result = {
            "level_bps": d["level_bps"],
            "slope_60d_bps": None,  # single-month feed; slope not available here
            "as_of": d["as_of"],
            "retrieved_at": now_iso(),
            "source": "treasury.gov:BC_10YEAR-BC_2YEAR",
        }
        cache_write("treasury_yield_curve", result)
        return result
    except ConnectorError as exc:
        treasury_err = exc

    # Fallback: FRED T10Y2Y (may be TCP-blocked on some hosts)
    try:
        series = fetch_series("T10Y2Y")
        latest_date, latest_pct = series[-1]
        level_bps = round(latest_pct * 100.0, 2)
        slope_60d: float | None = None
        if len(series) >= 61:
            slope_60d = round((latest_pct - series[-61][1]) * 100.0, 2)
        return {
            "level_bps": level_bps,
            "slope_60d_bps": slope_60d,
            "as_of": latest_date,
            "retrieved_at": now_iso(),
            "source": "fred:T10Y2Y",
        }
    except ConnectorError as exc2:
        raise ConnectorError(
            f"yield_curve: treasury.gov failed ({treasury_err}); FRED also failed ({exc2})"
        ) from exc2


def vix_levels() -> dict[str, Any]:
    """VIX and VIX3M levels and term structure ratio.

    Primary: Cboe official CDN CSV (confirmed reachable via Python urllib).
    Fallback: FRED VIXCLS + VXVCLS (may be TCP-blocked on some hosts).
    """
    cboe_err: Exception | None = None
    try:
        v_val = _cboe_last_close("VIX")
        v3_val = _cboe_last_close("VIX3M")
        if v3_val <= 0:
            raise ConnectorError("VIX3M non-positive from Cboe")
        return {
            "vix_spot": round(v_val, 2),
            "vix3m": round(v3_val, 2),
            "ratio": round(v_val / v3_val, 4),
            "retrieved_at": now_iso(),
            "source": "cboe:VIX_History,VIX3M_History",
        }
    except ConnectorError as exc:
        cboe_err = exc

    # Fallback: FRED VIXCLS + VXVCLS
    try:
        v = fetch_series("VIXCLS")
        v3 = fetch_series("VXVCLS")
        v_date, v_val = v[-1]
        v3_date, v3_val = v3[-1]
        if v3_val <= 0:
            raise ConnectorError("VIX3M non-positive from FRED")
        return {
            "vix_spot": round(v_val, 2),
            "vix3m": round(v3_val, 2),
            "ratio": round(v_val / v3_val, 4),
            "as_of_vix": v_date,
            "as_of_vix3m": v3_date,
            "retrieved_at": now_iso(),
            "source": "fred:VIXCLS,VXVCLS",
        }
    except ConnectorError as exc2:
        raise ConnectorError(
            f"vix_levels: cboe failed ({cboe_err}); FRED also failed ({exc2})"
        ) from exc2
