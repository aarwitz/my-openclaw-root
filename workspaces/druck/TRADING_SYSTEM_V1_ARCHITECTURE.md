# Trading System V1 Architecture

Status: proposed replacement for the current Sheets-first control flow
Owner: Aaron
Operator: Druck
Execution venue: Alpaca paper only
Notification venue: Telegram only
Canonical storage: SQLite with nightly Drive backup
Human review surfaces: Google Sheets and weekly summaries

## 1. Executive recommendation

Do not keep extending the current system as a Sheets-centered workflow.

The right V1 is:
- SQLite as the system of record
- deterministic rule engine for signal routing, risk, execution, and evaluation
- Alpaca paper autonomous execution
- Sheets as reporting only
- Telegram as alerting only
- all external calls wrapped by a spend ledger

The goal for V1 is not maximum feature breadth. The goal is a fast, auditable learning loop:

signal -> investigate -> decide -> execute -> reconcile -> evaluate -> adjust

The system should prefer clean, measurable, event-driven strategies over broad source sprawl.

## 2. Core design principles

1. DB-first, not Sheets-first.
2. Separate signal detection from trade execution.
3. Keep deterministic rules as the authority, use LLMs for extraction, summarization, classification, and diff interpretation only.
4. Treat cash as a real alternative, not a zero baseline.
5. Preserve exact evidence used at trade time.
6. Measure research quality and execution quality separately.
7. Prefer fewer strategies with clean attribution over many overlapping strategies.
8. Fail closed on data integrity, but do not let reporting artifacts block valid paper trading decisions.

## 3. What changes from the current architecture

### Keep
- Finnhub
- Polygon / Massive
- FMP
- Alpaca paper
- Schwab read-only
- Brave search
- Event Registry
- OpenClaw
- Telegram notifications
- Google Sheets for review
- Drive for durable raw artifacts

### Change
- Sheets stop being the source of truth
- candidate generation no longer depends on sheet population state
- strategy modules operate on DB entities, not on sheet rows
- paper execution is driven by lifecycle state transitions, not by desk-post formatting
- evaluation is strategy-native and source-native from day 1

### Defer from V1
- Reddit automation unless it is truly trivial
- broad social ingestion as a primary trigger
- auto-generated strategy variants at scale
- regime-based sizing modulation
- high-conviction thesis trading as a live autonomous strategy

## 4. Canonical entity model

The current `phase2/schema.py` is a good candidate-record schema for one layer of the system, but it is not sufficient as the full operating model. V1 should add the following entities.

### 4.1 signals
Represents a raw trigger from any source.

Fields:
- signal_id
- detected_ts
- source_type (`paste_in`, `news`, `filing`, `analyst`, `earnings`, `transcript`, `manual`, `scanner`)
- source_name
- source_ref
- ticker
- direction (`long`, `short`, `unknown`)
- raw_payload_path
- freshness_window_minutes
- expires_ts
- confidence_raw
- strategy_routes_json
- status (`new`, `enriched`, `expired`, `discarded`, `promoted`)

### 4.2 evidence_items
Represents normalized supporting facts attached to a signal or idea.

Fields:
- evidence_id
- signal_id nullable
- idea_id nullable
- evidence_type (`filing`, `news`, `price_action`, `analyst`, `insider`, `transcript`, `fundamental`, `social`)
- source
- source_ts
- summary
- sentiment
- strength_score
- file_path_or_url
- structured_json

### 4.3 ideas
Represents a tradeable thesis candidate before execution.

Fields:
- idea_id
- opened_ts
- ticker
- direction
- primary_strategy_id
- thesis_id nullable
- status (`open`, `watch`, `staged`, `expired`, `rejected`, `traded`)
- aggregate_score
- corroboration_count
- cash_hurdle_pass
- expected_holding_days
- expected_return_low
- expected_return_base
- expected_return_high
- risk_flags_json
- expiry_ts
- created_from_signal_id

### 4.4 trade_intents
Represents a rules-approved execution plan before an order is sent.

Fields:
- intent_id
- created_ts
- idea_id
- strategy_id
- variant_id
- ticker
- direction
- target_size_pct
- target_shares
- entry_style (`limit_mid`, `limit_passive`, `market`, `marketable_limit`)
- entry_limit_price nullable
- stop_loss
- targets_json
- trigger_text
- invalidator_text
- time_stop_days
- regime_tag_json
- expected_edge_pct
- cash_hurdle_pass
- approval_reason_json
- blocked_reason nullable
- status (`pending`, `blocked`, `ready`, `submitted`, `canceled`)

### 4.5 trades
Use the proposed table, with a few additions.

Required fields:
- trade_id
- intent_id
- strategy_id
- variant_id
- ticker
- direction
- shares
- entry_price
- entry_ts
- exit_price
- exit_ts
- stop_loss
- stop_type
- targets_json
- target_type
- thesis_id nullable
- regime_tag_json
- catalyst
- conviction
- size_pct
- paper_fill_pnl
- conservative_fill_pnl
- exit_reason
- entry_conditions_json
- data_quality_flags_json
- thematic_cluster
- engine (`conviction`, `opportunistic`)

Add:
- entry_order_id
- exit_order_id
- idea_id
- source_origin (`user`, `system`, `trusted_source`, `hybrid`)
- sector_tag
- factor_tags_json
- holding_days
- slippage_vs_mid_bps
- slippage_vs_arrival_bps

Notes:
- `entry_conditions_json` should snapshot the environment at trade time, for example spread, volume vs 20d, ATR%, RSI14, gap%, distance to moving averages, major ETF moves, and VIX.
- `data_quality_flags_json` should mark issues such as stale quotes, incomplete filing parses, conflicting price sources, low-confidence extraction, halts, or wide spreads.
- flagged trades may still be useful operationally, but should be excluded from clean attribution cohorts.

### 4.6 positions
Live position state independent of closed-trade history.

Fields:
- ticker
- direction
- strategy_id
- variant_id
- opened_trade_id
- shares_open
- avg_cost
- market_value
- unrealized_pnl
- stop_loss
- targets_json
- health_status
- add_count
- last_review_ts

### 4.7 strategy_performance
Per strategy and variant evaluation.

Fields:
- strategy_id
- variant_id
- as_of_date
- n
- sample_tier (`debug`, `provisional`, `usable`, `strong`)
- win_rate
- avg_win
- avg_loss
- expectancy
- total_return
- alpha_vs_spy
- alpha_vs_qqq
- alpha_vs_sector
- alpha_vs_random_entry_baseline
- max_drawdown
- avg_holding_days
- sharpe_30d
- sharpe_90d
- regime_breakdown_json
- cluster_breakdown_json
- source_breakdown_json
- long_short_breakdown_json
- clean_vs_flagged_breakdown_json
- active_flag
- pause_reason nullable

### 4.8 cost_ledger
Every external call gets logged here.

Fields:
- cost_event_id
- ts
- vendor
- endpoint
- units
- estimated_cost
- hard_cost nullable
- call_purpose
- strategy_id nullable
- ticker nullable
- run_id nullable
- status
- latency_ms
- metadata_json

### 4.9 regime_states
Daily market snapshot table.

Fields:
- as_of_date
- spy_vs_50dma
- spy_vs_200dma
- spy_dist_from_ath_pct
- vix_level
- vix_term_structure
- us2y
- us10y
- us30y
- curve_shape
- ig_spread
- hy_spread
- tech_vs_spy
- semis_vs_spy
- pct_spx_above_200dma
- revision_breadth
- dxy
- gold
- copper
- oil
- regime_classification
- tech_heat_score
- macro_stress_score
- breadth_score

### 4.10 portfolio_risk_snapshots
Needed to avoid hidden concentration.

Fields:
- snapshot_ts
- net_exposure_pct
- gross_exposure_pct
- long_gross_pct
- short_gross_pct
- cash_pct
- top_name_pct
- top_sector_pct
- factor_exposure_json
- theme_exposure_json
- open_risk_pct
- daily_drawdown_pct
- weekly_drawdown_pct
- monthly_drawdown_pct
- opportunity_cost_pct
- breach_flags_json

### 4.11 candidate_decisions
Every candidate should be logged whether it becomes a trade, watch, or reject.

Fields:
- candidate_id
- ts
- source
- strategy_id
- ticker
- signal_summary
- score_components_json
- decision (`trade`, `watch`, `reject`)
- reject_reason nullable
- watch_until nullable
- price_at_decision
- fwd_return_1d
- fwd_return_7d
- fwd_return_30d
- signal_holdout_7d_pnl
- signal_holdout_30d_pnl
- eventual_trade_id nullable

This table is critical for learning from false negatives and false positives.

### 4.12 trade_attribution
Computed nightly per closed trade.

Fields:
- trade_id
- signal_return
- management_return
- sizing_return
- computed_ts

Purpose:
- separate raw signal quality from management quality and sizing quality
- avoid confusing a good entry with poor management, or vice versa

### 4.13 thesis_versions
For the later conviction engine.

Fields:
- thesis_id
- version
- created_ts
- ticker
- market_consensus
- bot_assumption
- disagreement_reason
- testable_predictions_json
- invalidation_conditions
- target_timeframe
- position_size_pct
- pre_mortem
- supersedes_version nullable
- change_reason nullable


## 5. Lifecycle state machine

Every actionable name should move through explicit states.

### Signal lifecycle
`new -> enriched -> promoted | expired | discarded`

### Idea lifecycle
`open -> watch -> staged -> traded | expired | rejected`

### Intent lifecycle
`pending -> blocked | ready -> submitted -> canceled | filled`

### Trade lifecycle
`submitted -> partially_filled -> filled -> managing -> exited -> reconciled -> evaluated -> archived`

This state model prevents ambiguous automation around partial fills, stale ideas, and post-trade evaluation.

## 6. Risk architecture

The proposed `risk.yaml` is directionally good. I would keep the headline limits and add operational gates.

### 6.1 Keep
- min_market_cap 500M
- shorts true
- options false

### 6.1A Risk modes
Use explicit operating modes rather than one static profile.

Recommended modes:
- `seed`
- `normal`
- `aggressive`

Recommended shape:
- `seed`: very small sizes, lower max positions, lower sector concentration, tighter drawdown controls
- `normal`: moderate autonomous sizing once learning is real
- `aggressive`: only after system proves signal quality, execution quality, and risk discipline

This is better than jumping straight to aggressive limits while the plumbing is still young.

### 6.2 Adjust
- seed mode should override strategy sizes globally for 14 days
- `min_dollar_volume` should be tiered by strategy, not global only
  - high-liquidity event strategies: 20M+
  - slower conviction/watch strategies: 5M+ acceptable
- add `max_new_entries_per_day`, recommend 3 initially
- add `max_same_theme_pct`, recommend 35 to 40
- add `max_same_catalyst_cluster`, recommend 3 names
- add `max_gap_entry_pct_above_ref`, strategy specific
- add `min_cash_hurdle_excess_return_bps`

### 6.3 New hard gates
A trade cannot proceed if any of the below fail:
- data integrity check fails
- reconciliation mismatch unresolved
- risk snapshot already breached on blocking dimension
- expected edge does not beat cash hurdle
- source freshness expired
- order would materially increase factor concentration beyond allowed range

## 7. Cash hurdle standard

This needs to be explicit in V1.

For each idea / trade intent, compute:
- expected holding period
- base-case expected return
- downside estimate
- expected value relative to cash for same holding period

Add fields:
- cash_alt_return_pct
- edge_vs_cash_pct
- hurdle_pass boolean

Rule:
If edge vs cash is weak or negative after risk adjustment, no trade.

This directly reflects Aaron's stated preference to treat cash at 3% as the default alternative. Source: memory/2026-05-12.md#L1-L3

## 8. Strategy framework

Do not launch six autonomous opportunistic strategies at once.

### 8.1 Recommended V1 paper-live strategies
Start with only three:
1. `post_earnings_drift`
2. `8k_material_event`
3. `trusted_source_catalyst`

These should begin in debug sizing under seed mode. Graduation to larger sizing should require both elapsed time and closed-trade sample evidence, not just enthusiasm.

Why:
- strong event boundaries
- shorter feedback loops
- easier attribution
- good mix of machine-discovered and human-curated triggers

### 8.2 Put in shadow mode first
- `analyst_upgrade_momentum`
- `insider_cluster_buying`
- `x_cashtag_paste`

### 8.3 Defer
- `high_conviction_thesis` as autonomous execution logic

It should exist as a research object and candidate pipeline before it is allowed to trade itself.

### 8.4 Strategy states
Every strategy should have one of:
- `research_only`
- `shadow`
- `paper_live`
- `quarantine`
- `paused`
- `retired`

`quarantine` is useful when a strategy should keep producing low-cost learning data at tiny size while mutation variants are tested.

Important restraint:
- quarantine is optional in V1 and should not become an excuse to build automatic mutation machinery too early
- if it adds operational complexity before the first full loop is stable, keep the state but defer automated behavior

## 9. Execution policy

The current Alpaca paper policy and Monday runbook are useful foundations, but they should be updated to reflect the DB-first lifecycle.

### 9.1 Entries
- default to midpoint-based limit for liquid entries
- use marketable limit if urgency is high but spreads remain acceptable
- avoid fully blind market entries except where strategy rules explicitly justify it

### 9.2 Exits
- protective stops are rule-driven only
- high-liquidity exits may use market orders
- all exits must store reason category and triggering evidence

### 9.3 Position management loop
The 1-minute market-hours loop is reasonable if lightweight, but prioritize tasks:
- P0: stop / invalidation / rejection / reconciliation failure
- P1: live entries needing action inside freshness window
- P2: research enrichment
- P3: background scans

### 9.4 Reconciliation
End of day reconciliation must compare:
- Alpaca positions
- SQLite positions
- trade ledger
- reporting sheets mirror

Any mismatch should generate a pause reason if unresolved.

## 10. Evaluation model

The requested evaluation layer is correct, but split it into two components.

### 10.1 Research quality
Track by source, strategy, and signal family:
- hit rate
- expectancy
- alpha at 1d / 5d / 20d
- corroboration usefulness
- source trust decay
- false-positive rate

### 10.2 Execution quality
Track separately:
- slippage vs midpoint
- slippage vs arrival
- fill rate
- partial-fill frequency
- gap-through-stop rate
- adverse excursion after entry
- missed-trade opportunity cost for too-passive limits

Do not let strategy learning confuse idea quality with fill quality.

## 11. Experimentation policy revisions

The broad exploration / exploitation idea is good, but auto-generated variants should be constrained early.

### Recommended policy
First 30 to 45 days:
- no broad automatic variant generation
- manual creation only, or at most very tightly bounded mutations for quarantined strategies
- at most 1 to 2 active variants per strategy
- only one parameter changed at a time

After baseline exists:
- allow limited perturbation
- fixed evaluation windows
- do not mutate an actively measured variant mid-window

### Sample-size tiers
Use explicit evidence tiers instead of one flat threshold:
- `debug`: n < 20
- `provisional`: 20 to 50
- `usable`: 50 to 100
- `strong`: 100+

This is better than pretending all n=20 evidence is equally persuasive.

### Pause rules to keep
- any strategy or variant down 30% of allocated capital -> pause or quarantine
- after sufficient sample, negative expectancy over 30 trades -> pause
- rolling 30d alpha < -5% over 60d -> pause

### Add
- elevated rejection rate -> pause entries for that strategy
- data corruption or stale evidence -> pause that source route
- quarantine mode for struggling strategies that still may have useful signal value under tiny sizing

## 12. Manual paste-in should be a first-class product feature

This is a very good idea and should become the main human edge interface.

### Recommendations
- store the raw paste exactly as submitted
- tag origin as `user`, `trusted_source`, or `hybrid`
- retain extracted structured hypothesis plus confidence
- create a `signal` first, not a trade decision directly
- route to watch if corroboration insufficient
- auto-trade only after rules approve and risk checks pass

### Trusted source scoring
Do not use hit rate alone.

Track:
- hit rate
- expectancy
- median alpha by horizon
- drawdown contribution
- regime dependency
- strategy-route fit

Demotion should use expectancy plus sample size, not only percentage right.

## 13. EDGAR ingestion is higher priority than Reddit

Agree strongly with adding EDGAR.

### V1 filing priorities
1. 8-K
2. Form 4
3. 10-Q / 10-K diffs
4. 13D / 13G

### V1 filing behavior
- use SEC-compliant User-Agent and 8 req/sec cap
- preserve raw filing locally and in Drive
- extract item numbers and changed sections
- summarize deltas, not full documents
- push notable filings during market hours only when trade-relevant

## 14. Transcript layer guidance

Do not optimize for full transcript storage alone.

Extract:
- prepared remarks summary
- Q&A summary
- quarter-over-quarter language changes
- signals about demand, margin, hiring, pricing, capex, customer concentration, inventory
- management confidence / evasiveness markers

Transcript value is mostly in delta and Q&A.

## 15. Portfolio construction improvements

Current scalar limits are not enough.

Add explicit factor and theme controls:
- mega-cap tech
- AI / semis
- software
- high-beta growth
- index exposure
- energy / commodity
- financials
- consumer beta

A compliant system can still accidentally become one giant correlated bet. Portfolio snapshots should block that.

## 16. Global pause taxonomy

Add pause reasons and scope.

Reasons:
- `risk_breach`
- `data_integrity_failure`
- `reconciliation_mismatch`
- `broker_api_instability`
- `cost_overrun`
- `signal_source_corruption`
- `order_rejection_spike`
- `manual_review_required`

Scopes:
- strategy only
- shorts only
- new entries only
- all adds only
- full system

## 17. Cost tracker requirements

The spend ledger should be mandatory infrastructure, not a later add-on.

### Requirements
- wrap every external API call
- log vendor, endpoint, units, estimated cost, call purpose
- support projected monthly burn calculation
- throttle non-essential sources first when budget exceeds threshold
- send daily Telegram digest

### Non-essential sources for throttling order
1. broad background scans
2. transcript enrichment retries
3. low-priority search augmentation
4. shadow-mode strategy enrichment

Do not throttle broker reconciliation, risk checks, or stop management.

## 18. Recommended build order

This ordering is intentionally documentation-first and architecture-stable. Do not begin broad implementation until the document set is tight and non-contradictory.

### Phase A: canonical docs and schema
1. architecture doc
2. risk config and strategy registry
3. SQLite schema
4. execution state machine spec
5. cost tracker spec
6. paste-in signal spec

### Phase B: operating substrate
7. cost tracker wrapper
8. lifecycle state machine implementation
9. reconciliation engine

### Phase C: execution substrate
10. Alpaca paper execution service
11. position management loop
12. order and fill logging
13. slippage tracking
14. Telegram execution notifications

### Phase D: first signal loop
15. manual paste-in endpoint
16. signal -> evidence -> idea pipeline
17. candidate decision logging
18. EDGAR ingestion for 8-K and Form 4
19. Event Registry and FMP enrichment
20. one daily and one weekly digest

### Phase E: first autonomous strategies
21. `trusted_source_catalyst` paper-live
22. `post_earnings_drift` paper-live
23. `8k_material_event` paper-live
24. `analyst_upgrade_momentum` shadow
25. `insider_cluster_buying` shadow
26. `x_cashtag_paste` shadow

### Phase F: evaluation and refinement
27. strategy performance tables and dashboards
28. trusted source calibration
29. portfolio risk snapshotting
30. replay / audit report generation
31. component attribution
32. limited variant testing

### Phase G: later additions
33. transcript delta engine
34. conviction thesis engine
35. regime modulation after sufficient data
36. optional Reddit watchlist monitor

## 19. Minimal V1 deliverables

To avoid bloat, V1 is complete when all of the below are true:
- trades can be triggered without Sheets dependency
- every trade is tied to a strategy, variant, regime tag, thematic cluster, engine, and evidence trail
- every candidate decision is logged whether traded, watched, or rejected
- both paper-fill and conservative-fill P&L are tracked
- every external call hits cost tracking
- Alpaca paper positions reconcile daily to SQLite and reporting surfaces
- three strategies can operate autonomously in paper
- weekly evaluation can rank strategies and trusted sources
- component attribution can separate signal, management, and sizing contribution
- the system can pause itself safely on defined failures

## 20. What I would cut from immediate scope

For the first implementation pass, I would cut or defer:
- broad Reddit ingestion
- Discord/X automation APIs
- large-scale variant auto-generation
- autonomous high-conviction thesis execution
- heavy regime-driven trade filtering
- too many simultaneous opportunistic strategies
- any stale document or policy that conflicts with this DB-first design
- ambiguous language suggesting SQLite or Sheets are interchangeable as canonical storage

## 21. Canonical docs after cleanup

After cleanup, the active document set should be small and explicit.

Keep as active:
- `TRADING_SYSTEM_V1_ARCHITECTURE.md`
- `EXECUTION_STATE_MACHINE.md`
- `COST_TRACKER_SPEC.md`
- `PASTE_IN_SIGNAL_SPEC.md`
- `config/risk.yaml`
- `config/strategies.yaml`
- `sql/SQLITE_SCHEMA_V1.sql`
- `PHASE_II_PLAN.md`
- `AUTONOMOUS_PAPER_TRADING_POLICY.md`
- `MONDAY_OPEN_RUNBOOK.md`

Keep as legacy/reference unless later merged or deleted:
- `QUANT_DEV_SPEC.md`
- `SOFTWARE_DEV_SPEC.md`
- `INTRADAY_ALPHA_INFRA_REQUEST.md`
- `CLEANUP_CANDIDATES.md`

Nothing stale should remain active by accident.

## 22. Immediate next docs to create after this one

1. `config/cost_rates.yaml` with draft pricing and essentiality tags for every vendor currently in use
2. optional `DOC_INDEX.md` to mark active vs legacy docs if the directory keeps growing
3. optional `LEGACY_NOTES/` archive folder if we want old design docs retained but out of the way

## 23. Bottom line

The best path is not to fix the current desk post format first.

The best path is to build a narrow, auditable, DB-first paper trading system with:
- three live strategies
- first-class evidence tracking
- candidate-decision logging for false-negative and false-positive learning
- hard cash hurdle discipline
- thematic-cluster and concentration controls
- dual P&L tracking to discount paper-fill illusion
- component attribution across signal, management, and sizing
- deterministic replay and evaluation

That gets you a real learning machine instead of a more complicated spreadsheet workflow.

