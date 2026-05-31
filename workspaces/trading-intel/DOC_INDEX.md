# Trading Intelligence — Doc Index

Status: active doc map for the canonical 5-agent OpenClaw trading system.
Effective: 2026-05-28.

## Active docs (the only authoritative set)

1. `docs/01_OPERATING_AUTHORITY.md` — what we trade, why, with what limits.
2. `docs/02_ARCHITECTURE.md` — 5 agents, workspaces, routing, shared state, hot/cold paths.
3. `docs/03_EXECUTION_STATE_MACHINE.md` — hypothesis, trade intent, order, position lifecycles and gates.
4. `docs/04_SHARED_STATE_SCHEMA.md` — canonical entity model and SQLite contract.
5. `docs/05_IMPLEMENTATION_POLICY.md` — schedules, runtime controls, build phases, validation gates.

## Reference

- `sql/schema.sql` — canonical DDL for the shared state store.
- `sql/seed_bootstrap.py` — idempotent loader for canonical seed rows.
- `sql/seeds/regime_rules.json` — active deterministic regime classifier thresholds (loaded into `regime_rules`).
- `reference/regime_rules.md` — narrative spec for the live regime classifier.
- `reference/validation_corpus/` — staging area for validation cases (seeds + cases + index).
- `../researcher/skills/reasoning_chain.md` — active 8-question researcher reasoning chain.
- `DECISION_LOG.md` — every retire/keep/merge decision with rationale.
- `OPERATOR_GUIDE.md` — Telegram command reference and daily workflow for the human operator.
- `HUMAN_USE_GUIDE.md` — best-practice patterns for humans or external systems interfacing with Druck.
- `archives/` — date-stamped retirement of superseded docs and prototypes.

## Legacy decommissioned

Legacy druck workspace state has been removed. This stack is the only active trading-system authority as of 2026-05-28.

## Hard rules

- If two docs disagree, the lower-numbered active doc wins, then `sql/schema.sql`.
- New docs must be approved into `DOC_INDEX.md` before any agent treats them as authoritative.
- Active authority stack is capped at 5 documents. Anything else lives as reference or archive.
