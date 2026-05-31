# Scripts Policy

Purpose: prevent script bloat and make script usage auditable.

## Trail Requirement

Every script run should leave a trail in:
- `~/.openclaw/logs/script-runs.jsonl`

Use the traced runner for manual/scripted execution:

```bash
~/.openclaw/scripts/run-with-trace.sh <script> [args...]
```

Examples:

```bash
~/.openclaw/scripts/run-with-trace.sh ~/.openclaw/scripts/safe-restart.sh --reason maintenance
~/.openclaw/scripts/run-with-trace.sh --tag audit ~/.openclaw/scripts/audit_telegram_routing.py
```

## Inventory / Bloat Audit

Run inventory report:

```bash
python3 ~/.openclaw/scripts/audit_script_inventory.py --stale-days 45
```

JSON output for automation:

```bash
python3 ~/.openclaw/scripts/audit_script_inventory.py --stale-days 45 --json
```

## Deletion Rule

Do not delete based only on age.

Delete only when all are true:
1. Script is stale candidate in inventory.
2. Not referenced by active docs/config/cron/hooks.
3. No active operational owner depends on it.
4. Replacement path exists or script is truly obsolete.

## Existing Tracking Note

Some subsystems already track run state (for example, cron job metadata), but that is not universal script-level tracking. The traced runner above is the canonical cross-script trail.
