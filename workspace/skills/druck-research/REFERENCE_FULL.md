---
name: druck-research
description: Phase II trading research workflow for Druck (gates, scoring, penalties, regime, sizing, and write-back discipline).
metadata: {"openclaw":{"emoji":"📈","os":["linux"]}}
---

# druck-research

Phase II trading research workflow for Druck. Authoritative spec is `~/.openclaw/workspaces/druck/AUTONOMOUS_PM_OPERATING_MODEL.md`.

## When to use
- Aaron asks "what should I buy", "what's on the watchlist", "score X for me", "Monday picks", "weekly research"
- Saturday/Sunday/Monday cadence prompts
- Any portfolio-aware ranking

## Inputs (in order, fail closed)
1. Catalyst evidence — `finnhub` skill (earnings calendar, EPS/rev actuals+estimates, news, analyst revisions)
2. Price/volume — `massive` skill (daily aggregates → ATR%, dollar volume, 5d %, volume ratio)
3. Live corroboration near open — `alpaca` skill (latest quote/snapshot/intraday bars)
4. Analyst-sentiment support — `financialmodeling-prep-api` skill (grades breadth, price-target context, estimates when plan allows)
5. Live positions — `schwab` skill (positions endpoint, cached account hash)
6. Manual Robinhood holdings — `gog sheets get` on `Holdings` tab where account=Robinhood
7. Macro regime — `massive` for SPY + VIX daily aggregates

## Source hierarchy
- Finnhub decides catalyst truth
- Massive decides price-structure truth
- Alpaca decides live execution-context truth near the open
- FMP provides secondary analyst-sentiment confirmation only

## Routing policy
- Finnhub first for earnings, events, company news, and simple ticker triage
- Alpaca first for live/open checks and intraday confirmation
- FMP first for analyst-sentiment context
- Massive only for scoring-grade historical price structure and regime math

## Massive preservation rules
- use cache-first reads when adequate structure already exists
- prefer incremental updates over long-window refetches
- prioritize SPY/sector ETFs, holdings, watchlist, then broader discovery universe
- on `429`, stop gracefully, keep partial progress, and resume later
- prefer stale-but-labeled cached structure over a full workflow crash when a conservative fallback is possible

Anti-drift rules:
- sentiment cannot override a failed catalyst gate
- tape strength cannot substitute for event verification
- missing primary-source data is not neutral
- if a fallback source is used outside its normal authority, label it explicitly

## Constants
- Spreadsheet ID: `19LPX1xGCme4umn22GN4Z7WBQxGZBWWcysDjM6JEW-D4`
- Drive folder: `1AjzY_FuvtxrVtf1ejhE-WDHjbGdvU486`
- Notes doc: `1aWw94Tu8N4MZTmULeQBE3dOFPa1LPb7RZblNsZ_ks1g`
- Schwab token: `~/.openclaw/credentials/schwab-dev-token.json`
- Alpaca credentials: `~/.openclaw/credentials/alpaca-api.json`

## Catalyst gate (must pass at least one)
| type | source | rule |
|---|---|---|
| earnings_double_beat | finnhub | EPS_actual > EPS_est AND rev_actual > rev_est, last 10 trading days |
| guidance_raise | finnhub news | keyword match in last 10d |
| major_corporate_event | finnhub news | category in {contract, FDA, M&A, partnership, buyback ≥5% mcap}, last 5d |
| analyst_revision_cluster | finnhub | ≥3 up EPS revisions OR ≥2 PT raises, last 7d |
| sector_sympathy_confirmed | massive | sector ETF ≥ +2 ATR with named peers reacting, last 3d |

If none pass ⇒ `recommendation_class = watch_only` MAX. No exceptions.

## Setup states (label exactly one before scoring)
- `breakout_continuation` — close above 20d high, vol ≥ 1.5× avg, within 1 ATR of breakout
- `post_earnings_drift` — double-beat in 10d, between +0% and +1 ATR of post-earnings high
- `sell_the_news_digestion` — beat then sold off, now reclaiming pre-event level on volume
- `sympathy_momentum` — leader catalyst confirmed, peer correlation > 0.6
- `mean_reversion_bounce` — RSI(14) < 30 then close > prior day high, only quality non-extended names
- `overextended_chase` — > 2 ATR above 20d MA AND no fresh catalyst in 5d → AUTO `avoid` or `watch_only`

## Scoring (deterministic, 0–100 base)
| bucket | weight | scoring rule |
|---|---|---|
| catalyst_strength | 30 | double-beat + raised guide = 30; single beat = 12; analyst cluster = 15; sympathy confirmed = 10 |
| price_volume_confirmation | 20 | close vs 20d high (8) + vol ratio (8) + above 50d MA (4) |
| setup_quality | 15 | clean structure for label; 0 if structure inconsistent |
| sector_support | 10 | sector ETF 5d return rank percentile × 10 |
| portfolio_fit | 10 | adds missing factor = 10; neutral = 5; doubles existing = 0 |
| liquidity_quality | 5 | dollar vol ≥ $50M = 5; $20–50M = 3; <$20M = 0 (forces watch_only) |
| volatility_efficiency | 10 | (historical 5d move on similar setup) / ATR%, capped 0–10 |

## Penalties (separate, subtracted after base)
- `extension_penalty`: 0 to −15 — linear from 1.5 ATR to 2.5 ATR above 20d MA
- `crowding_penalty`: 0 to −10 — top-5 mention or up >25% in 10d
- `redundancy_penalty`: 0 to −10 — same sector AND factor as existing position

## Hard rules (apply after scoring + penalties)
- single penalty ≤ −10 → max class `conditional_buy`
- total penalties ≤ −20 → max class `watch_only`
- liquidity_score = 0 → `watch_only`
- setup = `overextended_chase` → never `buy_ready`
- if Alpaca live confirmation is required and unavailable near open → max class `watch_only`
- if Alpaca vs Massive intraday conflict is material → max class `conditional_buy` + `data_discrepancy` note

## Recommendation thresholds
- ≥75 + catalyst_pass + no hard rule → `buy_ready`
- 60–74 OR (≥75 with one hard rule tripped) → `conditional_buy`
- 40–59 → `watch_only`
- <40 OR forced by setup/liquidity → `avoid`

## Macro regime overlay (last step)
| regime | definition | action |
|---|---|---|
| risk_on | SPY > 20d > 50d MA, VIX < 18 | none |
| neutral | mixed | none |
| caution | SPY < 20d MA, VIX 18–25 | downgrade buy_ready→conditional_buy for breakout/sympathy |
| risk_off | SPY < 50d MA OR VIX > 25 | downgrade ALL buy_ready→conditional_buy; mean-rev→watch_only |
| crisis | SPY −5% in 5d OR VIX > 35 | all → watch_only |

## Position sizing output (advisory, no execution)
- per_name_risk_pct: 1.5 (conditional_buy) / 2.0 (buy_ready)
- position_$ = NAV × risk_pct / (1.5 × ATR$)
- initial_stop = entry − 1.5 × ATR
- time_stop = 10 trading days
- trail = breakeven after +1 ATR; trail by 1 ATR after +2 ATR
- caps: 15% single name, 35% sector, 50% factor

## Falsifier discipline
Every `buy_ready` row MUST include `falsifier_by_wednesday`: a single line stating what price/event by Wednesday close would prove the thesis wrong. Logged for hit-rate tracking.

## Sheet write protocol (idempotent + verified)
1. Compute row dict.
2. Check if `(date, ticker)` exists in `Candidates` via `gog sheets get Candidates!A:B`.
3. If exists: `gog sheets update` that row. Else: `gog sheets append`.
4. Read back the row and assert key fields match.
5. On mismatch: log to Notes doc and refuse to claim success.

## Workflow cadence
| Trigger | Action |
|---|---|
| Sat 10:00 ET | catalyst pull → raw JSON to Drive `raw/YYYY-MM-DD/` |
| Sat 11:00 ET | apply gate, classify, write `Candidates` rows with class=pending |
| Sun 19:00 ET | finalize scores + penalties + regime, assign class, write top-5 to Notes doc |
| Mon 08:30 ET | regime re-check, refresh quotes + Alpaca live confirmation, post top-5 to Druck Trading Desk Telegram |
| Fri 16:30 ET | fill Outcomes for past 10 days, mark falsifier_resolved |

## Non-goals
- No order placement
- No Reddit/Twitter (Phase III)
- No black-box ML
- No format/explanation that contradicts the score
