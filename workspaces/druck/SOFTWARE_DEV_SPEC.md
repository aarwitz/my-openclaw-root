# Software Dev Spec — Druck Phase II Research Pipeline

Date: 2026-05-10
Owner: Aaron
Consumer: Software development
Operator: Druck

## Goal
Build a lightweight, deterministic Phase II research pipeline that lets Druck use Finnhub, Massive, FMP, Alpaca, Schwab, and Google Sheets/Docs with less ad hoc stitching and less reasoning drift.

## Core objective
Given a ticker universe and a run timestamp, produce normalized candidate records and a deterministic Phase II score package suitable for:
- Saturday research pulls
- Sunday final scoring
- Monday pre-open and open checks
- Friday outcome review

## Source authority model
- Finnhub = catalyst truth
- Massive = price-structure truth
- Alpaca = live execution-context truth near the open
- FMP = analyst-sentiment support only
- Schwab = real-book holdings/exposure truth
- Google Sheets = system of record

## Required modules

### 1. Source adapters
Implement adapters for:
- Finnhub
- Massive
- FMP
- Alpaca
- Schwab
- Google Sheets

Each adapter should:
- accept ticker/date params
- return normalized JSON
- label missing/unavailable fields explicitly
- label source and fetch timestamp
- surface rate-limit/auth errors cleanly

### 2. Canonical normalized schema
Produce one record per `(date, ticker)` including:

#### Identity
- date
- ticker
- fetch_timestamp

#### Catalyst fields
- verified_catalyst_type
- catalyst_pass
- catalyst_source
- earnings_date
- eps_actual
- eps_estimate
- revenue_actual
- revenue_estimate
- guidance_raise_flag
- major_event_flag
- analyst_revision_cluster_flag
- sector_sympathy_flag
- catalyst_notes

#### Price / structure fields
- prev_close
- last_close
- five_day_change_pct
- twenty_day_high
- fifty_day_ma
- atr_abs
- atr_pct
- volume_ratio
- dollar_volume_m
- extension_vs_20d_ma_atr
- realized_vol_5d

#### Setup fields
- setup_state
- setup_state_reason
- breakout_level
- gap_hold_flag
- reclaim_flag
- overextended_flag

#### Analyst sentiment fields
- fmp_consensus
- fmp_buy_count
- fmp_hold_count
- fmp_sell_count
- fmp_target_consensus
- fmp_target_high
- fmp_target_low
- fmp_target_activity_last_month
- fmp_data_available

#### Portfolio fit fields
- overlaps_existing_sector
- overlaps_existing_factor
- portfolio_fit_bucket
- existing_related_positions

#### Live execution fields
- alpaca_bid
- alpaca_ask
- alpaca_last
- alpaca_quote_ts
- alpaca_spread_bps
- alpaca_live_conflict_flag
- alpaca_live_context_note

#### Regime fields
- spy_close
- spy_20d_ma
- spy_50d_ma
- vix_close
- regime

#### Scoring fields
- catalyst_score
- price_score
- setup_quality_score
- sector_score
- portfolio_fit_score
- liquidity_score
- volatility_efficiency_score
- extension_penalty
- crowding_penalty
- redundancy_penalty
- total_score_pre_penalty
- total_score_final
- recommendation_class

#### Action fields
- suggested_risk_pct
- suggested_stop
- trigger
- invalidator
- falsifier_by_wednesday
- next_check
- notes

### 3. Deterministic scoring engine
Implement exact logic from `PHASE_II_PLAN.md`.

Requirements:
- fail closed on missing required fields
- preserve penalties separately from base score
- enforce hard caps on class
- enforce macro regime overlay last
- output explanation fields that match the score exactly

### 4. Candidate generation
Build candidate-generation logic from:
- verified Finnhub catalysts
- strong recent movers from Massive
- optional watchlist seed names

Requirements:
- unsupported narrative names must not enter top-ranked pool by default
- every candidate must be traceable back to raw evidence

### 5. Monday-open orchestration
Build a repeatable workflow that:
- refreshes Schwab reference positions
- refreshes Alpaca paper account/orders/positions
- checks mirror-sync state
- computes regime
- refreshes top candidate records
- surfaces recommended actions
- optionally emits order-intent objects for approved Alpaca paper execution
- writes logs and verifies writes

### 6. Persistence and verification
All writes to Sheets/Docs must be:
- idempotent
- read back and verified
- keyed by `(date, ticker)` for Candidates
- keyed by `(date_added, ticker)` for Outcomes

### 7. Logging and replay
Store enough raw and derived fields to replay prior decisions.

Minimum replay targets:
- 1d return
- 5d return
- 10d return
- max_runup
- max_drawdown
- falsifier_resolved

## Non-goals
- live Schwab execution
- options execution
- ML-based black-box ranking
- real-time tick streaming

## Preferred implementation style
- simple, testable modules
- explicit schemas
- low hidden state
- idempotent mutations
- straightforward CLI or script entrypoints

## Ideal deliverables
1. normalization module
2. scoring module
3. candidate-generation module
4. Monday-open runner
5. replay/outcomes updater
6. brief README with example commands

## Success criteria
- same inputs produce same outputs
- no candidate reaches `buy_ready` without verified catalyst pass
- logs are sufficient to audit decisions later
- Monday workflow is fast and repeatable
