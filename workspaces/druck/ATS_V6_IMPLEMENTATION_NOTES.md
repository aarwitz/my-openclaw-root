# ATS v6 Implementation Notes

Status: implementation companion for issues #120 and #125

## Table boundaries

- `signals` stores raw triggers and routing metadata before a trade thesis exists.
- `ideas` stores tradeable hypotheses after enrichment and corroboration.
- `trade_intents` stores rules-approved execution plans before broker submission.
- `trades` stores executed lifecycle records and trade-time environment snapshots.
- `candidate_decisions` stores every trade/watch/reject decision, including false positives and false negatives for later learning.
- `trade_attribution` stores nightly post-trade decomposition so signal quality, management quality, and sizing quality stay separate.
- `positions`, `portfolio_risk_snapshots`, `regime_states`, `order_events`, `reconciliation_runs`, and `system_pauses` support execution integrity and oversight.

## JSON vs normalized choices

- Keep `entry_conditions_json` and `data_quality_flags_json` on `trades` so the exact trade-time snapshot remains durable and replayable.
- Keep `score_components_json`, `factor_tags_json`, `regime_tag_json`, `targets_json`, and exposure breakdown fields in JSON for flexible evolution without premature table explosion.
- Normalize only entities that need lifecycle joins or primary-key accountability: signals, ideas, intents, trades, attribution, positions, and risk snapshots.
- Queryability comes from stable top-level columns plus JSON payload preservation. If a JSON key becomes operationally critical across many reports, promote it later into a first-class column with migration evidence.

## Config layout

- `config/risk.yaml` is the risk authority for startup mode, hard gates, correlation caps, and reporting cadence.
- `config/strategies.yaml` is the strategy/allocation authority for conviction vs opportunistic capital buckets and per-strategy limits.
- Runtime DB path: `sql/ats_v6.db`
- Runtime validation entrypoint: `python3 -m phase2.cli validate-ats-v6`

## Aaron-approved additions now encoded

- Risk modes: `seed`, `normal`, `aggressive`, with `seed` as startup default.
- Capital allocation baseline: conviction 60, opportunistic 30, cash reserve 10.
- Correlation controls: thematic cluster caps plus 90-day rolling correlation thresholds.
- Reporting expectations: weekly summary and monthly review fields live in risk config so later stories can automate them consistently.

## Validation path

- Issue #120 local proof: `python3 -m phase2.cli validate-ats-v6`
- Issue #125 expands the same command into a real pre-merge harness.
- Current harness now covers:
  - schema creation
  - signal -> idea -> intent -> trade path
  - one opportunistic trade fixture
  - one conviction trade fixture
  - candidate decision logging
  - nightly attribution rows
  - reconciliation and pause smoke rows
  - report/export-style aggregation preview

## Paper-fill realism note

- Validation proves lifecycle integrity and reporting integrity, not real-market fill realism.
- `paper_fill_pnl` and `conservative_fill_pnl` are both present in fixtures so later stories can test gap awareness honestly.
- A passing validation suite does not claim live-tradability or realistic broker slippage, only that the ATS v6 accounting and lifecycle surfaces are wired correctly.
