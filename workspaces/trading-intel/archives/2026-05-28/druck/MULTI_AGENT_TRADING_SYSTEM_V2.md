# Multi-Agent Trading System V2

Status: active implementation plan
Owner: Aaron
Operator surface: OpenClaw
Execution venue: Alpaca paper
Primary benchmark: SPY
Fallback competitor: cash / short-duration Treasury equivalent

## 1. Purpose

Build a four-agent OpenClaw trading system that:
- learns continuously in the background
- maintains a durable hypothesis library
- converts surviving hypotheses into trade intents
- executes and manages Alpaca paper positions
- measures active return against `SPY` and cash

The design goal is not to predict everything. The design goal is to exploit slow market diffusion from important world changes into liquid public-market expressions.

## 2. Top-level shape

The system has four agents and one shared canonical state layer:
- `researcher`: discovers and updates hypotheses
- `quant`: scores, sizes, and maps expressions
- `trader`: executes and manages positions
- `critic`: challenges theses and grades live decisions
- shared SQLite state: hypotheses, evidence, scores, trade intents, positions, audits

Only `trader` should be bound to Aaron's Telegram DM.

## 3. OpenClaw operating model

Use OpenClaw for orchestration, not for ambiguous agent blending.

- Each agent gets its own workspace, identity, and background jobs.
- `trader` is the front door and owns human interaction plus Alpaca execution.
- Internal collaboration should flow through shared state first, not ad hoc chat.
- Detached work should run through background tasks / task-flow style pipelines.

## 4. Agent responsibilities

### 4.1 Researcher

Runs continuously in the background.

Responsibilities:
- monitor core feeds and hypothesis-triggered deep feeds
- create new hypotheses
- update evidence, leading indicators, falsifiers, and confidence
- maintain a compact thesis library rather than re-researching from scratch each run

Cannot:
- submit trades
- size positions
- override broker/risk policy

### 4.2 Quant

Runs on schedule and on demand after material thesis updates.

Responsibilities:
- score hypotheses
- estimate time-to-price and edge decay
- rank expression vehicles
- calculate starter/add/trim/exit sizing recommendations
- maintain regime state and benchmark-vs-cash rules

Cannot:
- place orders
- invent unsupported evidence

### 4.3 Trader

Runs on schedule, on event triggers, and via Telegram.

Responsibilities:
- read the active hypothesis library and quant outputs
- check live market state and Alpaca account state
- create and execute deterministic trade intents
- manage entries, adds, trims, exits, and rotations
- reconcile orders, fills, and positions

Constraints:
- every trade must reference a valid hypothesis
- every trade must include a falsifier and exit logic
- cash remains a valid alternative

### 4.4 Critic

Runs after new hypotheses, before live trade intents, and after material errors.

Responsibilities:
- attack thesis logic
- challenge whether the thesis is already priced in
- challenge whether the selected expression is actually optimal
- identify hidden risk, crowding, timing mismatch, and portfolio redundancy

Cannot:
- execute trades
- silently veto decisions without writing a challenge record

## 5. Shared canonical state

SQLite remains the canonical state store.

Minimum entities:
- `hypotheses`
- `hypothesis_evidence`
- `expression_candidates`
- `regime_snapshots`
- `trade_intents`
- `orders`
- `positions`
- `position_events`
- `agent_runs`
- `audits`
- `postmortems`

Hard rules:
- every state transition must be timestamped
- every write must record actor and rationale
- every market-facing action must be reproducible from stored state
- preserve point-in-time evidence, not just summaries

## 6. Hot path vs cold path

### 6.1 Hot path

Always-on data used by the trading loop:
- price / volume / market structure
- live Alpaca account, orders, positions
- core news / filings / earnings / analyst revision feeds
- active regime state
- top-ranked active hypotheses

### 6.2 Cold path

Background or hypothesis-triggered research only:
- patents
- trade flows
- procurement
- trial details
- energy / grid / infrastructure data
- minerals / climate / labor / scientific literature
- expensive alternative data if later justified

Launch principle:
- broad access at launch
- narrow default consumption

## 7. Hypothesis lifecycle

Suggested states:
- `raw`
- `scored`
- `challenged`
- `ready`
- `active`
- `dormant`
- `resolved`
- `retired`

Lifecycle:
1. `researcher` creates or updates a hypothesis.
2. `quant` scores it and adds expression candidates.
3. `critic` challenges it.
4. `trader` converts surviving ideas into trade intents.
5. `trader` executes and manages the position.
6. On resolution, `critic` and `quant` grade what happened.

## 8. Core decision framework

Every live candidate must answer:
- what changed in the world?
- why is the market still underpricing it?
- what is the cleanest listed expression?
- what would falsify the thesis?
- why does this beat `SPY`?
- why does this beat cash?

Useful scoring dimensions:
- thesis strength
- evidence breadth
- surprise-to-price gap
- expression clarity
- liquidity / execution quality
- regime fit
- crowding / redundancy penalty

## 9. Expression and sizing policy

Support these expression families:
- direct equity
- ETF
- pair trade
- competitor short
- later: options only after the equity workflow is stable

Launch rule:
- start with equities and ETFs only
- defer LEAPS and short-dated options until the core thesis engine proves edge

Sizing policy:
- starter positions small
- adds only after new independent confirmation
- no add if regime gates block it
- no position without defined max loss

## 10. Research and validation discipline

Before broad automation:
- manually replay at least 10 historical cases with point-in-time data
- validate whether the researcher would have found the thesis
- validate whether the critic would have killed the wrong names and allowed the right ones
- validate whether the trader would have expressed them sensibly

Do not store full chain-of-thought in canonical state.
Store:
- structured evidence
- concise rationale
- falsifiers
- decisions
- counterarguments

## 11. Build order

### Phase 1
- finalize doc set and authority chain
- define shared state schema changes
- define four agent workspaces and routing
- keep `AUTONOMOUS_PM_OPERATING_MODEL.md` as live trading authority

### Phase 2
- wire `researcher` pipelines for core feeds only
- build hypothesis creation / update workflow
- build quant scoring and regime snapshots

### Phase 3
- build critic review workflow
- build trader trade-intent and Alpaca reconciliation workflow
- bind Telegram to `trader`

### Phase 4
- run shadow mode with no execution
- compare proposed actions versus `SPY` and cash
- tighten permissions and failure modes

### Phase 5
- begin Alpaca paper execution
- add postmortem and deeper cold-path research expansion

## 12. Document authority

Priority order:
1. `AUTONOMOUS_PM_OPERATING_MODEL.md`
2. `AUTONOMOUS_PAPER_TRADING_POLICY.md`
3. this document

This document supersedes:
- `TRADING_SYSTEM_V1_ARCHITECTURE.md` for architecture direction

It does not yet replace the PM operating model as the trading authority.
