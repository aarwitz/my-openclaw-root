# 04 — Shared State Schema

Status: active. Authoritative entity model. The implementing DDL lives in `sql/schema.sql`. If this doc and the DDL disagree, the DDL is the runtime truth and this doc must be updated to match.

## 1. Storage

- Engine: SQLite (WAL mode).
- Location: `~/.openclaw/state/trading-intel.sqlite`.
- Access: all five trading agents share this DB. Write permissions are enforced at the application layer (see `02_ARCHITECTURE.md` section 4).
- Backups: daily snapshot to `~/.openclaw/state/backups/trading-intel-YYYY-MM-DD.sqlite`.

## 2. Storage policy for reasoning

- Store structured evidence, falsifiers, concise rationale (cap at ~500 chars), decisions, counterarguments.
- Do not store full chain-of-thought transcripts in the canonical store. Long reasoning artifacts go to a separate per-agent journal file under `~/.openclaw/state/journals/<agent>/YYYY-MM-DD.md` referenced by `audits.journal_ref`.

## 3. Core entities

### 3.1 `hypotheses`

| Field | Type | Notes |
|---|---|---|
| `id` | TEXT PK | e.g., `HYPO-20260527-GLP1-LILLY` |
| `created_at` | TEXT | ISO timestamp |
| `created_by` | TEXT | always `researcher` |
| `tickers` | TEXT (JSON) | list of strings |
| `thesis_summary` | TEXT | 2–3 sentences |
| `state` | TEXT | enum from `03_EXECUTION_STATE_MACHINE.md` section 1 |
| `confidence` | TEXT | `low` / `medium` / `high` |
| `time_horizon` | TEXT | e.g., `12-18 months` |
| `quant_score` | REAL | 0–100, nullable until scored |
| `scored_at` | TEXT | nullable |
| `edge_decay_monthly_pct` | REAL | nullable |
| `last_critic_review_at` | TEXT | nullable |
| `resolved_at` | TEXT | nullable |
| `resolved_state` | TEXT | `correct_right_reasons` / `correct_wrong_reasons` / `wrong` / null |
| `archivist_grade` | TEXT | nullable, short letter or score |
| `rationale_concise` | TEXT | <= 500 chars |
| `journal_ref` | TEXT | path to long-form reasoning if any |

### 3.2 `hypothesis_evidence`

Each row is one piece of evidence. Append-only.

| Field | Type | Notes |
|---|---|---|
| `id` | TEXT PK | UUID |
| `hypothesis_id` | TEXT FK | |
| `indicator` | TEXT | what is being observed |
| `value` | TEXT | observed value |
| `source` | TEXT | e.g., `SEC EDGAR` |
| `source_url` | TEXT | nullable |
| `retrieved_at` | TEXT | when our system fetched it |
| `released_at` | TEXT | when the source published it |
| `as_of` | TEXT | the point-in-time the value refers to |
| `vintage` | TEXT | revision id where applicable (FRED/ALFRED) |
| `signal_type` | TEXT | for independence counting (e.g., `clinical`, `capex`, `hiring`) |

### 3.3 `falsifier_signals`

| Field | Type | Notes |
|---|---|---|
| `id` | TEXT PK | |
| `hypothesis_id` | TEXT FK | |
| `condition` | TEXT | falsifier condition |
| `monitor_frequency` | TEXT | |
| `current_status` | TEXT | `no_signal` / `monitoring` / `warning` / `broken` |
| `updated_at` | TEXT | |
| `source_ref` | TEXT | evidence row id or URL |

### 3.4 `expression_candidates`

| Field | Type | Notes |
|---|---|---|
| `id` | TEXT PK | |
| `hypothesis_id` | TEXT FK | |
| `vehicle` | TEXT | `direct_equity` / `etf` / `leaps` / `short_options` / `competitor_short` / `pair_trade` |
| `ticker` | TEXT | underlying or contract symbol |
| `option_contract` | TEXT | OCC symbol for options, nullable |
| `event_date` | TEXT | required for shorter-dated options |
| `conviction_weight` | REAL | 0–1 |
| `quant_rationale` | TEXT | <= 500 chars |
| `recommended` | INTEGER | 0/1 |
| `score_json` | TEXT | full expression scoring dict |
| `created_at` | TEXT | |

### 3.5 `regime`

Single-row-per-snapshot table.

| Field | Type | Notes |
|---|---|---|
| `id` | TEXT PK | snapshot id |
| `determined_at` | TEXT | |
| `determined_by` | TEXT | always `quant` |
| `current` | TEXT | `risk_on` / `neutral` / `caution` / `risk_off` / `crisis` |
| `signals_json` | TEXT | spy_trend, credit_spreads, vix_term_structure, yield_curve |
| `implications_json` | TEXT | new thesis cadence, add cadence, position mgmt, cash target |

Convention: live `regime.current` is the latest snapshot by `determined_at`.

### 3.6 `trade_intents`

| Field | Type | Notes |
|---|---|---|
| `id` | TEXT PK | |
| `hypothesis_id` | TEXT FK | required |
| `expression_candidate_id` | TEXT FK | required |
| `created_by` | TEXT | `quant` or `trader` (for human-initiated rotations) |
| `created_at` | TEXT | |
| `action` | TEXT | `open` / `add` / `trim` / `exit` / `rotate` |
| `tranche_type` | TEXT | nullable for non-add actions |
| `ticker` | TEXT | |
| `vehicle` | TEXT | from `expression_candidates.vehicle` |
| `size` | REAL | shares or contracts |
| `entry_price_target` | TEXT | price or range |
| `stop_rule` | TEXT | |
| `time_horizon` | TEXT | |
| `triggered_by` | TEXT | concise signal description |
| `edge_scorecard_json` | TEXT | expected edge by horizon (daily/weekly/monthly/quarterly) vs SPY and cash |
| `evidence_freshness_status` | TEXT | `pass` / `fail` |
| `factor_overlap_status` | TEXT | `pass` / `fail` |
| `provenance_completeness_pct` | REAL | 0–100 |
| `counterargument_quality_score` | REAL | critic quality score |
| `explainability_status` | TEXT | `pass` / `fail` |
| `experiment_id` | TEXT | policy/prompt/scoring attribution tag |
| `max_fillable_size` | REAL | ADV-aware maximum realistic size |
| `modeled_slippage_bps` | REAL | expected slippage assumption |
| `modeled_fill_price` | REAL | slippage-adjusted expected fill |
| `state` | TEXT | from `03_EXECUTION_STATE_MACHINE.md` section 1 |
| `blocked_reason` | TEXT | nullable |
| `submitted_at` | TEXT | nullable |
| `executed_at` | TEXT | nullable |
| `actual_price` | REAL | nullable |
| `actual_size` | REAL | nullable |
| `broker_order_id` | TEXT | nullable |

### 3.7 `critic_reviews`

| Field | Type | Notes |
|---|---|---|
| `id` | TEXT PK | |
| `target_type` | TEXT | `hypothesis` or `trade_intent` |
| `target_id` | TEXT | |
| `reviewed_at` | TEXT | |
| `reviewed_by` | TEXT | always `critic` |
| `challenges_json` | TEXT | list of {challenge, response, resolved} |
| `all_challenges_addressed` | INTEGER | 0/1 |

### 3.8 `orders`

Mirror of Alpaca order events relevant to this system.

| Field | Type | Notes |
|---|---|---|
| `broker_order_id` | TEXT PK | |
| `trade_intent_id` | TEXT FK | |
| `symbol` | TEXT | |
| `side` | TEXT | `buy` / `sell` |
| `qty` | REAL | |
| `type` | TEXT | `market` / `limit` / `stop` / `stop_limit` |
| `limit_price` | REAL | nullable |
| `status` | TEXT | mirror of Alpaca |
| `submitted_at` | TEXT | |
| `filled_at` | TEXT | nullable |
| `avg_fill_price` | REAL | nullable |
| `raw_payload_path` | TEXT | optional |

### 3.9 `positions` and `tranches`

`positions` is the live aggregate per (hypothesis, vehicle, ticker). `tranches` are the immutable entry/exit ledger.

`positions` columns: `id`, `hypothesis_id`, `ticker`, `vehicle`, `qty`, `cost_basis`, `current_price`, `current_value`, `unrealized_pnl_pct`, `regime_at_first_open`, `state`, `opened_at`, `closed_at`.

Add P&L realism fields on `positions`: `pnl_ideal`, `pnl_slippage_adjusted`.

`tranches` columns: `id`, `position_id`, `trade_intent_id`, `tranche_type`, `qty`, `entry_price`, `entry_at`, `exit_price`, `exit_at`, `exit_reason`, `return_pct`.

### 3.10 `system_pauses`

| Field | Type | Notes |
|---|---|---|
| `id` | TEXT PK | |
| `scope` | TEXT | `new_entries_only` / `adds_only` / `shorts_only` / `exits_trims_only` / `full_system` |
| `reason` | TEXT | |
| `started_at` | TEXT | |
| `ended_at` | TEXT | nullable |
| `source_actor` | TEXT | which agent or human imposed it |
| `block_list_json` | TEXT | optional list of hypothesis_ids it applies to |

### 3.11 `reconciliation_runs`

| Field | Type | Notes |
|---|---|---|
| `id` | TEXT PK | |
| `started_at` | TEXT | |
| `finished_at` | TEXT | |
| `divergences_json` | TEXT | list of {field, expected, observed} |
| `resolved` | INTEGER | 0/1 |

### 3.12 `postmortems` and `patterns`

`postmortems` columns: `id`, `hypothesis_id`, `resolved_at`, `grade`, `thesis_analysis_json`, `expression_analysis_json`, `critic_analysis_json`, `researcher_analysis_json`, `external_mechanism_check_json`, `experiment_id`.

`patterns` columns: `id`, `created_at`, `pattern`, `confidence`, `applies_to_json`, `source_postmortem_id`, `external_validation_status`, `experiment_id`.

### 3.13 `audits`

Append-only. Every state transition writes an audit row.

| Field | Type | Notes |
|---|---|---|
| `id` | TEXT PK | |
| `timestamp` | TEXT | |
| `actor` | TEXT | one of the five agents or `human` |
| `entity_type` | TEXT | |
| `entity_id` | TEXT | |
| `action` | TEXT | concise verb |
| `before_state` | TEXT (JSON) | nullable |
| `after_state` | TEXT (JSON) | nullable |
| `rationale_concise` | TEXT | <= 500 chars |
| `journal_ref` | TEXT | nullable path to long-form journal |
| `experiment_id` | TEXT | nullable experiment tag |

### 3.14 `validation_cases`

Leakage-resistant validation corpus and outcomes.

Fields: `id`, `masked_case_json`, `case_class` (`winner` / `negative_control` / `post_cutoff`), `fake_date_variant`, `model_decision_json`, `resolved_outcome_json`, `passed`, `created_at`, `experiment_id`.

### 3.15 `regime_rules`

Deterministic mapping from input signals to regime enum.

Fields: `id`, `rule_version`, `effective_at`, `thresholds_json`, `notes`, `experiment_id`.

## 4. Required indexes

- `hypotheses(state)`, `hypotheses(resolved_at)`
- `hypothesis_evidence(hypothesis_id)`, `hypothesis_evidence(signal_type, hypothesis_id)`
- `falsifier_signals(hypothesis_id, current_status)`
- `trade_intents(state)`, `trade_intents(hypothesis_id)`
- `orders(trade_intent_id)`, `orders(status)`
- `positions(hypothesis_id, state)`
- `system_pauses(scope, ended_at)`
- `audits(entity_type, entity_id, timestamp)`
- `trade_intents(experiment_id, state)`
- `validation_cases(case_class, passed)`

## 5. Mapping notes (retired prototypes)

- Retired Pydantic prototype types from `/home/aaron/.openclaw/workspace/trader/trader_state/` map as follows:
  - `Hypothesis` → `hypotheses` + `hypothesis_evidence` + `expression_candidates`.
  - `TradeIntent` → `trade_intents` + `critic_reviews`.
  - `Position` → `positions` + `tranches`.
  - `RegimeSnapshot` → `regime`.
  - `ApprovalRecord` → `audits` rows with `action = override_approval`.
  - `RiskPolicy` is doc-defined (`01_OPERATING_AUTHORITY.md`), not a DB entity.
- Retired SQLite tables (`signals`, `ideas`, `evidence_items`, `trades`) from the legacy druck schema are not migrated; the new schema is canonical going forward.

## 6. Migration policy

- Schema changes are additive when possible. Breaking changes require a numbered migration file under `sql/migrations/` and an entry in `DECISION_LOG.md`.
- The DB always carries a `_schema_version` row in a `meta` table.
- SQLite writer policy: configure `busy_timeout`, keep write transactions short, and avoid holding write transactions across model calls. If write contention appears, use a single serialized writer queue.
