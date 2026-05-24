# Execution State Machine

Status: V1 operating spec
Scope: Alpaca paper only
Canonical state store: SQLite

## 1. Purpose

This document defines the lifecycle states and allowed transitions for:
- signals
- ideas
- trade intents
- trades
- positions
- system pauses

The purpose is to keep execution deterministic, auditable, and recoverable after errors, partial fills, stale signals, or broker inconsistencies.

## 2. Core principles

1. No trade is placed directly from a raw signal.
2. Every order must map back to a `trade_intent`.
3. Every filled trade must map back to an `idea`, `strategy_id`, and evidence trail.
4. State transitions are append-loggable and replayable.
5. Reconciliation failures can pause new risk but should not suppress protective exits.

## 3. Signal states

States:
- `new`
- `enriched`
- `promoted`
- `expired`
- `discarded`

### Transition rules
- `new -> enriched`
  - minimum extraction and normalization completed
- `enriched -> promoted`
  - enough evidence exists to create or update an `idea`
- `enriched -> expired`
  - freshness window elapsed before promotion
- `enriched -> discarded`
  - malformed, duplicate, low-quality, or invalid signal

### Notes
- all manual paste-ins create a signal first
- all signals should be retained for later candidate-decision learning

## 4. Idea states

States:
- `open`
- `watch`
- `staged`
- `traded`
- `expired`
- `rejected`

### Transition rules
- `open -> watch`
  - evidence exists but corroboration or timing is insufficient
- `open -> staged`
  - strategy rules pass and a `trade_intent` is created
- `open -> rejected`
  - fails deterministic filters or hard risk gates
- `watch -> staged`
  - later corroboration or price setup activates the idea
- `watch -> expired`
  - watch window elapsed with no activation
- `staged -> traded`
  - at least one trade fills from an intent tied to the idea
- `staged -> rejected`
  - intent blocked or canceled for substantive reasons

### Required fields before staging
- strategy_id
- direction
- aggregate_score or equivalent scoring record
- corroboration_count
- risk flags snapshot
- cash hurdle decision

## 5. Trade intent states

States:
- `pending`
- `blocked`
- `ready`
- `submitted`
- `filled`
- `canceled`

### Transition rules
- `pending -> blocked`
  - any hard gate fails
- `pending -> ready`
  - rules and risk checks pass
- `ready -> submitted`
  - broker order sent successfully
- `submitted -> filled`
  - first fill opens the position or completes the intended entry
- `submitted -> canceled`
  - order canceled, expired, or rejected without fill
- `blocked -> pending`
  - only if blocking condition was transient and a fresh evaluation reruns

### Hard gates before `ready`
- signal freshness valid
- required data present
- reconciliation clean or non-blocking
- risk snapshot fresh
- cash hurdle pass
- concentration limits pass
- system pause does not block this order type

## 6. Trade lifecycle states

States:
- `submitted`
- `partially_filled`
- `filled`
- `managing`
- `exited`
- `reconciled`
- `evaluated`
- `archived`

### Transition rules
- `submitted -> partially_filled`
  - broker confirms partial fill
- `submitted -> filled`
  - broker confirms full fill directly
- `partially_filled -> filled`
  - full intended entry achieved
- `partially_filled -> managing`
  - position exists and must be managed even if target size not fully reached
- `filled -> managing`
  - normal state after entry complete
- `managing -> exited`
  - full position closed
- `exited -> reconciled`
  - broker, ledger, and position tables agree
- `reconciled -> evaluated`
  - performance and attribution metrics computed
- `evaluated -> archived`
  - no more expected changes except historical reporting

### Trade state notes
- protective exits are always allowed even during most pauses
- a trade may enter `managing` from `partially_filled` if enough shares exist to require stop enforcement
- failed or canceled entries with zero fill should remain as order events, not trades with open risk

## 7. Position states

Suggested values for `health_status`:
- `healthy`
- `at_risk`
- `stop_near`
- `invalidated`
- `time_stop_due`
- `exit_pending`
- `reconcile_review`

### Position management rules
- open positions are reviewed on the 1-minute loop during market hours
- stop and invalidation checks outrank enrichment or new-entry work
- each review should refresh:
  - mark price
  - stop distance
  - thesis health inputs
  - add count
  - next forced review timestamp

## 8. Order event model

Every broker interaction should create an `order_event` row.

Common event types:
- `submitted`
- `accepted`
- `partially_filled`
- `filled`
- `replaced`
- `canceled`
- `rejected`
- `expired`
- `stop_triggered`

Order events are the ground truth for replaying execution behavior.

## 9. Pause-state model

System pause scopes:
- `strategy_only`
- `source_only`
- `new_entries_only`
- `adds_only`
- `shorts_only`
- `full_system`

### Pause behavior
- pauses block only the action classes defined in `blocks_json`
- protective exits remain allowed unless broker integrity itself is compromised
- each pause must have:
  - reason
  - start timestamp
  - scope
  - block list
  - source reference if relevant

## 10. Failure handling

### Reconciliation mismatch
- create `reconciliation_runs` record
- if mismatch affects open-risk interpretation, raise `system_pause`
- continue protective monitoring if broker data is still available

### Broker rejection
- write `order_event`
- if rejection rate exceeds threshold, pause affected strategy or symbol route
- do not automatically resubmit without a fresh evaluation unless error is known-transient

### Stale signal
- stale signals cannot create new `ready` intents
- they may still remain in `candidate_decisions` for forward-return learning

### Data-quality issues
- flagged trades may proceed only if the specific strategy and risk policy permit it
- flagged trades must be excluded from clean attribution cohorts

## 11. Required test cases

1. signal expires before enrichment completes
2. watch idea receives later corroboration and stages correctly
3. blocked intent becomes ready after transient issue clears
4. partial fill enters managing state and stop checks still run
5. reconciliation mismatch pauses new entries but still allows stop exits
6. stale signal blocked from submission
7. order rejection spike triggers strategy pause
8. trade reaches evaluated only after reconciliation and attribution complete

## 12. Implementation note

The state machine should be implemented as explicit transition functions with validation, not as loose status string edits spread across the codebase.
