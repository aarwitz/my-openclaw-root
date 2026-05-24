from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Optional

from . import cache_manager, regime as regime_mod
from .adapters import alpaca, massive
from .http_util import now_iso
from .x_monitor import fetch_events


@dataclass
class BasketIdea:
    ticker: str
    role: str
    rationale: str
    liquidity_score: float
    rel_strength_5d: Optional[float]
    expression_type: str


@dataclass
class EventAlphaIdea:
    source: str
    account: str
    posted_at: str
    event_type: str
    summary: str
    themes: list[str]
    first_order: list[dict] = field(default_factory=list)
    sympathy: list[dict] = field(default_factory=list)
    preferred_expression: Optional[dict] = None
    timing_label: str = "observe"
    requires_confirmation: bool = True
    confidence: float = 0.0
    notes: Optional[str] = None


THEME_BASKETS = {
    'semiconductors': ['INTC', 'NVDA', 'AMD', 'TSM', 'SMH', 'SOXL', 'QQQ', 'XLK'],
    'critical_minerals': ['LAC', 'ALB', 'MP', 'USAR', 'LIT', 'XME'],
    'nuclear_power': ['OKLO', 'SMR', 'CCJ', 'URA'],
    'defense': ['LMT', 'RTX', 'NOC', 'ITA'],
    'government_support': ['INTC', 'LAC', 'USAR', 'XLI', 'QQQ'],
    'trade_policy': ['XLI', 'CAT', 'DE', 'FCX', 'XME'],
    'domestic_manufacturing': ['INTC', 'XLI', 'CAT', 'DE', 'VRT'],
}

KNOWN_TICKERS = {
    'INTC','USAR','LAC','OKLO','SMR','CCJ','NVDA','AMD','TSM','QQQ','XLK','XLI','LMT','RTX','NOC','ITA','ALB','MP','LIT','XME','CAT','DE','FCX','VRT','SMH','SOXL','PLTR','BA','GE','RKLB'
}

ENTITY_TO_TICKERS = {
    'nvidia': ['NVDA', 'SMH', 'SOXL', 'QQQ', 'XLK'],
    'intel': ['INTC', 'SMH', 'SOXL', 'QQQ', 'XLK'],
    'jensen huang': ['NVDA', 'SMH', 'QQQ'],
    'lithium': ['LAC', 'ALB', 'LIT'],
    'rare earth': ['MP', 'USAR', 'XME'],
    'uranium': ['CCJ', 'URA', 'SMR', 'OKLO'],
    'nuclear': ['OKLO', 'SMR', 'CCJ', 'URA'],
    'pentagon': ['LMT', 'RTX', 'NOC', 'ITA'],
    'defense': ['LMT', 'RTX', 'NOC', 'ITA'],
    'china': ['FXI', 'BABA', 'JD', 'YINN'],
}


def _clean_text(text: str) -> str:
    return re.sub(r'https?://\S+', '', text).strip()



def _extract_known_tickers(text: str) -> list[str]:
    upper = text.upper()
    lower = text.lower()
    found = []
    for tk in KNOWN_TICKERS:
        if re.search(rf'\b{re.escape(tk)}\b', upper) and tk not in found:
            found.append(tk)
    for tk in re.findall(r'\$([A-Z]{1,5})\b', upper):
        if tk not in found:
            found.append(tk)
    for entity, mapped in ENTITY_TO_TICKERS.items():
        if entity in lower:
            for tk in mapped:
                if tk not in found:
                    found.append(tk)
    return found[:10]



def _infer_themes(text: str, existing: list[str]) -> list[str]:
    out = list(existing)
    lower = text.lower()
    rules = {
        'government_support': ['government', 'grant', 'funding', 'support', 'procurement', 'department of'],
        'trade_policy': ['tariff', 'trade war', 'import', 'export control', 'export ban'],
        'semiconductors': ['chips', 'semi', 'intel', 'semiconductor'],
        'critical_minerals': ['lithium', 'rare earth', 'critical mineral'],
        'nuclear_power': ['nuclear', 'uranium', 'reactor'],
        'defense': ['pentagon', 'defense', 'missile', 'drone', 'military'],
        'domestic_manufacturing': ['factory', 'manufacturing', 'reshoring', 'made in america'],
    }
    for theme, kws in rules.items():
        if theme not in out and any(k in lower for k in kws):
            out.append(theme)
    return out



def _liq_and_rs(ticker: str) -> tuple[float, Optional[float]]:
    try:
        bars = massive.daily_aggregates(ticker, lookback_days=30)
        if not bars:
            return 0.0, None
        last = float(bars[-1].get('c') or 0)
        vol = float(bars[-1].get('v') or 0)
        dv = (last * vol) / 1e6
        rs = massive.pct_change(bars, 5)
        score = 3.0 if dv >= 500 else 2.0 if dv >= 100 else 1.0 if dv >= 25 else 0.0
        return score, rs
    except Exception:
        return 0.0, None



def _basket_for_event(themes: list[str], tickers: list[str]) -> tuple[list[BasketIdea], list[BasketIdea]]:
    first = []
    sympathy = []
    for tk in tickers:
        liq, rs = _liq_and_rs(tk)
        first.append(BasketIdea(ticker=tk, role='first_order', rationale='explicitly referenced / directly implicated', liquidity_score=liq, rel_strength_5d=rs, expression_type='single_stock'))
    extra = []
    for th in themes:
        extra.extend(THEME_BASKETS.get(th, []))
    seen = {b.ticker for b in first}
    for tk in extra:
        if tk in seen:
            continue
        seen.add(tk)
        liq, rs = _liq_and_rs(tk)
        expr = 'leveraged_etf' if tk in {'SOXL'} else 'etf' if tk in {'QQQ','XLK','XLI','LIT','XME','URA','ITA','SMH'} else 'single_stock'
        sympathy.append(BasketIdea(ticker=tk, role='sympathy', rationale='theme basket / liquid expression', liquidity_score=liq, rel_strength_5d=rs, expression_type=expr))
    sympathy.sort(key=lambda x: (x.liquidity_score, x.rel_strength_5d or -999), reverse=True)
    return first, sympathy[:8]



def _preferred_expression(first: list[BasketIdea], sympathy: list[BasketIdea]) -> Optional[dict]:
    candidates = first + sympathy
    if not candidates:
        return None
    candidates = sorted(candidates, key=lambda x: (x.liquidity_score, x.rel_strength_5d or -999), reverse=True)
    top = candidates[0]
    return asdict(top)



def _timing_label(posted_at: str) -> str:
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(posted_at.replace('Z', '+00:00'))
        hour = dt.astimezone().hour
        if hour < 9:
            return 'premarket_prepare'
        if 9 <= hour < 15:
            return 'intraday_tradeable'
        if 15 <= hour < 16:
            return 'late_day_caution'
        return 'after_hours_prepare'
    except Exception:
        return 'observe'



def generate_event_alpha(accounts: Optional[list[str]] = None, limit_per_account: int = 3) -> dict:
    events = fetch_events(accounts=accounts, limit_per_account=limit_per_account)
    regime = regime_mod.compute()
    ideas = []
    for e in events:
        text = _clean_text(e.get('text') or '')
        themes = _infer_themes(text, list(e.get('theme_tags') or []))
        tickers = _extract_known_tickers(text)
        first, sympathy = _basket_for_event(themes, tickers)
        tradeable = bool(themes or tickers)
        idea = EventAlphaIdea(
            source=e.get('source') or 'x',
            account=e.get('account') or '',
            posted_at=e.get('posted_at') or now_iso(),
            event_type=e.get('event_type') or 'unknown',
            summary=text[:280],
            themes=themes,
            first_order=[asdict(x) for x in first],
            sympathy=[asdict(x) for x in sympathy],
            preferred_expression=_preferred_expression(first, sympathy),
            timing_label=_timing_label(e.get('posted_at') or now_iso()),
            requires_confirmation=bool(e.get('requires_confirmation', True)),
            confidence=float(e.get('confidence') or 0),
            notes='No mapped basket yet' if not tradeable else None,
        )
        ideas.append(asdict(idea))
    ideas = [i for i in ideas if i.get('confidence', 0) >= 0.2]
    return {
        'as_of': now_iso(),
        'regime': asdict(regime),
        'accounts': accounts or ['TrumpDailyPosts', 'BillAckman'],
        'ideas': ideas,
    }


if __name__ == '__main__':
    print(json.dumps(generate_event_alpha(), indent=2))
