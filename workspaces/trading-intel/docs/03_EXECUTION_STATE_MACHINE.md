# 03 — Execution State Machine

Status: active. Defines the deterministic state transitions, gates, and reconciliation rules for hypotheses, trade intents, orders, and positions. Implements `02_ARCHITECTURE.md`.

## 1. Entities and primary states

- `hypothesis.state`: `raw` → `scored` → `challenged` → `ready` → `active` → `dormant` → `resolved` → `retired`.
- `trade_intent.state`: `proposed` → `critic_review` → `approved` | `blocked` → `submitted` → `filled` | `partial` | `canceled` | `rejected`.
- `order.state`: mirrors Alpaca order status; reconciled to `trade_intent` and `position`.
- `position.state`: `opening` → `open` → `scaling` → `trimming` → `closing` → `closed`.

## 2. Hard gates before execution

A `trade_intent` may transition to `submitted` only if all of the following hold:

1. References a valid `hypothesis_id` whose `state` is `ready` or `active`.
2. Has at least one `critic_review` with `all_challenges_addressed = true`, or an Aaron-approved override recorded in `audits`.
3. Regime gates in `01_OPERATING_AUTHORITY.md` permit the action under the current `regime.current`.
4. Concentration and per-trade risk limits in `01_OPERATING_AUTHORITY.md` are satisfied after the would-be fill.
5. No active pause scope blocks the atomic action (`open`, `add`, `trim`, `exit`).
6. For options vehicles, the hypothesis confidence is `high` and an explicit catalyst window is recorded.
7. Decision-critical evidence satisfies per-source freshness budgets (`evidence_freshness_status = pass`).
8. Factor-overlap and concentration checks pass (`factor_overlap_status = pass`) after hypothetical fill.
9. Explainability thresholds pass: hypothesis reference present, falsifier set present, provenance completeness above threshold, and counterargument quality above threshold.
10. Fill-realism checks pass: intent size does not exceed ADV-based cap and modeled slippage/spread assumptions are attached.

If any gate fails, the trade intent moves to `blocked` with a structured `blocked_reason`.

## 3. Tranche transitions

Tranches are recorded on the position and link back to the originating `trade_intent`:

- `starter` — opens position. Requires hypothesis `state = ready` and confidence `high`.
- `confirmation_add` — requires one new independent signal type since the last tranche and prior Critic challenges addressed.
- `conviction_add` — requires two or more independent signal types since last tranche and no broken falsifiers.
- `max_conviction` — requires thesis past mid-horizon with trajectory intact and regime in `risk_on` or `neutral`.

Tranche eligibility is computed by quant on signal or schedule and emitted as a `trade_intent` of `action = add`.

## 4. Falsifier monitoring

- `falsifier_signals` are written by researcher whenever a monitored condition's status changes.
- If any falsifier transitions to `broken`, quant must transition the hypothesis to `dormant` within one checkpoint and emit an `exit` or `trim` intent per policy.
- A `dormant` hypothesis cannot receive new adds; executor may still exit.

## 5. Critic review semantics

- Critic must produce a `critic_review` for every hypothesis before it can leave `challenged`, and for every `trade_intent` before it can leave `critic_review`.
- A review with unanswered challenges keeps the intent at `critic_review`.
- Critic does not unilaterally block. It blocks by leaving challenges unresolved. Quant or trader can either address the challenge (writing a `response`) or escalate via `/intent` for human override.
- Overrides are first-class: they are written to `audits` with the overriding actor, the override rationale, and a reference to the unresolved challenge.

## 6. Options-specific execution rules

- LEAPS: stop derived from premium; close on either underlying stop or 50% premium loss, whichever fires first.
- Shorter-dated options: orders must include an `event_date`; if the event passes without thesis confirmation, executor closes the position on the next checkpoint regardless of P&L.
- Assignment risk: executor must detect early assignment from Alpaca order/position webhooks (or daily reconciliation) and emit a corrective `trade_intent` if it materially changes exposure.

## 7. Reconciliation

- Executor reconciles Alpaca account, orders, and positions against shared state every checkpoint and on every order event.
- A divergence (e.g., an `order` Alpaca says is filled but no `position` tranche recorded) creates a `reconciliation_run` record and pauses new opens for the affected hypothesis until resolved.
- On broker-wide anomalies or unresolved reconciliation divergence, system auto-enters degraded mode via `system_pauses.scope = exits_trims_only` until cleared.
- End-of-day reconciliation writes a daily account snapshot used by the archivist for attribution.

## 8. Pause-state model

Pause scopes (from `01_OPERATING_AUTHORITY.md`): `new_entries_only`, `adds_only`, `shorts_only`, `exits_trims_only`, `full_system`.

- Each pause record lives in `system_pauses` with scope, reason, start, optional end, and source actor.
- Executor evaluates all active pauses against each candidate atomic action before submission.
- Rotations decompose into `exit` then `open` and are blocked if either atomic component is blocked.
- Drawdown circuit-breaker: at portfolio drawdown threshold events, system writes a mandatory pause record automatically (no manual step).

## 9.1 Deterministic regime convention

- `regime.current` means the `current` value from the latest `regime` snapshot by `determined_at`.
- The classification logic from signals to enum must be deterministic and sourced from the active `regime_rules` row (`rule_version = 'live'`).
- Any regime-rule change requires a `DECISION_LOG.md` entry and refreshed `experiment_id` tags.

## 9. Resolution and post-mortem

A hypothesis resolves when one of:

- All positions closed and executor marks the thesis lifecycle complete.
- Time horizon expires with no open exposure.
- Aaron explicitly resolves it via Telegram override (`/exit` with rationale).

Archivist runs the post-mortem (`postmortems` table), writes patterns to `patterns`, and transitions the hypothesis to `retired`.

## 10. Required test cases

These cases must pass before the system goes live in Alpaca paper:

1. Hypothesis cannot move to `ready` without quant score and addressed critic challenges.
2. Trade intent cannot reach `submitted` with a missing or wrong `hypothesis_id`.
3. Adding a tranche violating concentration limits is rejected and logged.
4. Falsifier flip to `broken` on an `active` hypothesis transitions it to `dormant` within one checkpoint.
5. Pause scope `new_entries_only` blocks an `open` intent but allows an `exit` intent.
6. Options trade intent without a recorded catalyst window is rejected.
7. Rotation decomposes correctly and is blocked when its `exit` half is blocked.
8. Reconciliation divergence creates a `reconciliation_run` and pauses new opens for that hypothesis.
9. Stale decision-critical evidence blocks `open` and records a structured `blocked_reason`.
10. Factor-overlap breach blocks or downsizes an `open`/`add` according to policy.
11. Missing explainability fields (falsifier/provenance/counterargument score) prevents exit from `critic_review`.
12. ADV-cap breach marks intent as not realistically fillable and blocks submission.
13. Broker-wide anomaly auto-creates `exits_trims_only` pause and blocks new entries portfolio-wide.
14. Regime classifier is deterministic: known signal snapshot maps to expected regime label.
