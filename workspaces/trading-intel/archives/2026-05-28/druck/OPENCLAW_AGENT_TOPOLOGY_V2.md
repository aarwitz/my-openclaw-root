# OpenClaw Agent Topology V2

Status: active agent topology spec

## 1. Agents

The system runs four OpenClaw agents:
- `researcher`
- `quant`
- `trader`
- `critic`

## 2. External binding

Only `trader` should be externally bound to Aaron's Telegram DM.

Reason:
- one front door
- one execution owner
- no ambiguity about who speaks for the trading system

## 3. Workspace model

Each agent gets:
- its own workspace
- its own startup docs
- its own background jobs
- its own connector and tool permissions as needed

Shared coordination happens through the canonical SQLite state first.

## 4. Responsibilities

### Researcher
- background ingestion
- hypothesis creation and updates
- evidence curation
- falsifier monitoring inputs

### Quant
- scoring
- regime snapshots
- expression ranking
- sizing recommendations

### Trader
- Telegram interface
- Alpaca account reads
- trade intent creation and execution
- position management

### Critic
- live challenge review
- priced-in checks
- redundancy / crowding challenges
- post-resolution grading

## 5. Job map

### Researcher jobs
- nightly core ingestion
- intraday delta refresh for selected feeds
- hypothesis update run after major source changes

### Quant jobs
- pre-market scoring refresh
- regime refresh
- expression rerank after major hypothesis changes

### Trader jobs
- market-open review
- midday review
- close review
- event-driven order / position reconciliation

### Critic jobs
- review after new high-priority hypotheses
- review before ready trade intents can execute
- postmortem runs after hypothesis resolution

## 6. Routing rule

Default routing:
- Aaron DM -> `trader`

Internal escalation:
- `trader` consults `researcher`, `quant`, and `critic` through shared state and targeted runs

## 7. Authority

Priority:
1. `AUTONOMOUS_PM_OPERATING_MODEL.md`
2. `MULTI_AGENT_TRADING_SYSTEM_V2.md`
3. this document
