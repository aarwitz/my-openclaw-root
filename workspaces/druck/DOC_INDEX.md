# SUPERSEDED — Redirect

**This index and the trading docs that lived in `/workspaces/druck/` are superseded as of 2026-05-28.**

The authoritative trading-system docs now live under:

- `/home/aaron/.openclaw/workspaces/trading-intel/DOC_INDEX.md`
- `/home/aaron/.openclaw/workspaces/trading-intel/docs/01_OPERATING_AUTHORITY.md`
- `/home/aaron/.openclaw/workspaces/trading-intel/docs/02_ARCHITECTURE.md`
- `/home/aaron/.openclaw/workspaces/trading-intel/docs/03_EXECUTION_STATE_MACHINE.md`
- `/home/aaron/.openclaw/workspaces/trading-intel/docs/04_SHARED_STATE_SCHEMA.md`
- `/home/aaron/.openclaw/workspaces/trading-intel/docs/05_IMPLEMENTATION_POLICY.md`
- `/home/aaron/.openclaw/workspaces/trading-intel/sql/schema.sql`

## What happened

- The 4-agent topology was replaced by a 5-agent topology (`researcher`, `quant`, `critic`, `archivist`, `trader`).
- Options vehicles are now first-class.
- Canonical SQLite schema moved to `/workspaces/trading-intel/sql/schema.sql`.
- `MULTI_AGENT_TRADING_SYSTEM_V2.md`, `OPENCLAW_AGENT_TOPOLOGY_V2.md`, `AGENT_JOBS_V2.md`, `AUTONOMOUS_PM_OPERATING_MODEL.md`, `AUTONOMOUS_PAPER_TRADING_POLICY.md`, `EXECUTION_STATE_MACHINE.md`, `ATS_V6_IMPLEMENTATION_NOTES.md` are archived at `/workspaces/trading-intel/archives/2026-05-28/druck/`.
- `TRADING_SYSTEM_V1_ARCHITECTURE.md`, `MONDAY_OPEN_RUNBOOK.md`, `COST_TRACKER_SPEC.md` were hard-deleted as low-value.
- `PASTE_IN_SIGNAL_SPEC.md`, `config/`, `phase2/`, `sql/SQLITE_SCHEMA_V1.sql` remain in place as reference implementations until the new schema is wired in.

## Druck agent

The `druck` agent definition remains in `openclaw.json` for migration safety, but the trading system no longer routes to it. The Telegram account `druck` is bound to the `trader` agent.
