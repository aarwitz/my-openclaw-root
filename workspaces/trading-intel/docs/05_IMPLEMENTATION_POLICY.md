# 05 — Implementation Policy

Status: active. Schedules, approved actions, deferred features, build phases, and validation gates. Implements `01_OPERATING_AUTHORITY.md` and `~/.openclaw/SYSTEM_ARCHITECTURE.md` (canonical).

## 1. Approved actions at launch

- Long and short direct equity in Alpaca paper.
- ETF positions.
- LEAPS calls and call spreads consistent with `01_OPERATING_AUTHORITY.md`.
- Shorter-dated options for explicit catalyst windows.
- Pair trades and competitor shorts with the constraints in `01_OPERATING_AUTHORITY.md`.

## 2. Explicitly deferred at launch

- Live (non-paper) Alpaca account.
- Margin beyond Alpaca paper defaults.
- Crypto.
- Futures.
- Any data source not declared in `DATA_SOURCES.md` (catalog) / `~/.openclaw/SYSTEM_ARCHITECTURE.md` §6.

Lift restrictions only through a new `DECISION_LOG.md` entry approved by Aaron.

## 3. Recurring job schedule (all times US/Eastern)

Researcher:

- `06:30` core-source delta ingestion.
- `20:30` nightly hypothesis refresh.
- Event-driven on a major filing, trial, macro, or sector change.

Quant:

- `08:15` pre-market scoring and regime refresh.
- `12:45` intraday rerank.
- `16:20` after-close recalculation.
- On-demand when researcher flags material updates.

Critic:

- `08:35` high-priority hypothesis review.
- Before any `trade_intent` can move out of `critic_review`.
- `17:00` postmortem and error-review queue.

Trader:

- `09:00` pre-market orchestration and operator update pass.
- `09:30` market-open orchestration update pass.
- `11:00` confirmation / invalidation operator update pass.
- `13:30` replacement / rotation operator update pass.
- `15:30` close-risk operator update pass.

Executor:

- `09:00` pre-market execution decision pass.
- `09:30` market-open reaction pass.
- `11:00` confirmation / invalidation pass.
- `13:30` replacement / rotation pass.
- `15:30` close-risk pass.
- Event-driven on Alpaca order or position events.

Archivist:

- Daily at `18:30` for any hypotheses resolved that day.
- Weekly Sunday `09:00` for portfolio-level pattern extraction.

These are baseline cadences. Each agent may run additional event-driven jobs.

## 3.1 Executor execution timing policy

Executor timing behavior must follow `reference/execution_timing_framework.md`.

Operational defaults:
- Prefer resting broker-native limit or protective orders for already-approved price-sensitive setups instead of waiting for another checkpoint.
- Use scheduled passes to prepare, reassess, resize, or cancel execution plans rather than to justify reactive market chasing.
- Use Alpaca order and position events as the primary notification path after an order is staged.
- Cancel stale resting buy orders in the `15:30` ET close-risk pass unless the thesis explicitly supports overnight exposure at that level.

## 3.2 Whole-pipeline invocation policy

When operator intent is "run the whole pipeline" (or equivalent), trader must run the full sequence, in order:

1. researcher refresh/create hypotheses from fresh source pulls,
2. quant score + regime + expression updates,
3. critic challenge/review updates,
4. trader orchestration readiness and execution packet generation,
5. executor order staging/submit decision.

Fail-closed behavior:
- Trader may not silently collapse whole-pipeline requests into trader-only synthesis.
- Any blocked stage must be reported with stage id and concrete runtime/tool error.
- If execution is blocked but research/scoring/review can continue, continue those stages and report `no-submit` posture with executor blocked reason.

Execution-quality requirement:
- Any setup labeled actionable must include quote timestamp (`UTC` and `US/Eastern`), session label (`premarket`, `regular`, or `after-hours`), `% vs prior close`, `% vs post-event reference`, and `hold/extend/fade` reaction state.
- Missing quote-context fields force `watch_only` or `blocked_data` classification.

## 4. OpenClaw runtime surfaces

- Standing orders authorize each recurring job above.
- Cron supplies the timing.
- Task Flow wraps multi-step pipelines (pre-market thesis build, intraday confirmation, post-close audit).
- Plugins are not added unless a gap is documented in `DECISION_LOG.md`.

## 5. Logging and audit

- Every state transition writes an `audits` row.
- Every Alpaca order writes an `orders` row and an `audits` row referencing it.
- Long-form reasoning goes to per-agent journals under `~/.openclaw/state/journals/<agent>/YYYY-MM-DD.md` and is referenced by `audits.journal_ref`.
- Weekly audit job (Sunday `08:00`) emits a portfolio summary into Druck's Telegram thread.

## 6. Build phases and gates

Phase 1 — Schema and validation:

1. `sql/schema.sql` instantiates without errors and creates the empty `~/.openclaw/state/trading-intel.sqlite`.
2. Run leakage-resistant validation with anonymized case packets (no ticker/company/date identifiers), negative controls, and fake-date sensitivity checks. Store cases and outcomes in canonical state (`validation_cases`).
3. Add post-cutoff holdout cases that resolved after model training cutoff. Report in-training-window and post-cutoff metrics separately.
4. Gate: do not proceed unless post-cutoff performance and false-positive rate clear threshold on the anonymized set.

Phase 2 — Infrastructure:

5. Wire Alpaca paper account read/write through the existing skill.
6. Implement Tier-0 data connectors (SEC EDGAR, FRED, ClinicalTrials.gov, EIA, USAspending, BLS, USGS, USPTO, arXiv).
7. Each connector writes `hypothesis_evidence` with full provenance fields.
8. Implement fill-realism layer: slippage/spread model by vehicle and ADV bucket, and max-size caps by historical ADV fraction.

Phase 3 — Agents:

9. Researcher background job populates and updates hypotheses.
10. Quant scoring, deterministic regime classification, expression candidates, and sizing recommendations.
11. Critic challenges and reviews; calibrate on anonymized + post-cutoff validation cases.
12. Executor executes only after critic reviews, data-freshness gates, explainability thresholds, and reconciliation checks pass.
13. Archivist runs daily resolution sweep and weekly pattern extraction with external mechanism checks.

Phase 4 — Calibration:

14. Two-week dry run: executor emits intents but does not submit to Alpaca.
15. Grade intents against actual market movement and calibration quality (confidence vs realized outcomes).

Phase 5 — Live paper (90-day operational validation):

16. Enable Alpaca submission.
17. Run unmodified for 90 days to validate operations (execution, reconciliation, gating, data freshness).
18. Do not treat the 90-day run as edge proof. Any live-capital decision requires at least 30 resolved post-cutoff theses graded by Archivist.

## 7. Validation gates (must pass before each phase)

- Schema gate: all required test cases in `03_EXECUTION_STATE_MACHINE.md` section 10 pass against the running DB.
- Reasoning gate: anonymized + negative-control + fake-date suite clears threshold, with post-cutoff metrics reported separately and treated as primary evidence.
- Critic calibration gate: evaluated on the same anonymized + post-cutoff suite; report confidence intervals and false-positive rate.
- Reconciliation gate: 5 consecutive end-of-day reconciliations with zero unresolved divergences.
- Fill realism gate: every performance report includes both idealized and slippage-adjusted P&L; benchmark comparisons use slippage-adjusted series.
- Explainability gate: no intent reaches `submitted` without required thesis/falsifier/provenance/counterargument quality fields.

Default A1 threshold values (can be tightened later):

- Minimum sample: at least 30 post-cutoff resolved cases and 60 negative-control cases.
- Post-cutoff directional accuracy: >= 57%.
- Negative-control false-positive rate: <= 25%.
- Fake-date sensitivity: >= 95% conclusion invariance across fake-date variants.
- Confidence calibration: expected calibration error (ECE) <= 0.10 on resolved post-cutoff cases.
- Reasoning gate fail-closed rule: if minimum sample is not met, gate status is `fail`.

Validation corpus ingestion protocol:

- Researcher is the default producer of validation cases because it already sees source material first.
- Cases must be anonymized before storage: strip ticker, company name, deal name, date, and any direct mnemonic that could let the model memorize the answer.
- Each case must be labeled `winner`, `negative_control`, or `post_cutoff`, with optional `fake_date_variant` metadata.
- Every case must include a machine-readable model decision and a resolved outcome object before being counted as pass/fail evidence.
- `passed = true` means the model conclusion matched the resolved outcome under the anonymized representation and the fake-date variant did not flip the conclusion.
- Negative controls must intentionally produce no trade or a correctly blocked trade.
- Post-cutoff cases are weighted most heavily for go/no-go decisions.
- The validation runner writes one row per case into `validation_cases` and one audit row for the batch run.

Regime rules ingestion protocol:

- Quant owns regime classification and must write the current regime snapshot from the active deterministic rule set.
- A regime update is only valid if thresholds are recorded in `regime_rules` and the resulting `regime` row references the active rule configuration in audit metadata.
- Rule updates require a `DECISION_LOG.md` entry and refreshed `experiment_id` tags.
- Until the regime rule page is authored, default behavior is fail-closed to `neutral`/`caution`-style gating rather than inventing implicit thresholds.

## 8. Pre-flight checklist for live paper

- Telegram routing: account `druck` → agent `trader` (persona displayed to Aaron: Druck). No other Telegram bindings for trading agents.
- Executor (`executor`) is the only broker execution lane; trader never submits/cancels orders directly.
- Legacy `druck` agent-id collision resolved (legacy id removed or renamed) to prevent routing/operator ambiguity.
- `tools.agentToAgent.allow` includes `researcher`, `quant`, `critic`, `archivist`, `trader`, `executor`.
- Cron jobs enabled for the schedules in section 3.
- Daily SQLite backup configured.
- `system_pauses` table empty (no leftover pauses from testing).

## 9. Change control

- Any change to authority docs requires an entry in `DECISION_LOG.md`.
- Any new active doc requires updating `DOC_INDEX.md` and confirming the active set stays at five.
- Every policy/scoring/prompt change must create an `experiment_id` stamped onto downstream intents, audits, patterns, and postmortems.

## 10. Bootstrap operations and continuous corpus growth

- Initial operations are allowed in Alpaca paper before full validation corpus counts are reached, as long as all execution gates and reconciliation rules still pass.
- Initial operations do not change the edge-proof requirement: corpus thresholds in section 7 remain mandatory before treating results as robust edge evidence.
- Use a dual-lane workflow:
	- operations lane: real, specific thesis/execution data for live paper decisions.
	- evaluation lane: masked validation cases for leakage-resistant measurement.
- Continuous learning loop:
	1. export resolved hypothesis outcomes into a review queue,
	2. approve high-quality candidates,
	3. convert approved raw detailed cases into masked evaluation cases,
	4. re-run corpus validation.
