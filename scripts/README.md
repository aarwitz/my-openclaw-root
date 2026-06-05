# Scripts Policy (Canonical)

This is the single source of truth for how scripts are organized, used, and
retired in `~/.openclaw`. Every agent and every human should follow it.

## Container model (why this matters)

- `~/.openclaw/scripts/` is **shared** across every OpenClaw agent. All agents
  (main, druck, dwight, resi, trader, …) run inside the **single
  `openclaw-gateway` container**, which bind-mounts the entire `~/.openclaw`
  tree. There is **no per-agent copy**.
- Other containers (`dwight-taskmanager`, `lidi-task-manager-ewag-agent-*`) do
  **not** mount `~/.openclaw`. They run app code, not OpenClaw ops scripts,
  and are explicitly **exempt** from this policy.
- Result: edits to a script in `~/.openclaw/scripts/` take effect for every
  agent immediately. The risk is bloat, drift, and untraced ad-hoc work — this
  policy is the control.

## Registry

`~/.openclaw/scripts/policy.json` is the source of truth for **which
directories are governed**. The lint, audit, and scaffolder all read it.

- `governedDirs[]` — directories whose `.sh`/`.py` files MUST source the
  wrapper guard.
- `exemptDirs[]` — documented out-of-scope directories (e.g. app code in
  isolated containers).
- `exemptScripts[]` — individual files that legitimately cannot self-guard
  (currently only the wrapper itself).

To add a new governed directory: edit `policy.json`, then run the lint.

## Mandatory wrapper

Every governed `.sh` / `.py` file must begin with the wrapper guard:

```bash
# bash
#!/usr/bin/env bash
source "/home/aaron/.openclaw/scripts/lib/require-wrapper.sh"
set -euo pipefail
```

```python
# python
#!/usr/bin/env python3
import sys
sys.path.insert(0, "/home/aaron/.openclaw/scripts/lib")
from require_wrapper import require_wrapper
require_wrapper()
```

The guard rejects direct invocation (exit 126) and forces all runs through
`run-with-trace.sh`, which writes a JSONL trail to
`~/.openclaw/logs/script-runs.jsonl`.

## Running scripts

```bash
~/.openclaw/scripts/run-with-trace.sh <script> [args...]
~/.openclaw/scripts/run-with-trace.sh --tag <category> <script> [args...]
```

Common tags: `manual`, `audit`, `hook`, `cron`, `verify`, `test`.

## Day-to-day commands

| Task | Command |
|------|---------|
| Create a new governed script | `~/.openclaw/scripts/new-script.sh <name>.{sh,py} [target-dir]` |
| Lint (enforce wrapper coverage) | `~/.openclaw/scripts/scripts-policy-lint.sh` |
| Weekly lint runner (for cron) | `~/.openclaw/scripts/cron-scripts-policy-lint.sh` |
| Bloat inventory report | `~/.openclaw/scripts/run-with-trace.sh --tag audit ~/.openclaw/scripts/audit_script_inventory.py --stale-days 45` |
| JSON inventory (for automation) | `… audit_script_inventory.py --stale-days 45 --json` |

The scaffolder bakes in the wrapper guard automatically. Prefer it over hand-
written boilerplate so new scripts never drift from the policy.

## Scheduled enforcement

Weekly lint is installed in user crontab:

```cron
17 3 * * 0 /home/aaron/.openclaw/scripts/run-with-trace.sh --tag cron /home/aaron/.openclaw/scripts/cron-scripts-policy-lint.sh >> /home/aaron/.openclaw/logs/cron-scripts-policy-lint.log 2>&1
```

What it does:

- Runs wrapper coverage lint through `run-with-trace.sh`.
- Appends trace records to `~/.openclaw/logs/script-runs.jsonl`.
- Writes a structured policy-audit line to `~/.openclaw/logs/script-audit.jsonl`.
- Returns non-zero when violations exist, so failures are visible in cron logs.

## Deletion rule

Do **not** delete based on age alone. Delete only when **all** are true:

1. Script is a `deletion_candidate` in the inventory (stale **and** zero
   external references).
2. Not referenced by active docs, config (`openclaw.json`), cron, or hooks.
3. No active operational owner depends on it.
4. A replacement exists, or the script is truly obsolete.

Always use the trace runner for the deletion attempt as well so the
retirement leaves a trail.

## Architecture overview

```
~/.openclaw/scripts/
├── policy.json                  # registry (single source of truth)
├── README.md                    # this file
├── run-with-trace.sh            # the only legitimate entry point
├── lib/
│   ├── require-wrapper.sh       # bash guard sourced by every script
│   └── require_wrapper.py       # python equivalent
├── scripts-policy-lint.sh       # CI/pre-commit gate
├── cron-scripts-policy-lint.sh  # scheduled lint + JSON audit emitter
├── new-script.sh                # scaffolder (wrapper baked in)
├── audit_script_inventory.py    # bloat report across governed dirs
└── *.sh / *.py                  # governed ops scripts
```

```
Agent / hook / cron / human
        │
        ▼
   run-with-trace.sh ───► appends to logs/script-runs.jsonl
        │
        ▼
  governed script  (require-wrapper guard rejects direct calls)
        │
        ▼
audit_script_inventory.py   uses last-run timestamps + external ref count
        │                   to flag stale + deletion candidates
        ▼
scripts-policy-lint.sh      fails CI when a governed file lacks the guard
```

## Discoverability for agents

Each agent workspace's `TOOLS.md` includes a short "Scripts policy" section
pointing at this README. When an agent needs to run anything in
`~/.openclaw/scripts/` (or write a new helper), it should:

1. Use the scaffolder, never hand-write a new script.
2. Run scripts via `run-with-trace.sh`.
3. Refuse to run a governed script that is missing the wrapper guard; fix the
   script (or mark it exempt with a reason) first.
