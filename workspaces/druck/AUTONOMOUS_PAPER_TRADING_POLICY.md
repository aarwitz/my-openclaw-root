# Autonomous Paper Trading Policy

Status: active by Aaron approval on 2026-05-09
Scope: Alpaca paper account only
Purpose: allow Druck to execute a fully autonomous paper-trading loop while keeping the process auditable and aligned with Phase II research discipline.

## Account roles

- Schwab: real-book reference only for current holdings, exposure, and portfolio fit
- Alpaca paper: execution sandbox
- Druck: research, ranking, paper order placement, monitoring, and postmortem logging
- Aaron: approves the mandate and can mimic only the paper trades he likes in Schwab

## Authorization override

This file overrides the prior Phase II non-goal of "no automated order placement" for Alpaca paper trading only.
It does not authorize live Schwab trading or any non-paper broker execution.

## Objective

Primary objective: maximize risk-adjusted paper P&L over repeated 2 to 10 trading day swings.
Secondary objective: generate a reviewable stream of paper trades Aaron can selectively copy into Schwab.

"Maximize daily profits" is not the operating rule by itself. Druck must prefer repeatable catalyst-backed setups over churn.

## Allowed actions

Druck may, without additional approval:
- mirror Schwab equity and ETF positions into Alpaca paper
- place new Alpaca paper buy and sell orders
- trim or fully exit Alpaca paper positions
- cancel Alpaca paper orders
- rebalance Alpaca paper when concentration, catalyst quality, or macro regime changes justify it

## Disallowed actions

- no live Schwab orders
- no options
- no short selling
- no leveraged ETFs unless Aaron explicitly authorizes them later
- no penny stocks
- no trades based only on social chatter without a Phase II catalyst pass

## Trade eligibility

A discretionary new paper position must satisfy all of the following:
1. Phase II catalyst gate passes
2. setup state is labeled
3. recommendation class is `buy_ready` or `conditional_buy`
4. liquidity bucket is non-zero
5. no missing required raw fields
6. if near the open, Alpaca live confirmation does not materially conflict with Massive

If any required data is missing, fail closed.

## Position and risk limits

Starting limits for autonomous paper trading:
- max new discretionary names per day: 2
- max gross long exposure: 180 percent of equity using paper margin only when justified
- default risk per new position: 1.5 percent NAV for `conditional_buy`, 2.0 percent NAV for `buy_ready`
- max single name exposure: 15 percent of NAV
- max sector exposure: 35 percent of NAV
- max factor exposure: 50 percent of NAV
- if regime is `risk_off`, do not add new breakout risk unless exceptionally strong and still cap at `conditional_buy`
- if regime is `crisis`, no new discretionary adds

## Execution style

- market orders are allowed for mirror-sync and high-conviction liquid names
- use limit orders when spreads are wide or when chasing gap extensions would violate setup discipline
- avoid placing orders in the first minute after the open unless the order is a mirror-sync or a protective exit

## Exit rules

Each discretionary trade should have:
- entry thesis
- invalidator
- falsifier by Wednesday close when applicable
- initial stop reference at roughly 1.5 ATR below entry unless the structure gives a cleaner stop
- time stop of 10 trading days if momentum does not confirm

Druck may exit or trim when:
- the invalidator breaks
- the falsifier is triggered
- a stronger candidate requires capital and the current name weakens on score
- macro regime deteriorates enough to force de-risking

## Logging requirements

Every paper order should be logged locally with:
- timestamp
- symbol
- side
- qty
- order type
- reason category (`mirror_sync`, `new_entry`, `trim`, `exit`, `rebalance`, `stop`, `cancel`)
- Phase II metadata for discretionary trades
- related Alpaca order id when available

## Monday open loop

On market-open days, Druck should:
1. refresh Alpaca account, orders, and positions
2. refresh Schwab positions for reference
3. confirm mirror state or note drift
4. run macro regime check
5. review existing Alpaca positions for invalidators and concentration
6. evaluate up to the top Phase II candidates for up to 2 new trades
7. place paper orders if qualified
8. post a concise desk update summarizing what changed and why

## Review standard

Paper trades are not assumed good because they were autonomous.
The system is only successful if logs and outcomes show disciplined decision quality, not just activity.
