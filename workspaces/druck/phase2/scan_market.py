"""High-level scan-market orchestrator (MVP top-50 ranker).

Combines:
  - candidate_gen.generate_pools()  — multi-source discovery with tags
  - market_scanner.scan_market()    — enrichment + hard filters
  - catalyst_verifier.verify_many() — Finnhub catalyst tagging
  - regime.compute()                — SPY/VIX context
  - alpha_ranker.rank()             — 1-week SPY-beat odds

Returns the "top 50 liquid US stocks/ETFs most likely to outperform SPY"
that Druck specified as the most-impactful first deliverable.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Iterable, Optional

from . import alpha_ranker, candidate_gen, catalyst_verifier, market_scanner, regime as regime_mod
from .adapters import massive
from .universe import SECTOR_ETFS


@dataclass
class TopPick:
    ticker: str
    asset_type: str
    last_price: Optional[float]
    pct_change_1d: Optional[float]
    pct_change_5d: Optional[float]
    dollar_volume: Optional[float]
    volume_ratio: Optional[float]
    sector: Optional[str]
    catalyst_tag: str
    catalyst_confidence: float
    extension_flag: bool
    benchmark_relative_score: float
    score: float
    confidence: float
    dominant_risk: Optional[str]
    source_tags: list[str]
    components: dict
    penalties: dict

    def as_dict(self) -> dict:
        return asdict(self)


def _sector_5d_map() -> dict[str, float]:
    """Pull 5d % change for each sector ETF."""
    out: dict[str, float] = {}
    for etf in SECTOR_ETFS.values():
        try:
            bars = massive.daily_aggregates(etf, lookback_days=15)
            mv = massive.pct_change(bars, 5)
            if mv is not None:
                out[etf] = mv
        except Exception:
            continue
    return out


def run(
    *,
    seed: Optional[Iterable[str]] = None,
    top_n: int = 50,
    include_ipos: bool = False,
    verify_catalysts: bool = True,
) -> dict:
    """Run the MVP scan + rank. Returns dict with metadata + top picks.

    Output schema:
        {
          "regime": {...},
          "spy_5d_pct": float,
          "universe_size": int,
          "scanned_size": int,
          "top_picks": [TopPick.as_dict(), ...],
          "buckets": {...}  # ranked-list shortcuts
        }
    """
    # ---- 1. discover candidate universe with source tags ----
    pools = candidate_gen.generate_pools(
        seed=seed,
        max_total=120,
        include_movers=True,
        include_sector_leaders=True,
        include_ipos=include_ipos,
    )
    pool_tags: dict[str, list[str]] = {cp.ticker: cp.source_tags for cp in pools}

    # ---- 2. enrich via market_scanner (filters + LiquidMover) ----
    movers = market_scanner.scan_market(
        extra_seed=[cp.ticker for cp in pools],
        include_etfs=True,
        include_unusual_vol=False,  # already covered via candidate_gen pool
        max_total=200,
    )
    # merge source_tags from candidate pools into mover records
    by_ticker = {m.ticker: m for m in movers}
    for tk, tags in pool_tags.items():
        if tk in by_ticker:
            for t in tags:
                if t not in by_ticker[tk].source_tags:
                    by_ticker[tk].source_tags.append(t)

    # ---- 3. catalyst verification (parallel) ----
    catalysts: dict[str, catalyst_verifier.CatalystResult] = {}
    if verify_catalysts:
        catalysts = catalyst_verifier.verify_many(list(by_ticker.keys()), workers=8)

    # ---- 4. regime + benchmarks ----
    rg = regime_mod.compute()
    spy_bars = []
    try:
        spy_bars = massive.daily_aggregates("SPY", lookback_days=15)
    except Exception:
        pass
    spy_5d = massive.pct_change(spy_bars, 5) if spy_bars else None
    sector_5d = _sector_5d_map()

    # ---- 5. rank ----
    ranked = alpha_ranker.rank(
        list(by_ticker.values()),
        catalysts,
        regime=rg.regime,
        spy_5d_pct=spy_5d,
        sector_5d_map=sector_5d,
        top_n=top_n,
    )

    top_picks: list[TopPick] = []
    for m, s in ranked:
        cat = catalysts.get(m.ticker)
        top_picks.append(TopPick(
            ticker=m.ticker,
            asset_type=m.asset_type,
            last_price=m.last_price,
            pct_change_1d=m.pct_change_1d,
            pct_change_5d=m.pct_change_5d,
            dollar_volume=m.dollar_volume,
            volume_ratio=m.volume_ratio,
            sector=m.sector,
            catalyst_tag=(cat.catalyst_type if cat else "none"),
            catalyst_confidence=(cat.catalyst_confidence if cat else 0.0),
            extension_flag=m.extension_flag,
            benchmark_relative_score=s.benchmark_relative_score,
            score=s.score,
            confidence=s.confidence,
            dominant_risk=s.dominant_risk,
            source_tags=m.source_tags,
            components=s.components,
            penalties=s.penalties,
        ))

    return {
        "regime": asdict(rg),
        "spy_5d_pct": spy_5d,
        "universe_size": len(pools),
        "scanned_size": len(by_ticker),
        "top_picks": [tp.as_dict() for tp in top_picks],
        "buckets": market_scanner.ranked_buckets(list(by_ticker.values())),
    }


def format_text(report: dict, n: int = 25) -> str:
    """Human-readable summary of scan-market output."""
    lines: list[str] = []
    rg = report.get("regime") or {}
    lines.append(
        f"Regime: {rg.get('regime')} | SPY 5d: {report.get('spy_5d_pct')} "
        f"| VIX: {rg.get('vix_close')}"
    )
    lines.append(
        f"Universe={report.get('universe_size')} Scanned={report.get('scanned_size')} "
        f"Top picks (score, ticker, 5d%, $vol M, catalyst, risk):"
    )
    lines.append("-" * 90)
    for tp in report.get("top_picks", [])[:n]:
        lines.append(
            f"  {tp['score']:>5.1f}  {tp['ticker']:<6}  "
            f"5d={(tp['pct_change_5d'] or 0)*100:>+5.1f}%  "
            f"$vol={tp['dollar_volume'] or 0:>6.0f}M  "
            f"cat={tp['catalyst_tag']:<24}  "
            f"risk={tp['dominant_risk'] or '-'}"
        )
    return "\n".join(lines)
