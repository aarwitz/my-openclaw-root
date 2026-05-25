---
name: openclaw-ops
description: Manage OpenClaw gateway operations, configuration changes, health checks, reliability, and safe restarts. Use when editing openclaw.json, validating plugin/channel status, applying config updates, handling auth/token safety, or restarting the gateway safely.
metadata: {"clawdbot":{"emoji":"🛠️"}}
---

# OpenClaw Operations

This skill defines how Jerry manages OpenClaw safely on the RSL Linux host.

## Critical Restart Policy

1. Do not run systemctl restart for OpenClaw.
2. Do not use forceful restarts for routine config changes.
3. OpenClaw supports hot reload for config updates in normal operation.
4. If a restart is truly required, use only:

```bash
/home/aaron/.openclaw/scripts/safe-restart.sh
```

Why: some providers use fragile refresh-token flows. Unsafe restarts can corrupt auth sessions.

## Default Workflow For Config Changes

1. Edit config file or use config-set command.
2. Gateway auto-applies via hybrid reload (hot-reloads safe changes, auto-restarts only for gateway infrastructure changes like port/bind/auth).
3. Validate with health checks.
4. If hybrid reload didn't pick it up, use safe-restart.sh.

Preferred checks:

```bash
openclaw gateway status
openclaw status --deep
openclaw health
```

## Allowed vs Disallowed Commands

Allowed:

```bash
openclaw gateway status
openclaw status --deep
openclaw health
openclaw config set <path> <value>
/home/aaron/.openclaw/scripts/safe-restart.sh
```

Disallowed:

```bash
systemctl --user restart openclaw
systemctl restart openclaw
kill -9 <openclaw-pid>
```

## Incident Pattern

When OpenClaw behavior appears broken:

1. Run status and health checks.
2. Confirm credentials are still valid (use credential-preflight if relevant).
3. Verify recent config edits and expected hot reload behavior.
4. Only then run safe restart script.
5. Re-run health checks and confirm channels/plugins recovered.

## Response Style

- Be concise and operational.
- Provide exact commands and expected verification checks.
- For risky actions, explicitly state risk and safer alternative.
