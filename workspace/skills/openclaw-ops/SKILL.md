---
name: openclaw-ops
description: Deterministic OpenClaw operations router for config changes, health checks, and safe restart policy enforcement.
metadata: {"clawdbot":{"emoji":"🛠️"}}
---

# OpenClaw Ops Router (Lean + Deterministic)

Use this skill for gateway/runtime operations and configuration safety.

## Operation Table

| Operation | Deterministic Action | Fail-Closed Rule |
|---|---|---|
| Config update | edit config or use `openclaw config set` | if invalid, stop and revert the specific change |
| Runtime verification | run status/health checks after change | if checks fail, treat as unresolved |
| Restart decision | prefer hybrid/hot reload behavior first | no restart for routine config edits |
| Required restart | use `/home/aaron/.openclaw/scripts/safe-restart.sh` only | never use `systemctl restart` for OpenClaw |

## Critical Safety Rules

- Never run `systemctl --user restart openclaw` or force-kill gateway process.
- Assume token fragility for auth-backed providers.
- Validate after every operational change.

## Verification Commands

- `openclaw gateway status`
- `openclaw status --deep`
- `openclaw health`

## Incident Pattern

1. run status + health checks
2. verify credential preflight/recent config edits
3. perform safe restart only if needed
4. re-run checks and confirm recovery

## Output Contract

Return:
1. action taken
2. checks run + outcomes
3. any unresolved risk
4. next safe command

## On-Demand Deep Reference

- `workspace/skills/openclaw-ops/REFERENCE_FULL.md`
