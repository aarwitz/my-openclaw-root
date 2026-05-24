# Quant Dev Spec — Druck Phase II Calibration and Rule Formalization

Date: 2026-05-10
Owner: Aaron
Consumer: Quant development
Operator: Druck

## Goal
Help formalize and calibrate the rule-based Phase II stock-selection framework so it is deterministic, auditable, and empirically tighter.

## Core need
This is not a request for black-box ML. The system should remain rules-first and human-auditable.

The quant-dev role is to improve:
- setup-state definitions
- volatility normalization
- penalty calibration
- replay / review quality

## Areas of work

### 1. Setup-state classifier formalization
Formalize rule-based detection for:
- breakout_continuation
- post_earnings_drift
- sell_the_news_digestion
- sympathy_momentum
- mean_reversion_bounce
- overextended_chase

Requirements:
- deterministic inputs
- minimal ambiguity
- explainable output
- explicit precedence if multiple setups appear true

Suggested outputs:
- setup_state
- confidence_flag
- reason_code
- supporting raw metrics

### 2. ATR and volatility-efficiency design
Need robust definitions for:
- ATR absolute
- ATR percent
- expected move framing for 2 to 10 day swings
- volatility-efficiency score

Questions to answer:
- best ATR window for this use case
- how to compare follow-through across different vol regimes
- whether to use realized volatility alongside ATR
- how to normalize strong movers without over-rewarding chaos

### 3. Penalty calibration
Calibrate thresholds and slopes for:
- extension_penalty
- crowding_penalty
- redundancy_penalty

Needs:
- simple formulas
- intuitive behavior at extremes
- no hidden interaction terms
- class caps that align with actual risk behavior

Specific questions:
- at what ATR extension does edge deteriorate materially?
- how should recent % move and crowding interact?
- how should sector overlap vs factor overlap be weighted?

### 4. Sector sympathy logic
Formalize `sector_sympathy_confirmed`.

Need a reproducible rule for:
- identifying leading catalyst name
- confirming peer reaction
- deciding when sympathy is genuine vs late chasing

Suggested inputs:
- sector ETF move
- peer basket move
- rolling correlation
- event timing proximity

### 5. Regime overlay validation
Validate the current SPY/VIX overlay:
- risk_on
- neutral
- caution
- risk_off
- crisis

Need to test whether downgrade rules are too soft or too harsh for:
- breakout setups
- post-earnings drift setups
- mean reversion setups

### 6. Replay / backtest framework
Build a simple replay framework that can:
- take stored candidate rows
- recompute class/score from frozen raw inputs
- compare recommendation to realized outcome
- surface which rules add value and which degrade value

Key outputs:
- hit rate by recommendation class
- hit rate by setup_state
- score bucket performance
- falsifier quality tracking
- extension penalty usefulness
- redundancy penalty usefulness

## Data assumptions
Primary source hierarchy remains fixed:
- Finnhub = catalyst truth
- Massive = price-structure truth
- Alpaca = live confirmation near open
- FMP = analyst-sentiment support

Quant work should respect this hierarchy, not blur it.

## Constraints
- no black-box ranking model as primary engine
- no opaque feature interactions
- no strategy complexity that breaks auditability
- fail closed on missing critical fields

## Preferred outputs from quant dev
1. formula recommendations
2. threshold recommendations
3. deterministic rule table updates
4. simple pseudocode or spec notes
5. replay summary on sample historical windows

## Ideal success criteria
- fewer false positives from hot but low-quality names
- better separation between `conditional_buy` and `watch_only`
- cleaner handling of overextended names
- higher confidence that score explanations match actual edge

## Immediate highest-value tasks
1. setup-state classifier formalization
2. extension/crowding/redundancy calibration
3. volatility-efficiency formula refinement
4. replay framework for last several weeks of candidate decisions
