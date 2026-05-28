# Autonomous PM Operating Model

Status: active operating spec for Druck's autonomous Alpaca paper portfolio manager
Owner: Aaron
Operator: Druck
Execution venue: Alpaca paper
Primary benchmark: SPY
Secondary hurdle: cash / short-duration Treasury equivalent

This document is the authoritative operating and quantitative strategy spec.
It supersedes `PHASE_II_PLAN.md`, which has been retired.

## 1. Purpose

Druck is no longer a Monday-picks reporter.

Druck is an autonomous benchmark-relative portfolio manager whose job is to:
- beat `SPY` on rolling 1-week, 1-month, and 1-quarter windows
- hold cash when expected active return is weak
- maintain explicit long-term, medium-term, and short-term theses
- execute, rotate, trim, and exit positions autonomously on Alpaca paper
- explain every meaningful position and every material change in state
- self-audit whether realized results matched expected edge

This spec supersedes any workflow that treats Telegram text or Google Sheets as the authority for what should be traded.

## 2. Core operating principles

1. `SPY` is the primary benchmark.
2. Cash is a real competitor to every trade.
3. Every position must map to a thesis, an expression, and an execution intent.
4. Orders are created from deterministic state transitions, never directly from chat output.
5. Reporting failures do not block valid trading decisions, but reconciliation failures can pause new risk.
6. Telegram is the control tower and reporting surface, not the trading brain.
7. Long-term, medium-term, and short-term sleeves must use different turnover and invalidation logic.
8. Event-driven actions between checkpoints are allowed when stops, invalidators, or major catalysts trigger.

## 3. Benchmark model

Primary objective:
- beat `SPY` on rolling 1-week, 1-month, and 1-quarter horizons

Secondary objective:
- exceed the current cash hurdle before opening or maintaining marginal-risk positions

Guardrails:
- avoid concentration blowups
- avoid low-liquidity names unless explicitly allowed by strategy
- avoid unbounded drift between thesis horizon and actual holding behavior

Interpretation rules:
- `score` means expected attractiveness relative to the active objective
- `replacement_delta` means expected improvement in active return versus replacing an existing holding
- a candidate that does not clearly beat cash should not be held just because it is interesting

## 4. Portfolio sleeve model

The portfolio is divided conceptually into three sleeves.

### 4.1 Long-term sleeve

Purpose:
- express structural beliefs with multi-month durability

Typical horizon:
- 3 to 12 months

Examples:
- AI proliferation
- compute, memory, networking, power, cooling, and grid build-out
- cybersecurity as a durable spend category

Behavior:
- thesis changes rarely
- ticker expressions may change if a better expression emerges
- may hold through short-term noise if the medium-term and short-term damage does not break the structural case

### 4.2 Medium-term sleeve

Purpose:
- express 2 to 8 week leadership, rotation, and theme pressure

Typical horizon:
- 2 to 8 weeks

Examples:
- semis leadership broadening
- memory pricing upcycle
- cyber post-earnings drift cluster
- utilities / power beneficiaries

Behavior:
- refreshed daily before market open
- can rotate across expressions within the same thesis family
- should not survive repeated short-term invalidation without re-underwriting

### 4.3 Short-term sleeve

Purpose:
- express tactical 1 to 5 trading day opportunities

Typical horizon:
- 1 to 5 trading days

Examples:
- breakout continuation
- post-earnings drift
- sympathy momentum
- mean-reversion bounce

Behavior:
- updated at every checkpoint
- aggressive about changing its mind
- should not quietly become a medium-term or long-term hold without explicit promotion

## 5. Thesis schema

Every thesis record must include:
- `thesis_id`
- `horizon`: `long_term | medium_term | short_term`
- `claim`: what should happen
- `why_now`: why this matters now
- `benchmark_to_beat`: usually `SPY`
- `cash_hurdle_required`: yes/no
- `preferred_expression_types`: stock, ETF, basket, or rotation candidates
- `best_current_expressions`
- `disconfirming_evidence`
- `expiry_rule`
- `last_updated_at`
- `next_forced_review_at`

Every position must include:
- `position_id`
- `linked_thesis_id`
- `sleeve`
- `ticker`
- `entry_reason`
- `trigger`
- `invalidator`
- `stop_logic`
- `expected_holding_horizon`
- `why_this_beats_spy`
- `why_this_beats_cash`

## 6. Expression-selection rules

Thesis and ticker are not the same thing.

Examples:
- thesis: AI power demand will tighten the power/cooling value chain
- expression set: networking, memory, utilities, power equipment, cooling, grid infrastructure
- selected trade: whichever current expression has the best benchmark-relative setup and liquidity profile

Rules:
1. A thesis may remain valid while the preferred ticker changes.
2. Expression changes are expected more often than thesis changes.
3. A weak expression must not be defended just because the underlying thesis still sounds persuasive.

## 7. Checkpoint cadence

Trading-day checkpoints:
- `09:00 ET` pre-market thesis and intent refresh
- `11:00 ET` morning confirmation / invalidation check
- `13:30 ET` re-ranking and replacement review
- `15:30 ET` overnight hold and close-risk review

Between checkpoints:
- immediate action is allowed for stop hits, invalidators, exceptional catalyst changes, or broker/risk events

## 8. Required work at each checkpoint

### 8.1 09:00 ET

Must do:
- refresh macro regime
- refresh long-term, medium-term, and short-term thesis stack
- refresh candidate ranking
- review all open positions versus benchmark objective
- generate or update `trade_intents`
- decide entries, exits, adds, trims, and rotations
- publish concise checkpoint summary if there is material change

### 8.2 11:00 ET

Must do:
- check whether the pre-market thesis is confirming or failing
- inspect fills, spreads, and early price structure
- manage short-term trades aggressively
- cut failed morning ideas rather than rationalizing them

### 8.3 13:30 ET

Must do:
- re-rank holdings versus available replacements
- compute `replacement_delta` for likely rotations
- ask which current holding is hurting active return versus `SPY`
- rotate if expected improvement is meaningful and trading costs are acceptable

### 8.4 15:30 ET

Must do:
- decide which positions deserve overnight risk
- remove weak short-term expressions that no longer beat cash or `SPY`
- preserve long-term and medium-term positions only when their theses still survive
- produce concise closing plan for the next session

## 9. Event-driven actions between checkpoints

The system must not wait for the next checkpoint if any of the following occur:
- stop triggered
- invalidator triggered
- market-wide regime shock
- major thesis-breaking news
- broker rejection or reconciliation event that changes exposure truth

Rules:
- protective exits are always allowed
- new adds may be paused during integrity failures
- every event-driven action must be logged with cause and timestamp

## 10. Autonomous execution authority

For Alpaca paper, Druck may autonomously:
- open new positions
- add to positions
- trim positions
- fully exit positions
- rotate from one expression to another
- cancel stale orders
- restage orders with better execution logic
- hold cash when opportunities are weak

Execution is permitted only from deterministic `trade_intents`.

Chat output may:
- request status
- request explanation
- request a manual run
- change top-level policy

Chat output may not:
- directly create broker orders without the intent being written to canonical state

## 11. Sizing and concentration policy

For now, autonomy on sizing and concentration is broad, but the system must still log and measure risk.

Hard requirements:
- log intended risk per trade
- log resulting concentration after every fill
- maintain sector and factor concentration stats
- flag extreme concentration explicitly in every checkpoint summary

Recommended initial policy defaults:
- target exposure may vary by sleeve
- long-term sleeve can hold more concentration than short-term sleeve
- short-term sleeve should be most aggressive about trimming when benchmark-relative edge degrades

Important:
- "max autonomy" does not mean "no measurement"
- even if concentration is allowed, the PM must know when he is making a concentration bet

## 12. Self-audit rules

Every meaningful action should later be reviewable against:
- the thesis that justified it
- the benchmark-relative objective at the time
- the cash alternative
- the stated invalidator and falsifier
- the realized outcome versus expected edge

Minimum audit fields per trade or material hold decision:
- timestamp
- sleeve
- ticker
- action
- thesis id
- trigger
- invalidator
- stop logic
- why this beats `SPY`
- why this beats cash
- expected holding horizon
- realized outcome snapshot at later review

## 13. Quantitative candidate rules

These are the retained deterministic Phase II rules that still govern candidate
ranking and execution readiness inside the autonomous PM loop.

### 13.1 Source hierarchy

1. Finnhub or primary published-news verification decides catalyst truth.
2. Massive decides historical price-structure truth.
3. Alpaca decides live execution-context truth near the open and intraday.
4. FMP is secondary analyst-sentiment support only.

Fail-closed rules:
- missing primary catalyst data cannot be treated as neutral
- sentiment cannot override a failed catalyst gate
- tape strength cannot substitute for event verification
- if Alpaca and Massive materially disagree intraday, cap aggressiveness and log `data_discrepancy`

### 13.2 Catalyst gate

No ticker may reach `buy_ready` or `conditional_buy` without at least one
verified catalyst:
- `earnings_double_beat`
- `guidance_raise`
- `major_corporate_event`
- `analyst_revision_cluster`
- `sector_sympathy_confirmed`

If no catalyst gate passes:
- max class is `watch_only`

### 13.3 Setup-state classification

Every candidate must be labeled exactly one:
- `breakout_continuation`
- `post_earnings_drift`
- `sell_the_news_digestion`
- `sympathy_momentum`
- `mean_reversion_bounce`
- `overextended_chase`

Hard rule:
- `overextended_chase` can never be `buy_ready`

### 13.4 Scoring buckets

Base score remains a deterministic 0-100 weighted sum:
- catalyst strength: 30
- price/volume confirmation: 20
- setup quality: 15
- sector support: 10
- portfolio fit: 10
- liquidity quality: 5
- volatility efficiency: 10

Penalties are stored separately and applied after base scoring:
- extension penalty: 0 to -15
- crowding penalty: 0 to -10
- redundancy penalty: 0 to -10

Hard caps:
- any single penalty <= -10 caps class at `conditional_buy`
- total penalties <= -20 caps class at `watch_only`
- liquidity quality = 0 caps class at `watch_only`

### 13.5 Recommendation classes

After penalties and hard rules:
- `buy_ready`
- `conditional_buy`
- `watch_only`
- `avoid`

Interpretation:
- `buy_ready` means eligible for autonomous entry if it also survives live execution checks and portfolio replacement logic
- `conditional_buy` means the idea is directionally valid but not clean enough for aggressive expression
- `watch_only` means monitor but do not allocate new risk
- `avoid` means the setup is actively unattractive

### 13.6 Macro regime overlay

The macro regime is applied last:
- `risk_on`
- `neutral`
- `caution`
- `risk_off`
- `crisis`

Mandatory downgrades:
- `risk_off` downgrades all `buy_ready` names to `conditional_buy`
- `crisis` forces all new candidates to `watch_only`

### 13.7 Risk sizing defaults

Default risk sizing remains volatility-targeted:
- `conditional_buy`: 1.5% NAV risk
- `buy_ready`: 2.0% NAV risk
- position size = `risk_$ / (1.5 x ATR$)`
- initial stop = `1.5 x ATR` below entry reference

Concentration monitoring remains mandatory:
- single name: 15% of NAV guideline
- sector: 35% guideline
- factor: 50% guideline

These are measurement guardrails, not permission to ignore concentration.

### 13.8 Falsifier discipline

Every `buy_ready` candidate must include a one-line falsifier:
- what price or event by Wednesday close would prove the thesis wrong

This is required for self-grading and postmortem review.

Every checkpoint must answer:
- what currently beats `SPY`?
- what currently beats cash?
- what is hurting active return?
- what thesis changed?
- what must be rotated, trimmed, added, or closed?

Every trade must log:
- expected alpha versus `SPY`
- expected edge versus cash
- sleeve
- thesis id
- trigger
- invalidator
- stop
- realized result
- whether the thesis was confirmed or broken

Daily after close:
- compute portfolio return and active return versus `SPY`
- attribute contribution by position
- identify best and worst active-return contributors
- grade whether today’s actions improved expected portfolio quality

Weekly:
- recalibrate thesis quality, sleeve contribution, and benchmark-relative hit rate

## 13. Telegram and OpenClaw surface rules

Telegram DM with Druck:
- primary operator surface for questions, manual triggers, explanations, and policy changes

Trading Desk topic:
- shared updates only
- fills, risk breaches, material thesis changes, daily checkpoint summaries, and audit notes

Task Manager:
- engineering backlog, infra failures, strategy enhancements, and execution-system bugs

OpenClaw cron:
- should call deterministic entrypoints
- should be market-calendar aware
- should emit only material deltas, not repetitive fail-closed spam

Subagents:
- useful for bounded side investigations, not for urgent execution-path decisions

## 14. Canonical state requirement

Canonical state must live in SQLite, not Sheets.

Minimum entities:
- theses
- ideas
- trade_intents
- trades
- positions
- checkpoint_runs
- order_events
- portfolio_snapshots
- self_audits

Sheets and Docs remain review/reporting surfaces only.

## 15. Replacement for the old pre-market cron

The old `DRUCK_PRE_MARKET` cron is a legacy workflow and should remain disabled.

Replacement model:
- a checkpoint-driven trading-day manager
- deterministic state refresh at 09:00 / 11:00 / 13:30 / 15:30 ET
- event-driven execution between checkpoints
- concise Telegram output only when state changed materially

## 16. Initial implementation priorities

1. Disable the legacy sheet-first pre-market cron.
2. Add thesis and sleeve concepts to canonical state.
3. Build checkpoint-run records and summary outputs.
4. Build portfolio attribution and replacement-delta logic.
5. Route Alpaca orders through `trade_intents`.
6. Add self-audit and after-close benchmark attribution.
7. Reintroduce trading-day cron only after deterministic entrypoints exist.
