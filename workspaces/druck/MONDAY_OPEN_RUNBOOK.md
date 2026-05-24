# Monday Open Runbook

Purpose: execute Druck's paper-trading loop on market-open days using Schwab as the reference book and Alpaca paper as the execution venue.

## Pre-open checklist

1. Refresh Schwab token if needed
2. Pull Schwab account positions and balances
3. Pull Alpaca paper account, positions, and open orders
4. Confirm mirror-sync status versus Schwab
5. Pull SPY and VIX context for macro regime
6. Pull live Alpaca clock and verify market state
7. Review any pending orders from prior session

## Opening workflow

### 1. Mirror confirmation
- If mirror orders are still pending from prior close, verify they remain valid
- After fill, compare Alpaca positions to Schwab reference positions
- Log any share drift, rejection, or partial fill

### 2. Existing position review
For each Alpaca position:
- note whether it is `mirror_sync` legacy exposure or `discretionary`
- check concentration versus policy caps
- define or refresh trigger, invalidator, and falsifier if discretionary
- identify any immediate de-risk actions needed

### 3. Regime check
Classify one of:
- risk_on
- neutral
- caution
- risk_off
- crisis

Apply the policy overlay before considering any new adds.

### 4. Candidate generation
Build a short list using Phase II:
- catalyst gate first
- setup label second
- price/volume and liquidity confirmation
- penalties and macro overlay last

Prefer only the top few names that clearly pass. Skip adds if the evidence is thin.

### 5. New discretionary trade limit
Default limit: no more than 2 new discretionary names in one day.

For each candidate actually traded, log:
- catalyst type
- setup state
- recommendation class
- score
- trigger
- invalidator
- falsifier by Wednesday
- intended hold horizon

### 6. Order placement logic
- use market orders for highly liquid names when timing sensitivity matters
- use limit orders when spread or extension risk is meaningful
- avoid first-minute discretionary chasing unless the signal quality is exceptional

### 7. Post-open review
Within the first review window after fills:
- confirm fills and average prices
- update local ledger
- note whether the portfolio is now over-concentrated
- identify next review timestamp

## Desk update format

Use a concise summary:
- mirror status
- any exits or trims
- any new discretionary entries
- top current risks
- next check time

## Current initial state as of 2026-05-09

Mirror orders submitted to Alpaca paper for:
- GOOG 65
- VOO 21
- NVDA 30
- SPY 100
- VOOG 60
- JPM 10

These were accepted after Saturday close and should be checked for fill status at Monday open.
