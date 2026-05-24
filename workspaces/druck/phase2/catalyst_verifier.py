"""Catalyst verification pipeline (Priority 3).

Given a discovered ticker (e.g. from market_scanner), confirm WHY it's moving
using Finnhub authoritative sources (calendar + news + analyst).

Returns a CatalystResult that downstream alpha_ranker uses to gate / weight.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date as _date
from typing import Optional

from .adapters import finnhub
from .schema import CatalystType


@dataclass
class CatalystResult:
    ticker: str
    catalyst_type: str = CatalystType.NONE.value
    catalyst_source: Optional[str] = None       # "finnhub.earnings_calendar", etc.
    catalyst_headline: Optional[str] = None
    catalyst_date: Optional[str] = None
    catalyst_confidence: float = 0.0            # 0..1

    def as_dict(self) -> dict:
        return asdict(self)


def verify(ticker: str, *, days_back: int = 14) -> CatalystResult:
    """Run full catalyst-verification chain for one ticker. Always returns a result."""
    res = CatalystResult(ticker=ticker.upper())

    # ---- 1. earnings calendar — double beat is strongest ----
    try:
        ec = finnhub.earnings_calendar(ticker, days_back=days_back)
    except Exception:
        ec = []
    db = finnhub.detect_double_beat(ec)
    if db:
        res.catalyst_type = CatalystType.EARNINGS_DOUBLE_BEAT.value
        res.catalyst_source = "finnhub.earnings_calendar"
        res.catalyst_date = db.get("date")
        res.catalyst_headline = (
            f"EPS {db.get('epsActual')} vs {db.get('epsEstimate')} | "
            f"Rev {db.get('revenueActual')} vs {db.get('revenueEstimate')}"
        )
        res.catalyst_confidence = 0.9
        return res

    # ---- 2. company news — guidance raise / major event ----
    try:
        news = finnhub.company_news(ticker, days_back=days_back)
    except Exception:
        news = []

    gr = finnhub.detect_guidance_raise(news)
    if gr:
        res.catalyst_type = CatalystType.GUIDANCE_RAISE.value
        res.catalyst_source = "finnhub.company_news"
        res.catalyst_headline = gr.get("headline")
        ts = gr.get("datetime")
        res.catalyst_date = _ts_to_iso(ts)
        res.catalyst_confidence = 0.75
        return res

    me = finnhub.detect_major_event(news)
    if me:
        res.catalyst_type = CatalystType.MAJOR_CORPORATE_EVENT.value
        res.catalyst_source = "finnhub.company_news"
        res.catalyst_headline = me.get("headline")
        res.catalyst_date = _ts_to_iso(me.get("datetime"))
        res.catalyst_confidence = 0.6
        return res

    # ---- 3. recent earnings without double-beat — partial credit ----
    if ec:
        latest = sorted(ec, key=lambda r: r.get("date", ""), reverse=True)[0]
        latest_date = latest.get("date")
        if latest_date:
            try:
                d = _date.fromisoformat(latest_date)
                age = (_date.today() - d).days
                if -1 <= age <= 7:
                    res.catalyst_type = CatalystType.NONE.value
                    res.catalyst_source = "finnhub.earnings_calendar"
                    res.catalyst_headline = "recent earnings, not a double beat"
                    res.catalyst_date = latest_date
                    res.catalyst_confidence = 0.2
                    return res
            except Exception:
                pass

    # ---- 4. nothing found ----
    return res


def _ts_to_iso(ts) -> Optional[str]:
    """Finnhub news datetime is unix seconds. Convert to ISO date."""
    if ts is None:
        return None
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()
    except Exception:
        return None


def verify_many(tickers: list[str], *, workers: int = 6, days_back: int = 14) -> dict[str, CatalystResult]:
    """Parallel verification across many tickers. Returns ticker -> CatalystResult."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    out: dict[str, CatalystResult] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(verify, t, days_back=days_back): t for t in tickers}
        for f in as_completed(futures, timeout=180):
            try:
                cr = f.result(timeout=30)
                out[cr.ticker] = cr
            except Exception:
                pass
    return out
