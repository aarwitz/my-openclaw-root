# Cost Tracker Spec

Status: V1 required infrastructure
Scope: all external API calls

## 1. Purpose

Every external API call must pass through a cost-tracking wrapper so the system can:
- estimate monthly burn
- identify waste
- throttle non-essential sources when over budget
- attribute spend to strategies, tickers, and workflows

This is required before broad signal-source expansion.

## 2. Covered vendors

Initial expected vendors:
- Finnhub
- Massive / Polygon
- FMP
- Event Registry
- Brave search / fetch
- Alpaca market/account endpoints
- Schwab read endpoints
- SEC EDGAR if any paid proxy is later introduced
- FRED if rate or quota tracking becomes useful
- any transcript or speech-to-text service if later added

Free endpoints should still be logged with zero estimated cost where useful, because volume matters even when direct cost is zero.

## 3. Wrapper contract

All external calls should go through a common interface conceptually like:

`tracked_call(vendor, endpoint, units, purpose, strategy_id=None, ticker=None, run_id=None, metadata=None)`

The wrapper should:
1. record start time
2. execute the call
3. record success/failure
4. estimate cost from configured pricing rules
5. persist a `cost_ledger` row
6. update rolling burn estimates
7. trigger throttling logic if thresholds are exceeded

## 4. Required logged fields

Each cost event should include:
- timestamp
- vendor
- endpoint
- units
- estimated_cost
- hard_cost if available
- call_purpose
- strategy_id nullable
- ticker nullable
- run_id nullable
- status (`success`, `error`, `throttled`, `skipped`)
- latency_ms
- metadata_json

## 5. Pricing model

Maintain pricing in config, not code.

Recommended config shape:
- vendor
- endpoint pattern
- unit type (`request`, `article`, `symbol`, `bar`, `minute`, `transcript_minute`)
- estimated_cost_per_unit
- monthly_budget_bucket optional
- essentiality (`critical`, `important`, `nonessential`)

### Notes
- some services will be flat-fee subscriptions, not marginal-cost APIs
- still assign estimated internal per-call costs so relative burn is visible
- if only monthly subscription is known, distribute a notional per-unit cost based on expected usage envelope

## 6. Daily spend digest

At 8am local time, send a Telegram digest covering yesterday:
- total estimated spend
- total by vendor
- highest-volume endpoints
- any throttling that occurred
- projected monthly burn
- recommendation if burn trend exceeds budget

## 7. Budget and throttle rules

Default hard budget:
- projected monthly burn > $50 triggers throttling review

### Throttle order
Throttle highest-volume non-essential sources first:
1. broad background scans
2. shadow-strategy enrichment
3. transcript retries and deep enrichment
4. duplicate search augmentation
5. low-priority discovery jobs

Do not throttle:
- broker reconciliation
- position management and stop checks
- required risk snapshots
- active trade management
- user-triggered paste-in investigations

## 8. Projected monthly burn

Projected monthly burn should use rolling recent usage, for example:
- 7-day trailing average spend * days in month
- optionally compare with 30-day trailing estimate once enough data exists

Expose:
- current month actual
- projected month total
- projected budget utilization percent

## 9. Required reports

### By vendor
- spend
- call volume
- success/failure counts
- average latency

### By strategy
- enrichment spend
- execution-support spend
- spend per trade
- spend per candidate
- spend per profitable trade

### By workflow
- manual paste-in
- filings ingestion
- analyst enrichment
- market scans
- transcript enrichment
- reconciliation

## 10. Failure and fallback behavior

If a call is skipped due to throttle:
- log status `throttled`
- store the reason
- if the skipped call affects an active strategy, note degraded evidence quality

If pricing config is missing:
- log zero estimated cost temporarily
- raise a config warning
- do not silently bypass logging

## 11. Tests required

1. successful call logs a ledger row
2. failed call still logs a ledger row
3. projected monthly burn updates correctly
4. non-essential source throttles when budget exceeded
5. critical broker/risk calls are never throttled
6. daily digest totals match ledger aggregation

## 12. Immediate next artifact

Create `config/cost_rates.yaml` with draft pricing and essentiality tags for every vendor currently in use.
