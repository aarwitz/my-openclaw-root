# Druck — Phase II Trading Research System

**Owner:** Aaron (aggressive, young, large savings, prioritizes high gains while staying convenient)
**Operator:** Druck agent
**Horizon:** 2–10 trading day swings, with Monday-open emphasis
**Status:** Phase II design — supersedes Phase I freeform scoring

---

## 0. Operating principles (non-negotiable)

1. **Event-driven, not vibes-driven.** A name without a verified catalyst cannot be top-ranked.
2. **Rules first, explanation second.** Numeric score is ground truth; prose explains it, never overrides it.
3. **Fail closed.** Missing required data ⇒ degrade to `watch_only` or `incomplete`, never assume favorable.
4. **Traceable.** Every score cell maps back to raw inputs stored in the same row.
5. **Idempotent.** Re-running a sync updates by `(date, ticker)` key, never duplicates.
6. **Verified writes.** Every Sheets/Docs/Drive mutation is read back and confirmed before claiming success.
7. **Capital-allocation safe.** If explanation and score disagree, the run is invalid — do not present.
8. **Aggressive ≠ careless.** Aggressive sizing is allowed; aggressive *risk-per-name* is not. Position size is volatility-targeted.

---

## 1. Data sources and authority

| Source | Used for | Authority |
|---|---|---|
| Finnhub | earnings calendar, EPS/rev actuals + estimates, company news, analyst revisions, quotes, fundamentals | catalyst evidence (primary) |
| Massive (Polygon) | daily aggregates, realized vol, ATR, dollar volume, sector ETF context | price/volume confirmation (primary) |
| Alpaca paper market data | latest quote, snapshot, intraday bars near the open | execution-timing confirmation (primary near open, secondary otherwise) |
| FMP | grades consensus, ratings breadth, price-target context, estimates where available | analyst-sentiment support (secondary) |
| Schwab Trader | live positions, balances, cost basis, account hash (cached); options chains (Phase III) | portfolio fit, IV/implied move |
| Manual Robinhood block | non-API holdings | portfolio fit only — never as price source |
| Google Sheets | control plane (4 tabs) | system-of-record |
| Google Drive folder | durable artifacts (raw JSON dumps, weekly snapshots) | audit trail |
| Notes doc | weekly research narrative | human-readable summary, never authoritative |
| Reddit / Twitter (X) | **Phase III only**, corroboration only, never standalone reason | bonus signal capped at +3 to a name already passing the catalyst gate |

### 1A. Source hierarchy and anti-drift rules

1. **Catalyst truth first.** Finnhub/news verification decides whether a gate passes. FMP sentiment, tape strength, or intuition cannot promote a name through a failed gate.
2. **Price structure second.** Massive determines multi-day setup quality, ATR framing, extension, and liquidity context.
3. **Execution confirmation third.** Alpaca is used near the open to confirm live spread/tape behavior and can downgrade or block a trade, but cannot invent a catalyst.
4. **Analyst sentiment is support only.** FMP can strengthen or weaken confidence around a thesis, but cannot rescue missing event evidence.
5. **Fail closed on missing authority data.** Missing primary-source fields are bearish for classification purposes, not neutral.
6. **No single-source overreach.** Do not use one provider outside its authority without labeling the fallback explicitly.

### 1B. Provider routing and Massive preservation policy

Default routing policy:
- **Finnhub first** for earnings, event verification, company news, and simple quote/context checks.
- **Alpaca first** for live market-open checks, spread sanity, latest quote/snapshot, and intraday confirmation.
- **FMP first** for analyst-consensus breadth, ratings context, and price-target context.
- **Massive only when needed** for scoring-grade historical price structure: ATR, moving averages, multi-day setup geometry, dollar volume, sector-relative pricing, and formal regime calculations.

Massive preservation rules:
1. Use **cache-first** reads whenever sufficient local history already exists.
2. Prefer **incremental bar updates** over repeated long-window refetches.
3. Pull symbols in **priority tiers**: SPY/VIX proxies + sector ETFs, current holdings, active watchlist, top catalyst candidates, then broader discovery names.
4. On `429` or quota stress, **stop gracefully**, retain partial progress, and resume later rather than failing the whole workflow.
5. If Massive data is temporarily unavailable, use cached structure if fresh enough and label stale fields; do not crash the whole run when a conservative fallback is possible.
6. Finnhub/Alpaca/FMP should absorb simple requests so Massive quota is reserved for the calculations that materially need it.

---

## 2. Hard catalyst gate (eligibility)

A ticker is eligible for `buy_ready` or `conditional_buy` only if **at least one** verified catalyst type is true within the lookback window:

| Catalyst type | Lookback | Verification source |
|---|---|---|
| `earnings_double_beat` | last 10 trading days | Finnhub: EPS actual > estimate AND revenue actual > estimate |
| `guidance_raise` | last 10 trading days | Finnhub news + transcript keyword match (`raise`, `above prior`, `increase outlook`) |
| `major_corporate_event` | last 5 trading days | Finnhub news with category in {contract, FDA, M&A, partnership, buyback ≥ 5% mcap} |
| `analyst_revision_cluster` | last 7 days | Finnhub: ≥ 3 upward EPS revisions OR ≥ 2 price-target raises |
| `sector_sympathy_confirmed` | last 3 trading days | sector ETF up ≥ 2 ATR with ≥ 2 named peers also reacting; the leading event must be cited |

If no gate passes ⇒ `recommendation_class = watch_only` maximum, regardless of score.

---

## 3. Setup-state classification

Every candidate is labeled exactly one:

- `breakout_continuation` — close above 20d high on ≥ 1.5× avg volume, price within 1 ATR of breakout level
- `post_earnings_drift` — earnings double-beat in last 10d, price between +0% and +1 ATR of post-earnings high, holding above the gap
- `sell_the_news_digestion` — earnings beat but immediate sell-off, now reclaiming pre-event level on volume
- `sympathy_momentum` — sector leader had verified catalyst; this name moved with peer correlation > 0.6
- `mean_reversion_bounce` — RSI(14) < 30 then closes above prior day high; only valid for non-extended quality names
- `overextended_chase` — > 2 ATR above 20d MA AND no fresh catalyst inside 5d; *automatic* `avoid` or `watch_only`

Setup label gates which scoring profile is applied (see §4).

---

## 4. Scoring (deterministic)

Base score = weighted sum, scaled 0–100.

| Bucket | Weight | Definition |
|---|---|---|
| catalyst_strength | 30 | gate type × magnitude (e.g., double-beat with raised guide = 30; single soft beat = 12) |
| price_volume_confirmation | 20 | close vs. 20d high, volume vs. 20d avg, distance above 50d MA |
| setup_quality | 15 | clean structure for the labeled setup (breakout: tight base; PED: holding gap; etc.) |
| sector_support | 10 | sector ETF 5d return rank vs. all sectors |
| portfolio_fit | 10 | adds a missing factor (positive) vs. doubles existing exposure (zero) |
| liquidity_quality | 5 | dollar volume ≥ $50M/day = 5; $20–50M = 3; < $20M = 0 (and forces watch_only) |
| volatility_efficiency | 10 | (historical 5d move on similar setups) ÷ ATR%; capped 0–10 |

**Penalties (subtracted after base, separately recorded):**

| Penalty | Range | Trigger |
|---|---|---|
| extension_penalty | 0 to −15 | > 1.5 ATR above 20d MA (linear); −15 if > 2.5 ATR |
| crowding_penalty | 0 to −10 | already top-5 most-mentioned in retail/news sentiment OR up > 25% in last 10d |
| redundancy_penalty | 0 to −10 | overlaps an existing book position by sector AND factor (e.g., another AI semi) |

**Hard rules on top of the math:**
- Any single penalty ≤ −10 ⇒ max class is `conditional_buy`.
- Total penalties ≤ −20 ⇒ max class is `watch_only`.
- `overextended_chase` setup ⇒ `watch_only` or `avoid`, never buy-ready.
- Liquidity bucket = 0 ⇒ `watch_only` regardless of score.

**Recommendation thresholds (after penalties):**

| Final score | Class |
|---|---|
| ≥ 75 AND catalyst gate passed AND no hard rule tripped | `buy_ready` |
| 60–74 OR (≥75 with one hard rule tripped) | `conditional_buy` |
| 40–59 | `watch_only` |
| < 40 OR setup = `overextended_chase` without catalyst | `avoid` |

---

## 5. Macro regime overlay (kill switch, applied last)

Computed once per run from SPY + VIX:

| Regime | Definition | Action on candidates |
|---|---|---|
| `risk_on` | SPY > 20d MA > 50d MA, VIX < 18 | no change |
| `neutral` | mixed | no change |
| `caution` | SPY < 20d MA, VIX 18–25 | downgrade `buy_ready` → `conditional_buy` for breakout_continuation and sympathy_momentum setups |
| `risk_off` | SPY < 50d MA OR VIX > 25 | downgrade ALL `buy_ready` → `conditional_buy`; mean_reversion_bounce setups → `watch_only` |
| `crisis` | SPY −5% in 5d OR VIX > 35 | all candidates → `watch_only`; review existing book stops |

Regime label is logged on every Candidates row.

---

## 6. Position sizing & exits (aggressive but bounded)

These are advisory outputs written next to each `buy_ready` / `conditional_buy`. No automated execution.

- **Per-name risk:** 1.5% of account NAV for `conditional_buy`, 2.0% for `buy_ready`.
- **Position size:** `position_$ = risk_$ / (1.5 × ATR$)`.
- **Initial stop:** 1.5 × ATR below entry reference price.
- **Time stop:** 10 trading days; if not at +1 ATR favorable by then, exit.
- **Trail rule:** after +1 ATR favorable, move stop to breakeven; after +2 ATR, trail by 1 ATR.
- **Concentration caps (aggressive book):**
  - Single name ≤ 15% of NAV
  - Single sector ≤ 35% of NAV
  - Single factor (e.g., AI-beta, levered growth) ≤ 50% of NAV
- **Implied-move tag (when Schwab options pulled, Phase III):** if IV-implied weekly move > 2× the setup's historical 5d move with all-positive factors, tag `optionality_cheap`.

---

## 7. Self-discipline rule (anti-Druck-drift)

Every `buy_ready` row must include a `falsifier_by_wednesday` field:
> "What price/event by Wednesday close would prove this thesis wrong?"

This line is graded in the weekly outcome review. Druck's hit rate on falsifiers is tracked alongside the trade outcomes — this measures *reasoning quality*, not just luck.

---

## 8. Sheet structure (system of record)

Spreadsheet: `19LPX1xGCme4umn22GN4Z7WBQxGZBWWcysDjM6JEW-D4`

### Tab 1 — `Holdings`
`ticker | account | shares | cost_basis | market_value | sector | factor | thesis | horizon | risk_notes | updated_at`

### Tab 2 — `Watchlist`
`ticker | setup_type | catalyst | trigger | invalidator | horizon | confidence | next_check | account_preference | notes`

### Tab 3 — `Candidates`
`date | ticker | regime | setup_state | verified_catalyst_type | catalyst_pass | raw_eps_actual | raw_eps_estimate | raw_revenue_actual | raw_revenue_estimate | raw_5d_price_change_pct | raw_volume_ratio | atr_pct | dollar_volume_m | catalyst_score | price_score | setup_quality_score | sector_score | portfolio_fit_score | liquidity_score | volatility_efficiency_score | extension_penalty | crowding_penalty | redundancy_penalty | total_score_pre_penalty | total_score_final | recommendation_class | suggested_risk_pct | suggested_stop | trigger | invalidator | falsifier_by_wednesday | next_check | notes`

### Tab 4 — `Outcomes`
`date_added | ticker | regime | setup_state | recommendation_class | entry_reference_price | 1d_return | 5d_return | 10d_return | max_runup | max_drawdown | falsifier_resolved | outcome_label | postmortem`

Idempotency key: `(date, ticker)` for Candidates; `(date_added, ticker)` for Outcomes.

---

## 9. Workflow cadence

| When | Who | Action | Output |
|---|---|---|---|
| Sat 10:00 ET | Druck | pull Finnhub earnings + news for last 10d; pull Massive aggregates for resulting universe | raw JSON dropped in Drive folder under `raw/YYYY-MM-DD/` |
| Sat 11:00 ET | Druck | apply catalyst gate; classify setups; write Candidates rows with `recommendation_class = pending` | Candidates tab populated |
| Sun 19:00 ET | Druck | finalize scores + penalties; compute regime overlay; assign `recommendation_class`; write top-5 to Notes doc | Notes doc weekly section |
| Mon 08:30 ET | Druck | re-check regime, refresh quotes, post ranked top-5 to Druck Trading Desk Telegram group | Telegram message |
| Fri 16:30 ET | Druck | fill 1d/5d/10d returns + max_runup/max_drawdown into Outcomes for past entries; mark `falsifier_resolved` | Outcomes tab updated |
| Monthly | Druck | hit-rate postmortem by `recommendation_class` and `setup_state`; calibration check on falsifiers | Notes doc monthly section |

---

## 10. Software/tooling deliverables (build order)

1. **Sheet restructure** — 4 tabs with exact headers above. *(Phase II.0)*
2. **Holdings migration** — move existing combined holdings into new Holdings tab with `sector` + `factor` columns added. *(Phase II.0)*
3. **Druck prompt update** — reference this plan; bake gate + setup labels + recommendation classes into system instructions. *(Phase II.0)*
4. **`druck-research` skill** — playbook describing the Sat/Sun/Mon/Fri workflow and the gate/scoring rules in machine-checkable form. *(Phase II.1)*
5. **Catalyst pull script** — Finnhub earnings + news → raw JSON in Drive. *(Phase II.1)*
6. **Price/volume normalize script** — Massive aggregates → ATR%, dollar volume, 5d change, volume ratio. *(Phase II.2)*
7. **Scoring engine** — deterministic function from raw row → scored row, with separate penalty fields. *(Phase II.2)*
8. **Regime check** — SPY/VIX one-shot pull → regime label. *(Phase II.2)*
9. **Outcome filler** — Friday cron that pulls Massive returns for tickers in Candidates from past 10 days and writes Outcomes. *(Phase II.3)*
10. **Backtest replay** — given a Candidates snapshot date, recompute scores and compare to Outcomes. *(Phase II.4)*

Phase III (deferred): options/IV layer, Reddit/Twitter corroboration, monthly auto-postmortem narrative.

---

## 11. Non-goals

- No live broker order placement.
- No real-time tick streaming.
- No ML for its own sake. (A linear scoring model with disciplined gates beats a black box you don't trust.)
- No Reddit/Twitter in Phase II.
- No "improvements" that break determinism or traceability.

## 12. Paper execution override

As of 2026-05-09, Aaron approved autonomous order placement in the Alpaca paper account only.

This means:
- Schwab remains reference-only for real holdings and exposure context
- Alpaca paper may be used for autonomous mirror-sync, entries, trims, exits, and rebalances
- all discretionary paper orders still require Phase II discipline, logging, and postmortem review
- nothing in this override authorizes live Schwab execution

Operational details live in `AUTONOMOUS_PAPER_TRADING_POLICY.md` and `MONDAY_OPEN_RUNBOOK.md`.
