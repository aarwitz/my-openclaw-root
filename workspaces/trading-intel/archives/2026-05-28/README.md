# Archive — 2026-05-28 Canonical Reset

This folder preserves docs and prototypes superseded by the canonical root at `/home/aaron/.openclaw/workspaces/trading-intel/`.

## `druck/`

Trading-system docs that lived at `/home/aaron/.openclaw/workspaces/druck/` and were the previous architecture authority. They are retained here for historical context only. **Do not consult these as authoritative.** The successor docs are:

| Archived | Replaced by |
|---|---|
| `MULTI_AGENT_TRADING_SYSTEM_V2.md` | `docs/02_ARCHITECTURE.md` |
| `OPENCLAW_AGENT_TOPOLOGY_V2.md` | `docs/02_ARCHITECTURE.md` |
| `AGENT_JOBS_V2.md` | `docs/05_IMPLEMENTATION_POLICY.md` |
| `AUTONOMOUS_PM_OPERATING_MODEL.md` | `docs/01_OPERATING_AUTHORITY.md` |
| `AUTONOMOUS_PAPER_TRADING_POLICY.md` | `docs/01_OPERATING_AUTHORITY.md` |
| `EXECUTION_STATE_MACHINE.md` | `docs/03_EXECUTION_STATE_MACHINE.md` |
| `ATS_V6_IMPLEMENTATION_NOTES.md` | (rolled into `docs/05_IMPLEMENTATION_POLICY.md`) |

Hard-deleted as low-value at the same time: `TRADING_SYSTEM_V1_ARCHITECTURE.md`, `MONDAY_OPEN_RUNBOOK.md`, `COST_TRACKER_SPEC.md`.

## `workspace-trader-prototype/`

The Pydantic typed-state prototype that lived at `/home/aaron/.openclaw/workspace/trader/`. The canonical state store is now SQLite per `docs/04_SHARED_STATE_SCHEMA.md` and `sql/schema.sql`. Type mappings from this prototype into the SQLite schema are documented in `docs/04_SHARED_STATE_SCHEMA.md` section 5.

The prototype is preserved here as reference. The original location at `/home/aaron/.openclaw/workspace/trader/` is retained until the new state store is wired in; remove it after Phase 1 validation passes (see `docs/05_IMPLEMENTATION_POLICY.md` section 6).
