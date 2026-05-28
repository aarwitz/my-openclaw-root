# 05 — Implementation Policy

Status: active. Schedules, approved actions, deferred features, build phases, and validation gates. Implements `01_OPERATING_AUTHORITY.md` and `02_ARCHITECTURE.md`.

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
- Any data source not declared in `02_ARCHITECTURE.md` section 6.

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

- `09:00` pre-market decision pass.
- `11:00` confirmation / invalidation pass.
- `13:30` replacement / rotation pass.
- `15:30` close-risk pass.
- Event-driven on Alpaca order or position events.

Archivist:

- Daily at `18:30` for any hypotheses resolved that day.
- Weekly Sunday `09:00` for portfolio-level pattern extraction.

These are baseline cadences. Each agent may run additional event-driven jobs.

## 4. OpenClaw runtime surfaces

- Standing orders authorize each recurring job above.
- Cron supplies the timing.
- Task Flow wraps multi-step pipelines (pre-market thesis build, intraday confirmation, post-close audit).
- Plugins are not added unless a gap is documented in `DECISION_LOG.md`.

## 5. Logging and audit

- Every state transition writes an `audits` row.
- Every Alpaca order writes an `orders` row and an `audits` row referencing it.
- Long-form reasoning goes to per-agent journals under `~/.openclaw/state/journals/<agent>/YYYY-MM-DD.md` and is referenced by `audits.journal_ref`.
- Weekly audit job (Sunday `08:00`) emits a portfolio summary into the trader's Telegram thread.

## 6. Build phases and gates

Phase 1 — Schema and validation:

1. `sql/schema.sql` instantiates without errors and creates the empty `~/.openclaw/state/trading-intel.sqlite`.
2. Manually validate the researcher reasoning chain on at least 10 historical cases (e.g., NVDA 2019, Novo 2020, Uranium 2016, ASTS 2021, Cheniere 2018, plus five wrong-thesis cases) using only data available at the time. Document each in `archives/validation/`.
3. Gate: do not proceed until 8 of 10 cases pass.

Phase 2 — Infrastructure:

4. Wire Alpaca paper account read/write through the existing skill.
5. Implement Tier-0 data connectors (SEC EDGAR, FRED, ClinicalTrials.gov, EIA, USAspending, BLS, USGS, USPTO, arXiv).
6. Each connector writes `hypothesis_evidence` with full provenance fields.

Phase 3 — Agents:

7. Researcher background job populates and updates hypotheses.
8. Quant scoring, regime, expression candidates, and sizing recommendations.
9. Critic challenges and reviews; calibrate against the 10 historical cases.
10. Trader executes only after critic reviews and reconciles every checkpoint.
11. Archivist runs daily resolution sweep and weekly pattern extraction.

Phase 4 — Calibration:

12. Two-week dry run: trader emits intents but does not submit to Alpaca.
13. Grade intents against actual market movement.

Phase 5 — Live paper:

14. Enable Alpaca submission.
15. Run unmodified for 90 days while archivist accumulates patterns.

## 7. Validation gates (must pass before each phase)

- Schema gate: all required test cases in `03_EXECUTION_STATE_MACHINE.md` section 10 pass against the running DB.
- Reasoning gate: 8 of 10 historical cases pass.
- Critic calibration gate: critic approves at least 80% of historically correct theses and flags at least 80% of historically wrong theses.
- Reconciliation gate: 5 consecutive end-of-day reconciliations with zero unresolved divergences.

## 8. Pre-flight checklist for live paper

- Telegram routing: account `druck` → agent `trader`. No other Telegram bindings for trading agents.
- `tools.agentToAgent.allow` includes `researcher`, `quant`, `critic`, `archivist`, `trader`.
- Cron jobs enabled for the schedules in section 3.
- Daily SQLite backup configured.
- `system_pauses` table empty (no leftover pauses from testing).

## 9. Change control

- Any change to authority docs requires an entry in `DECISION_LOG.md`.
- Any new active doc requires updating `DOC_INDEX.md` and confirming the active set stays at five.
