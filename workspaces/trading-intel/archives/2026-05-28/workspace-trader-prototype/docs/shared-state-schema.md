# Shared State Schema

## Purpose

This document defines the initial contract for the trader system's shared state.
It is the coordination layer between researcher, critic, quant, trader, and audit processes.

The intent is to make every important state transition explicit before implementation.

## Design Rules

- Every durable object has a stable ID.
- Every write is attributable to a role, timestamp, and reason.
- Every trade-relevant object is auditable end to end.
- No role may write fields outside its permission boundary.
- State must support point-in-time reconstruction.
- Invalidations, regime gates, and exposure constraints are first-class state, not narrative side notes.

## Global Metadata

All top-level durable records must include these fields unless otherwise stated:

| Field | Type | Notes |
|---|---|---|
| `id` | string | Stable unique identifier |
| `created_at` | datetime | UTC timestamp |
| `updated_at` | datetime | UTC timestamp |
| `created_by` | enum | `researcher`, `critic`, `quant`, `trader`, `system`, `aaron` |
| `updated_by` | enum | Same role set as above |
| `version` | integer | Monotonic record version |
| `status` | string | Record-specific lifecycle state |
| `audit_ref` | string | Pointer to audit event stream root |

## Top-Level Objects

1. `regime_snapshot`
2. `hypothesis`
3. `research_case`
4. `falsifier`
5. `trade_intent`
6. `position`
7. `portfolio_snapshot`
8. `risk_policy`
9. `approval_record`
10. `audit_event`

## Object Schemas

### `regime_snapshot`

Captures the system's current market regime assessment and gating implications.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | Example: `REGIME-20260528-01` |
| `status` | enum | yes | `draft`, `active`, `superseded` |
| `as_of` | datetime | yes | Effective timestamp |
| `summary` | string | yes | Concise regime description |
| `liquidity_state` | enum | yes | `loose`, `neutral`, `tight` |
| `volatility_state` | enum | yes | `compressed`, `neutral`, `expanded` |
| `breadth_state` | enum | yes | `broad`, `mixed`, `narrow` |
| `factor_leadership` | array[string] | yes | Leading drivers |
| `risk_posture` | enum | yes | `offense`, `neutral`, `caution`, `defense` |
| `sizing_multiplier` | number | yes | Portfolio-wide sizing scalar |
| `disallowed_patterns` | array[string] | no | Setups to avoid in this regime |
| `notes` | string | no | Supporting rationale |

Allowed writers:
- `quant` may propose and update draft regime snapshots.
- `critic` may append challenge notes through audit events only.
- `trader` may activate a snapshot only if no unresolved blocking critic challenge exists.
- `aaron` may override any field.

### `hypothesis`

Core research object for a specific mispricing thesis.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | Example: `HYPO-20260528-ABC` |
| `status` | enum | yes | `draft`, `active`, `invalidated`, `retired`, `archived` |
| `symbol_set` | array[string] | yes | Tickers or instruments |
| `title` | string | yes | Short thesis name |
| `thesis_type` | enum | yes | `long`, `short`, `pair`, `basket`, `watchlist` |
| `mispricing_claim` | string | yes | What the market is mispricing |
| `why_now` | string | yes | Why the opportunity exists now |
| `mechanism` | string | yes | Catalyst or repricing pathway |
| `time_horizon_days` | integer | yes | Expected window |
| `expected_move_base_pct` | number | yes | Base-case magnitude |
| `expected_move_bull_pct` | number | no | Bull-case magnitude |
| `expected_move_bear_pct` | number | no | Bear-case magnitude |
| `confidence_pct` | number | yes | 0-100 |
| `regime_fit` | enum | yes | `strong`, `acceptable`, `weak`, `blocked` |
| `consensus_view` | string | yes | What market likely believes |
| `variant_perception` | string | yes | Why system differs |
| `crowding_risk` | enum | yes | `low`, `medium`, `high` |
| `liquidity_profile` | enum | yes | `deep`, `adequate`, `fragile` |
| `invalidation_conditions` | array[string] | yes | Explicit fail conditions |
| `supporting_evidence` | array[string] | yes | Point-in-time evidence refs |
| `falsifier_ids` | array[string] | yes | Linked falsifier objects |
| `critic_state` | enum | yes | `not_reviewed`, `challenged`, `approved`, `blocked` |
| `quant_score` | number | no | Normalized composite score |
| `positioning_notes` | string | no | Positioning/crowding context |
| `next_review_at` | datetime | no | Scheduled review |

Allowed writers:
- `researcher` owns draft creation and updates to thesis content.
- `quant` may write `quant_score`, `regime_fit`, and structured scoring attachments.
- `critic` may write only `critic_state` and linked challenge references, not the thesis text.
- `trader` may not change thesis reasoning fields.
- `aaron` may override any field.

### `research_case`

Structured evidence pack supporting a hypothesis or historical validation.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | Stable case ID |
| `status` | enum | yes | `open`, `complete`, `stale`, `rejected` |
| `case_type` | enum | yes | `live`, `historical_validation`, `postmortem` |
| `hypothesis_id` | string | no | Null allowed for generic validation case |
| `as_of_date` | date | yes | Point-in-time boundary |
| `source_refs` | array[string] | yes | Raw source references |
| `timeline` | array[string] | yes | Ordered observations |
| `counterevidence` | array[string] | yes | Contradictory evidence |
| `point_in_time_pass` | boolean | yes | Whether data cleanliness passed |
| `conclusion` | string | yes | Final case conclusion |

Allowed writers:
- `researcher` owns the record.
- `critic` may append counterevidence or dispute markers through audit events.
- `aaron` may override any field.

### `falsifier`

Specific condition or observation that would weaken or invalidate a hypothesis.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | Stable falsifier ID |
| `status` | enum | yes | `active`, `triggered`, `dismissed`, `retired` |
| `hypothesis_id` | string | yes | Parent thesis |
| `severity` | enum | yes | `warning`, `major`, `fatal` |
| `condition_text` | string | yes | The actual falsifier |
| `observation_method` | string | yes | How it is measured |
| `check_frequency` | enum | yes | `daily`, `weekly`, `event_driven` |
| `last_checked_at` | datetime | no | Last evaluation time |
| `triggered_at` | datetime | no | If triggered |
| `trigger_evidence` | string | no | Evidence reference |

Allowed writers:
- `researcher` creates falsifiers.
- `critic` may trigger or escalate severity with evidence.
- `trader` may mark dismissed only with linked approval from `aaron`.
- `aaron` may override any field.

### `trade_intent`

Execution proposal derived from an active hypothesis.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | Stable intent ID |
| `status` | enum | yes | `draft`, `pending_review`, `approved`, `rejected`, `executed`, `canceled` |
| `hypothesis_id` | string | yes | Required linkage |
| `regime_snapshot_id` | string | yes | Regime basis |
| `instrument` | string | yes | Primary trade instrument |
| `direction` | enum | yes | `long`, `short` |
| `entry_logic` | string | yes | Why now and what entry means |
| `entry_zone` | string | yes | Price or condition band |
| `stop_logic` | string | yes | Explicit loss-of-edge or hard stop |
| `target_logic` | string | yes | Profit-taking or fair-value logic |
| `max_loss_pct_portfolio` | number | yes | Must fit policy |
| `sizing_basis` | string | yes | Formula and assumptions |
| `expected_holding_period_days` | integer | yes | Planned duration |
| `critic_signoff_required` | boolean | yes | Usually true |
| `critic_signoff_status` | enum | yes | `not_requested`, `pending`, `approved`, `rejected` |
| `approval_record_ids` | array[string] | yes | Sign-offs |
| `execution_constraints` | array[string] | no | Liquidity, spread, timing limits |

Allowed writers:
- `trader` owns creation and lifecycle.
- `quant` may write sizing support fields or attachments.
- `critic` may write signoff status only through approval records.
- `researcher` may not mutate execution fields once intent exists.
- `aaron` may override any field.

### `position`

Live or historical exposure record.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | Stable position ID |
| `status` | enum | yes | `open`, `trimmed`, `closed`, `suspended` |
| `hypothesis_id` | string | yes | Parent thesis |
| `trade_intent_id` | string | yes | Source execution intent |
| `instrument` | string | yes | Held instrument |
| `quantity` | number | yes | Current size |
| `cost_basis` | number | yes | Weighted entry |
| `mark_price` | number | no | Latest mark |
| `gross_exposure` | number | yes | Dollar exposure |
| `net_exposure` | number | yes | Signed exposure |
| `realized_pnl` | number | yes | Closed component |
| `unrealized_pnl` | number | no | Open component |
| `thesis_state` | enum | yes | `intact`, `weakened`, `broken` |
| `next_action_state` | enum | yes | `hold`, `trim`, `add_review`, `exit_review` |
| `last_review_at` | datetime | yes | Most recent review |

Allowed writers:
- `trader` owns quantity and position management fields.
- `critic` may write `thesis_state` only through a formal review event.
- `quant` may write mark-to-market derived analytics.
- `aaron` may override any field.

### `portfolio_snapshot`

Time-bounded aggregate portfolio state.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | Stable snapshot ID |
| `status` | enum | yes | `final` |
| `as_of` | datetime | yes | Snapshot timestamp |
| `nav` | number | yes | Net asset value |
| `cash_pct` | number | yes | Cash as share of NAV |
| `gross_exposure_pct` | number | yes | Gross exposure / NAV |
| `net_exposure_pct` | number | yes | Net exposure / NAV |
| `max_drawdown_ytd_pct` | number | yes | Drawdown tracker |
| `ytd_return_pct` | number | yes | Portfolio return |
| `spy_ytd_return_pct` | number | yes | Benchmark return |
| `active_hypothesis_count` | integer | yes | Count |
| `pending_trade_intent_count` | integer | yes | Count |
| `largest_single_name_pct` | number | yes | Concentration |
| `largest_theme_pct` | number | yes | Thematic concentration |
| `regime_snapshot_id` | string | yes | Active regime |

Allowed writers:
- `system` or `quant` may generate snapshots.
- No manual edits other than `aaron` overrides.

### `risk_policy`

Codified portfolio and trade risk rules.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | Stable policy ID |
| `status` | enum | yes | `draft`, `active`, `retired` |
| `portfolio_max_drawdown_pct` | number | yes | Example: 20 |
| `per_trade_max_loss_pct` | string | yes | Example: `2-3` |
| `max_single_position_pct` | number | yes | Capital concentration cap |
| `max_theme_exposure_pct` | number | yes | Theme concentration cap |
| `liquidity_minimum_rule` | string | yes | Minimum tradability rule |
| `regime_gate_rules` | array[string] | yes | Exposure gating rules |
| `add_to_loser_rule` | string | yes | Explicit conditional rule |
| `critic_override_rule` | string | yes | Approval requirement |

Allowed writers:
- `aaron` owns policy.
- `trader` may reference but not alter.
- `system` may read-only enforce.

### `approval_record`

Formal sign-off or block from a reviewing actor.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | Stable approval ID |
| `status` | enum | yes | `issued`, `superseded` |
| `target_object_type` | enum | yes | `hypothesis`, `trade_intent`, `position_change`, `regime_snapshot` |
| `target_object_id` | string | yes | Target record |
| `reviewer_role` | enum | yes | `critic`, `aaron` |
| `decision` | enum | yes | `approve`, `reject`, `block`, `conditional_approve` |
| `reason` | string | yes | Why |
| `conditions` | array[string] | no | Conditions if conditional |
| `issued_at` | datetime | yes | Timestamp |

Allowed writers:
- `critic` and `aaron` only.

### `audit_event`

Append-only event stream for every material change.

| Field | Type | Required | Notes |
|---|---|---|---|
| `id` | string | yes | Stable event ID |
| `status` | enum | yes | `recorded` |
| `event_type` | enum | yes | `create`, `update`, `review`, `approval`, `execution`, `invalidation`, `override` |
| `object_type` | string | yes | Target object type |
| `object_id` | string | yes | Target record ID |
| `actor_role` | enum | yes | Writer role |
| `event_time` | datetime | yes | When it happened |
| `field_changes` | array[string] | no | Changed fields summary |
| `reason` | string | yes | Why the change occurred |
| `source_refs` | array[string] | no | Supporting references |

Allowed writers:
- Any role may append events within its write permissions.
- No role may edit or delete prior events.

## State Transition Rules

### `hypothesis`

- `draft -> active`: requires complete thesis fields, at least one falsifier, and critic state not equal to `blocked`.
- `active -> invalidated`: requires triggered fatal falsifier or explicit Aaron override.
- `active -> retired`: thesis reached maturity or expected asymmetry compressed.
- `invalidated -> archived`: allowed after postmortem completion.

### `trade_intent`

- `draft -> pending_review`: requires active hypothesis, active regime snapshot, and sizing basis.
- `pending_review -> approved`: requires all mandatory approvals.
- `approved -> executed`: requires execution event and resulting position record.
- `pending_review -> rejected`: requires approval record with reject or block.
- `approved -> canceled`: requires explicit reason event.

### `position`

- `open -> trimmed`: partial reduction executed.
- `open -> closed`: full exit executed.
- `open -> suspended`: operational hold due to policy or market issue.
- No transition may result in increased size without linked trade intent and approval chain where required.

## Permission Matrix

| Field Family | Researcher | Critic | Quant | Trader | Aaron | System |
|---|---|---|---|---|---|---|
| Thesis narrative | write | no | no | no | override | no |
| Falsifiers | write | trigger/escalate | no | limited | override | no |
| Scoring / regime analytics | no | no | write | read | override | derived |
| Execution plan | read | review | assist | write | override | no |
| Position quantities | no | no | derived | write | override | derived |
| Risk policy | read | read | read | read | write | enforce |
| Audit log | append own | append own | append own | append own | append | append derived |

## Validation Requirements Before Implementation

The schema is not complete until all of the following are validated:

1. Every object can be reconstructed point in time from audit events plus snapshots.
2. Every trade path can be traced from hypothesis to execution to exit.
3. There is no field that requires two independent roles to write conflicting truths.
4. Every forbidden action in Aaron's rules is enforceable from state alone.
5. Historical validation cases can be represented without leaking future information.

## Immediate Next Gaps

- Canonical enums and core field bounds are now encoded in the Python models.
- JSON Schema should be emitted from the models and treated as generated output.
- Add explicit derived metrics formulas.
- Define unresolved challenge handling for critic objections.
- Define portfolio-level correlation and theme exposure objects if needed.
