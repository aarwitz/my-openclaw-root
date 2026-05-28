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
- `DECISION_LOG.md` — every retire/keep/merge decision with rationale.
- `archives/` — date-stamped retirement of superseded docs and prototypes.

## Superseded (do not consult except as history)

All trading docs under `/home/aaron/.openclaw/workspaces/druck/` are superseded by this stack as of 2026-05-28. Archive copies are in `archives/` once migrated.

## Hard rules

- If two docs disagree, the lower-numbered active doc wins, then `sql/schema.sql`.
- New docs must be approved into `DOC_INDEX.md` before any agent treats them as authoritative.
- Active stack is capped at 5 documents. Anything else lives as reference or archive.
