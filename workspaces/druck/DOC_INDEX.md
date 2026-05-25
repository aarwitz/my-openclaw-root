# Trading System Docs Index

Status: active doc map for the autonomous paper-trading redesign

## Active docs

Architecture and operating specs:
- `TRADING_SYSTEM_V1_ARCHITECTURE.md`
- `EXECUTION_STATE_MACHINE.md`
- `AUTONOMOUS_PM_OPERATING_MODEL.md`
- `COST_TRACKER_SPEC.md`
- `PASTE_IN_SIGNAL_SPEC.md`

Configs and schema:
- `config/risk.yaml`
- `config/strategies.yaml`
- `sql/SQLITE_SCHEMA_V1.sql`

Existing operating authorities still in force:
- `PHASE_II_PLAN.md`
- `AUTONOMOUS_PAPER_TRADING_POLICY.md`
- `MONDAY_OPEN_RUNBOOK.md`

## Legacy or reference docs

Keep for context, but do not treat as current source of truth for the redesign unless merged explicitly:
- `QUANT_DEV_SPEC.md`
- `SOFTWARE_DEV_SPEC.md`
- `INTRADAY_ALPHA_INFRA_REQUEST.md`
- `CLEANUP_CANDIDATES.md`

## Notes

- SQLite is the canonical state store in the redesign.
- Google Sheets are reporting and review surfaces only.
- The old `DRUCK_PRE_MARKET` cron is legacy and should remain disabled until replaced by the checkpoint-driven PM loop.
- If two docs conflict, prefer the active docs list above.
- Delete or archive stale docs rather than leaving them ambiguous.
