# AutoTrade — Data Sources Catalog

**Discipline:** a source only feeds *sized trading* after its features pass the FDR-corrected,
cost-net backtest gate (`mechanism_backtest.py`). Free live-only sources (no history) can be
**advisory** (shown in UI) or **forward-collected** (build history → validate later), never
sized un-validated. Status as of 2026-06-25.

## Legend
`LIVE` = featurized + trading · `KEYED` = credential works, not yet wired · `EXPAND` = paying for it, using a fraction · `BLOCKED` = needs key/refresh/paid tier · `ADVISORY` = no history, can't backtest

---

## Tier 1 — wired & trading (the current brain)
| Source | Cred | Cost | Features (live) | Notes |
|---|---|---|---|---|
| **Alpaca / FMP prices** | alpaca, fmp | paid | 7 technicals (rsi, mom, drawdown, vol, dist-sma…) | 37M price rows back to 2004 |
| **FMP fundamentals** | fmp | **paid (a lot)** | 8 (rev/eps ttm, margins, growth, surprise, insider, ratings) | **EXPAND — see Tier 3** |
| **Massive (Polygon)** | massive | **paid (a lot)** | news_sent_7d/30d, news_vol_z, days_to_cover, short_int_chg | **EXPAND — options/aggs unused** |
| **FRED macro** | (keyless) | free | 17 (rates curve, credit spreads, real yields, VIX, dollar, oil, fed funds) | **just expanded + validated; 16 live macro mechanisms** |
| **Sector ETF** | fmp | paid | sector_rel_63d | |

## Tier 2 — KEYED, unlock now (highest ROI)
| Source | Cred | Cost | What it adds | Backtestable? | Priority |
|---|---|---|---|---|---|
| **X / Twitter** | x-api | paid (have it) | cashtag **mention-volume** (counts API) + **sentiment** (tweets); social buzz, attention spikes | **YES — full-archive access confirmed** | **#1** |
| **Event Registry** | news-api-ai | free tier | entity-tagged news (precise ticker tagging vs lexical); richer than Massive lexicon | recent only (pair w/ Massive/GDELT for history) | #2 |

## Tier 3 — paying for it, under-utilized (expand)
| Source | Have | Currently | Unused high-value endpoints |
|---|---|---|---|
| **FMP** | ✅ paid | 8 fundamentals | analyst **price targets**, estimates & revisions, **institutional ownership** Δ, earnings-call transcripts, economic calendar, ETF holdings |
| **Massive/Polygon** | ✅ paid | news + short int | **options flow / open interest**, trade-level aggregates, intraday, technical indicators |
| **Finnhub** | ✅ key | UI risk_checks only | insider tx + analyst recs (free, overlaps FMP); social/news-sentiment are **premium (403)** |

## Tier 4 — supplementary / blocked
| Source | Status | Note |
|---|---|---|
| **Schwab** | BLOCKED (token expired) | OAuth refresh_token present; market data overlaps Alpaca/Massive — low priority |
| **EDGAR** | KEYLESS | SEC filings; overlaps FMP fundamentals; unique value = 8-K event timing |
| **AlphaVantage** | BLOCKED (no key) | connector exists, no credential |
| **GDELT** | ADVISORY | free global news tone, but **too rate-limited** for live |
| **StockTwits / Reddit** | ADVISORY | free crowd sentiment, **no history** — superseded by X full-archive |

---

## Recommended integration order
1. **X mention-volume + sentiment** (Tier 2 #1) — the validatable social signal you wanted. Smart path:
   start with the **counts endpoint** (cheap, full-history mention-volume + abnormal-spike features → backtest),
   then add **sentiment** from sampled archive tweets. Same loop as FRED: add → FDR-validate → wire live.
2. **Expand FMP** (price targets, estimates revisions, institutional ownership) — already paid, high signal.
3. **Expand Massive** (options flow / OI) — already paid; options positioning is strong edge.
4. **Activate Event Registry** for entity-precise news (improve the news features).
5. (later) Schwab refresh, EDGAR 8-K event timing.

Build scripts: connectors live in `workspaces/trading-intel/scripts/connectors/`; features are emitted by
`feature_store.py` (per-ticker) and `mechanism_backtest.py::_macro_series` (market-wide). Live firing reads
`signal_scan.py` (now merges macro via `latest_macro()` — mirror that pattern for any market-wide source).
