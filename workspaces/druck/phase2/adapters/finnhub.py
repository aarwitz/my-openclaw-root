"""Finnhub adapter — catalyst truth.

Authority:
  earnings actuals/estimates, company news, analyst revisions, basic quote.

Not used for: ATR / multi-day price structure (Massive), execution (Alpaca),
analyst-sentiment breadth (FMP).
"""
from __future__ import annotations

from datetime import date as _date, timedelta
from typing import Optional

from ..credentials import finnhub_key
from ..http_util import http_get_json, write_cache, now_iso

BASE = "https://finnhub.io/api/v1"


def quote(ticker: str) -> dict:
    p = http_get_json(f"{BASE}/quote", params={"symbol": ticker, "token": finnhub_key()})
    write_cache("finnhub", ticker, "quote", p)
    return p


def earnings_calendar(ticker: str, days_back: int = 14, days_fwd: int = 0) -> list[dict]:
    today = _date.today()
    frm = (today - timedelta(days=days_back)).isoformat()
    to = (today + timedelta(days=days_fwd)).isoformat()
    p = http_get_json(
        f"{BASE}/calendar/earnings",
        params={"from": frm, "to": to, "symbol": ticker, "token": finnhub_key()},
    )
    write_cache("finnhub", ticker, "earnings_calendar", p)
    return p.get("earningsCalendar") or []


def market_earnings_calendar(days_back: int = 10) -> list[dict]:
    today = _date.today()
    frm = (today - timedelta(days=days_back)).isoformat()
    to = today.isoformat()
    p = http_get_json(
        f"{BASE}/calendar/earnings",
        params={"from": frm, "to": to, "token": finnhub_key()},
    )
    write_cache("finnhub", "_market", f"earnings_calendar_{days_back}d", p)
    return p.get("earningsCalendar") or []


def company_news(ticker: str, days_back: int = 10) -> list[dict]:
    today = _date.today()
    frm = (today - timedelta(days=days_back)).isoformat()
    to = today.isoformat()
    p = http_get_json(
        f"{BASE}/company-news",
        params={"symbol": ticker, "from": frm, "to": to, "token": finnhub_key()},
    )
    write_cache("finnhub", ticker, "company_news", p)
    return p if isinstance(p, list) else []


def company_profile(ticker: str) -> dict:
    p = http_get_json(
        f"{BASE}/stock/profile2",
        params={"symbol": ticker, "token": finnhub_key()},
    )
    write_cache("finnhub", ticker, "profile2", p)
    return p


# ---------- catalyst-detection helpers ----------

GUIDANCE_KEYWORDS = (
    "raises guidance", "raised guidance", "increases outlook",
    "raises outlook", "boosts forecast", "raises full-year",
    "above prior", "lifts forecast", "ups forecast", "raises fy",
)

EVENT_KEYWORDS = {
    "fda": ("fda approval", "fda clearance", "phase 3", "phase iii"),
    "ma":  ("acquires", "to acquire", "merger", "buyout", "takeover"),
    "contract": ("awarded contract", "wins contract", "signs deal", "selected by"),
    "partnership": ("partnership", "strategic alliance"),
    "buyback": ("share repurchase", "buyback", "repurchase program"),
}


def detect_double_beat(ec: list[dict]) -> Optional[dict]:
    """Return the most-recent earnings event that was a double beat, else None."""
    for row in sorted(ec, key=lambda r: r.get("date", ""), reverse=True):
        try:
            ea, ee = row.get("epsActual"), row.get("epsEstimate")
            ra, re_ = row.get("revenueActual"), row.get("revenueEstimate")
            if ea is None or ee is None or ra is None or re_ is None:
                continue
            if float(ea) > float(ee) and float(ra) > float(re_):
                return row
        except (TypeError, ValueError):
            continue
    return None


def detect_guidance_raise(news: list[dict]) -> Optional[dict]:
    for n in news:
        h = (n.get("headline") or "").lower()
        if any(k in h for k in GUIDANCE_KEYWORDS):
            return n
    return None


def detect_major_event(news: list[dict]) -> Optional[dict]:
    for n in news:
        h = (n.get("headline") or "").lower()
        for cat, kws in EVENT_KEYWORDS.items():
            if any(k in h for k in kws):
                return {**n, "_event_category": cat}
    return None


def now() -> str:
    return now_iso()
