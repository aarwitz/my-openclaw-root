#!/usr/bin/env python3
"""SEC EDGAR fundamentals connector.

Free, authoritative US-GAAP financial statements with no API key, via the SEC
XBRL `companyfacts` API. stdlib only (matches the other connectors) so cron jobs
carry no third-party dependency.

Two endpoints:
  * ticker -> CIK map:  https://www.sec.gov/files/company_tickers.json
  * facts:              https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json

SEC requires a descriptive User-Agent with a contact; requests without one are
throttled/blocked. We keep well under the 10 req/s fair-access limit (everything
is cached: the ticker map for a week, company facts for a day).

The public surface is `fundamentals(ticker)` which returns a normalized dict of
the values a valuation model needs (TTM where derivable, else latest annual),
plus short annual histories for growth/comps. Raises ConnectorError for symbols
with no SEC filer (ETFs, ADRs without us-gaap tags, etc.) so callers can mark a
ticker "not applicable" rather than fabricate a number.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _http import ConnectorError, cache_read, cache_write, http_get, now_iso  # noqa: E402

# A contact UA is mandatory for SEC. Public project contact only — no secrets/PII.
# (No Accept-Encoding: urllib won't auto-gunzip, so we request plain JSON.)
_UA = {"User-Agent": "AutoTrade research desk (contact: research@lidisolutions.ai)"}
_UA_WWW = _UA

TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"

# Candidate XBRL concept names (first present wins), per logical field.
_CONCEPTS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "net_income": ["NetIncomeLoss"],
    "operating_income": ["OperatingIncomeLoss"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "dep_amort": ["DepreciationDepletionAndAmortization", "DepreciationAmortizationAndAccretionNet",
                  "DepreciationAndAmortization", "DepreciationAmortizationAndAccretionNetExcludingAmortizationOfDebtIssuanceCosts",
                  "CostOfGoodsAndServicesSoldDepreciationAndAmortization"],
    "shares_diluted": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    "cash": ["CashAndCashEquivalentsAtCarryingValue",
             "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"],
    "lt_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "cur_debt": ["LongTermDebtCurrent", "DebtCurrent"],
    "equity": ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
}


def _ticker_map() -> dict[str, int]:
    cached = cache_read("edgar_ticker_cik", max_age_h=168.0)
    if cached and "map" in cached:
        return {k: int(v) for k, v in cached["map"].items()}
    raw = http_get(TICKERS_URL, headers=_UA_WWW, retries=2)
    import json
    data = json.loads(raw)
    m: dict[str, int] = {}
    for row in data.values():
        t = str(row.get("ticker", "")).upper()
        if t:
            m[t] = int(row["cik_str"])
    if not m:
        raise ConnectorError("edgar: empty ticker->cik map")
    cache_write("edgar_ticker_cik", {"map": m})
    return m


def cik_for(ticker: str) -> int:
    t = ticker.upper().strip()
    m = _ticker_map()
    if t not in m:
        raise ConnectorError(f"edgar: no SEC filer for {t} (ETF/ADR/foreign?)")
    return m[t]


def _facts(ticker: str) -> dict[str, Any]:
    t = ticker.upper().strip()
    cached = cache_read(f"edgar_facts_{t}", max_age_h=24.0)
    if cached and "facts" in cached:
        return cached["facts"]
    cik = cik_for(t)
    import json
    raw = http_get(FACTS_URL.format(cik=cik), headers=_UA, retries=2)
    facts = json.loads(raw)
    cache_write(f"edgar_facts_{t}", {"facts": facts})
    return facts


def _unit_points(facts: dict, names: list[str], unit_pref=("USD", "shares")) -> list[dict]:
    """Return the raw fact points for the candidate concept with the FRESHEST
    data. Companies often switch XBRL concepts over time (e.g. NVDA moved revenue
    from RevenueFromContractWithCustomer... to Revenues); picking the first that
    merely *exists* can grab a stale, abandoned concept. We instead pick the
    candidate whose most-recent point is newest (us-gaap preferred over dei)."""
    best: list[dict] = []
    best_key: tuple = ("", "")
    for ns_rank, ns in enumerate(("dei", "us-gaap")):  # us-gaap wins ties (higher rank)
        block = (facts.get("facts") or {}).get(ns) or {}
        for name in names:
            concept = block.get(name)
            if not concept:
                continue
            units = concept.get("units") or {}
            pts = None
            for u in unit_pref:
                if u in units and units[u]:
                    pts = units[u]
                    break
            if pts is None:
                for v in units.values():
                    if v:
                        pts = v
                        break
            if not pts:
                continue
            latest_end = max((p.get("end", "") for p in pts), default="")
            key = (latest_end, ns_rank)
            if key > best_key:
                best_key, best = key, pts
    return best


def _days(p: dict) -> int | None:
    s, e = p.get("start"), p.get("end")
    if not s or not e:
        return None
    from datetime import date
    try:
        return (date.fromisoformat(e) - date.fromisoformat(s)).days
    except ValueError:
        return None


def _latest_annual(points: list[dict]) -> dict | None:
    ann = [p for p in points if (_days(p) or 0) >= 330 and (_days(p) or 0) <= 400 and "val" in p]
    return max(ann, key=lambda p: p["end"]) if ann else None


def _annual_series(points: list[dict]) -> dict[int, float]:
    out: dict[int, float] = {}
    for p in points:
        d = _days(p)
        if d and 330 <= d <= 400 and "val" in p:
            yr = int(p["end"][:4])
            out[yr] = float(p["val"])  # later (higher end) overwrites — fine, same FY
    return out


def _ttm(points: list[dict]) -> float | None:
    """Sum the 4 most recent non-overlapping ~quarter points (flows). Falls back
    to None if a clean trailing-twelve-months can't be assembled."""
    q = [p for p in points if (_days(p) and 80 <= _days(p) <= 100) and "val" in p]
    if len(q) < 4:
        return None
    q.sort(key=lambda p: p["end"], reverse=True)
    chosen, last_start = [], None
    for p in q:
        if last_start is None or p["end"] <= last_start:
            chosen.append(p)
            last_start = p["start"]
        if len(chosen) == 4:
            break
    if len(chosen) < 4:
        return None
    return float(sum(p["val"] for p in chosen))


def _latest_instant(points: list[dict]) -> dict | None:
    inst = [p for p in points if _days(p) is None and "val" in p and "end" in p]
    return max(inst, key=lambda p: p["end"]) if inst else None


def _flow(facts: dict, key: str) -> tuple[float | None, float | None, dict]:
    """(ttm, latest_annual_val, annual_series) for a flow concept."""
    pts = _unit_points(facts, _CONCEPTS[key])
    la = _latest_annual(pts)
    return _ttm(pts), (la["val"] if la else None), _annual_series(pts)


def _instant(facts: dict, key: str) -> float | None:
    p = _latest_instant(_unit_points(facts, _CONCEPTS[key]))
    return float(p["val"]) if p else None


def fundamentals(ticker: str) -> dict[str, Any]:
    """Normalized fundamentals for a US filer. Prefers TTM for flows, falls back
    to latest annual. Raises ConnectorError when SEC has no usable facts."""
    t = ticker.upper().strip()
    facts = _facts(t)

    rev_ttm, rev_ann, rev_series = _flow(facts, "revenue")
    ni_ttm, ni_ann, _ = _flow(facts, "net_income")
    oi_ttm, oi_ann, _ = _flow(facts, "operating_income")
    cfo_ttm, cfo_ann, _ = _flow(facts, "cfo")
    capex_ttm, capex_ann, _ = _flow(facts, "capex")
    da_ttm, da_ann, _ = _flow(facts, "dep_amort")

    # Prefer the clean annual (10-K) figure for stability; a hand-rolled TTM from
    # XBRL quarter facts is error-prone (restatements/overlap) and valuation is a
    # long-horizon number, so annual is the more defensible base.
    def pick(ttm, ann):
        return ann if ann is not None else ttm

    revenue = pick(rev_ttm, rev_ann)
    net_income = pick(ni_ttm, ni_ann)
    operating_income = pick(oi_ttm, oi_ann)
    cfo = pick(cfo_ttm, cfo_ann)
    capex = pick(capex_ttm, capex_ann)
    dep_amort = pick(da_ttm, da_ann)

    # shares: diluted weighted-average (us-gaap, dilution-aware), else the most
    # recent dei/us-gaap shares-outstanding instant from the cover page.
    sh_la = _latest_annual(_unit_points(facts, _CONCEPTS["shares_diluted"], unit_pref=("shares",)))
    shares = float(sh_la["val"]) if sh_la else None
    if not shares:
        p = _latest_instant(_unit_points(facts, ["EntityCommonStockSharesOutstanding",
                                                 "CommonStockSharesOutstanding"], unit_pref=("shares",)))
        shares = float(p["val"]) if p else None

    cash = _instant(facts, "cash")
    lt_debt = _instant(facts, "lt_debt") or 0.0
    cur_debt = _instant(facts, "cur_debt") or 0.0
    total_debt = (lt_debt + cur_debt) or None
    equity = _instant(facts, "equity")

    fcf = (cfo - capex) if (cfo is not None and capex is not None) else None
    ebitda = (operating_income + dep_amort) if (operating_income is not None and dep_amort is not None) else None
    net_debt = ((total_debt or 0.0) - (cash or 0.0)) if (total_debt is not None or cash is not None) else None
    eps_ttm = (net_income / shares) if (net_income and shares) else None

    if revenue is None and net_income is None and cfo is None:
        raise ConnectorError(f"edgar: no usable financial facts for {t}")

    return {
        "ticker": t,
        "cik": facts.get("cik"),
        "name": facts.get("entityName"),
        "revenue": revenue,
        "net_income": net_income,
        "operating_income": operating_income,
        "cfo": cfo,
        "capex": capex,
        "dep_amort": dep_amort,
        "fcf": fcf,
        "ebitda": ebitda,
        "shares": shares,
        "cash": cash,
        "total_debt": total_debt,
        "net_debt": net_debt,
        "equity": equity,
        "eps_ttm": eps_ttm,
        "revenue_series": rev_series,
        "is_ttm": rev_ann is None and rev_ttm is not None,
        "source": "sec-edgar/companyfacts",
        "as_of": now_iso(),
    }


if __name__ == "__main__":
    import json as _json
    sym = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    try:
        print(_json.dumps(fundamentals(sym), indent=2, default=str))
    except ConnectorError as e:
        print(f"ConnectorError: {e}")
        sys.exit(2)
