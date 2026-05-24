"""FMP adapter — analyst-sentiment support only.

Authority: ratings breadth, price-target context, estimate trends.
Cannot rescue a failed catalyst gate.

Uses FMP `/stable` endpoints (legacy `/api/v3` requires pre-Aug-2025 sub).
All calls fail-soft → return None on any error so ranking is never poisoned.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

from ..credentials import fmp_key
from ..http_util import http_get_json, write_cache

BASE = "https://financialmodelingprep.com/stable"


def _get(endpoint: str, ticker: str, params: Optional[dict] = None) -> Optional[list | dict]:
    p = {"symbol": ticker, "apikey": fmp_key(), **(params or {})}
    try:
        out = http_get_json(f"{BASE}/{endpoint}", params=p)
    except Exception:
        return None
    if isinstance(out, dict) and "Error Message" in out:
        write_cache("fmp", ticker, f"{endpoint}.error", out)
        return None
    write_cache("fmp", ticker, endpoint.replace("/", "_"), out)
    return out


def _first(out) -> Optional[dict]:
    if isinstance(out, list) and out:
        return out[0]
    return None


def grades_consensus(ticker: str) -> Optional[dict]:
    """Returns {strongBuy, buy, hold, sell, strongSell, consensus}."""
    return _first(_get("grades-consensus", ticker))


def ratings_snapshot(ticker: str) -> Optional[dict]:
    """Returns {rating, overallScore, ...subscores}."""
    return _first(_get("ratings-snapshot", ticker))


def price_target_consensus(ticker: str) -> Optional[dict]:
    """Returns {targetHigh, targetLow, targetConsensus, targetMedian}."""
    return _first(_get("price-target-consensus", ticker))


def price_target_summary(ticker: str) -> Optional[dict]:
    """Returns {lastMonthCount, lastMonthAvgPriceTarget, lastQuarterCount, ...}."""
    return _first(_get("price-target-summary", ticker))


# ===================================================================
# Bundle helper — single call, all FMP fields normalized.
# ===================================================================

@dataclass
class FmpBundle:
    ticker: str
    available: bool = False
    consensus: Optional[str] = None
    buy_count: Optional[int] = None
    hold_count: Optional[int] = None
    sell_count: Optional[int] = None
    strong_buy_count: Optional[int] = None
    strong_sell_count: Optional[int] = None
    target_consensus: Optional[float] = None
    target_high: Optional[float] = None
    target_low: Optional[float] = None
    target_activity_last_month: Optional[int] = None
    rating_letter: Optional[str] = None
    overall_score: Optional[int] = None

    def as_dict(self) -> dict:
        return asdict(self)


def bundle(ticker: str) -> FmpBundle:
    """One-shot: pull all FMP fields, return normalized FmpBundle.

    Always returns an FmpBundle; check `.available` to see if any data landed.
    """
    b = FmpBundle(ticker=ticker.upper())
    gc = grades_consensus(ticker)
    if gc:
        b.available = True
        b.consensus = gc.get("consensus")
        b.buy_count = gc.get("buy")
        b.hold_count = gc.get("hold")
        b.sell_count = gc.get("sell")
        b.strong_buy_count = gc.get("strongBuy")
        b.strong_sell_count = gc.get("strongSell")
    ptc = price_target_consensus(ticker)
    if ptc:
        b.available = True
        b.target_consensus = ptc.get("targetConsensus")
        b.target_high = ptc.get("targetHigh")
        b.target_low = ptc.get("targetLow")
    pts = price_target_summary(ticker)
    if pts:
        b.available = True
        b.target_activity_last_month = pts.get("lastMonthCount")
    rs = ratings_snapshot(ticker)
    if rs:
        b.available = True
        b.rating_letter = rs.get("rating")
        b.overall_score = rs.get("overallScore")
    return b
