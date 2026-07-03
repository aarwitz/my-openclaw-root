# TOOLS.md

## Purpose

Operational runbook for tool availability and auth health in this OpenClaw workspace.

## Runtime Verification

- Gateway runtime must resolve to system Node: `/usr/bin/node`.
- Source of truth is systemd `ExecStart`, not shell `node` resolution:
  - `systemctl --user show -p ExecStart openclaw-gateway.service`
- If wiring drifts:
  - `openclaw doctor --non-interactive --no-workspace-suggestions`
  - `openclaw doctor --fix`
- If restart is truly required, use only:
  - `bash /home/aaron/.openclaw/scripts/safe-restart.sh`

## Tool Status Snapshot

Current state from last full validation run (2026-04-15):

| Area | Status | What was verified | Notes |
|---|---|---|---|
| OpenClaw gateway runtime | PASS | `openclaw status --deep`, `openclaw health` | Gateway reachable and active. |
| Telegram channel plugin | PASS (runtime) / WARN (config display) | `openclaw status --deep` | Health reports Telegram OK for `@a_rslbot`. Channels table may still show `SETUP/no token` when token is env-injected. |
| Brave plugin load | PASS | `openclaw plugins inspect brave`, `openclaw plugins doctor` | Plugin is loaded with `web-search: brave` capability. |
| Brave live API probe | PASS | direct Brave API request returned HTTP 200 | Key is sourced from service environment (`BRAVE_API_KEY`), not from `openclaw.json`. |
| Google auth identity | PASS | `gog auth list --check --no-input` | Active default account: `aaronclawrsl@gmail.com`. |
| Gmail API | PASS | `gog gmail search 'newer_than:2d' --max 3 --no-input` | Returns live results. |
| Drive API | PASS | `gog drive search ... --max ... --no-input` | Returns live results. |
| Calendar API | PASS | `gog calendar calendars --max 5 --no-input` | Returns calendar list. |
| Contacts API | PASS | `gog contacts list --max 1 --no-input` | API responds (`No contacts` is a valid empty result). |
| Sheets API | PASS | create/update/get/delete temp sheet | End-to-end write/read verified; temp sheet permanently deleted. |
| Docs API (content ops) | PASS | create/insert/cat/delete temp doc | Docs API content operations now work; temporary test doc was permanently deleted. |
| People API profile | OPTIONAL | `gog people me --no-input` | Not required for core workflows; skipped to avoid OAuth localhost callback issues. |
| GitHub auth + repo access | PASS | `gh auth status`, `git remote -v`, collaborator permission API | Auth account `aaronclawrsl-bot`; repo remote correct; permission is `write`. |
| Credential artifacts present | PASS | `ls -l ~/.openclaw/credentials/...` | Expected files exist on disk. |

## Current Posture

- Functional coverage: 13/14 core items PASS (People profile check is optional).
- Security posture: `0 critical · 3 warn · 1 info`.
- Runtime hardening: `channels.telegram.groupPolicy = allowlist`.
- Production readiness: Yes.

## Repeatable Verification Checklist

Run before autonomous loops, screenshot-delivery flows, or external reporting.

Recommended one-shot preflight:

```bash
~/.openclaw/scripts/credential-preflight.sh
```

### OpenClaw + Infrastructure

- `openclaw status --deep`
- `openclaw health`
- `ss -ltnp | grep 18789`
- Local `workspace/ios-agent-v2.sh` is retired; canonical iOS execution remains on the Mac node via `/Users/taylorolsen-vogt/ios-agent/ios-agent` over SSH.
- `ssh -o BatchMode=yes -o ConnectTimeout=10 taylorolsen-vogt@100.125.133.123 'timeout 15 /Users/taylorolsen-vogt/ios-agent/ios-agent branch'`
- `ssh -o BatchMode=yes -o ConnectTimeout=10 taylorolsen-vogt@100.125.133.123 'echo OK'`
- `openclaw plugins inspect brave`
- `openclaw plugins doctor`

### Google (`gog`)

- `gog auth list --check --no-input`
- `gog gmail search 'newer_than:2d' --max 3 --no-input`
- `gog drive search 'owner:me' --max 3 --no-input`
- `gog calendar calendars --max 5 --no-input`
- `gog contacts list --max 1 --no-input`
- `gog people me --no-input`

### GitHub

- `gh auth status`
- `git remote -v`
- `gh api repos/aarwitz/workspace/collaborators/aaronclawrsl-bot/permission --jq .permission`

### Credential Files

- `ls -l ~/.openclaw/credentials/github_credentials.json ~/.openclaw/credentials/google_client_secret.json ~/.openclaw/credentials/telegram-pairing.json ~/.openclaw/credentials/telegram-default-allowFrom.json`

## Auth Drift Rules

- `google_client_secret.json` and `github_credentials.json` are source credentials, not live renewable sessions.
- For Google Workspace MCP, `~/.openclaw/credentials/google_client_secret.json` is required, but runtime also needs MCP account tokens (`google-workspace-mcp accounts add ...`).
- Run cheap read probes before write actions (Drive upload, Gmail send, calendar writes).
- On revocation/expired-session errors, refresh auth immediately and retry once before escalating.



## Scripts policy (shared across all OpenClaw agents)

All `.sh`/`.py` scripts under `~/.openclaw/scripts/` and registered workspace script dirs are governed by a single policy. Source of truth: `~/.openclaw/scripts/README.md`.

Rules for you:
- Run scripts via `~/.openclaw/scripts/run-with-trace.sh <script> [args...]` so the call is logged to `~/.openclaw/logs/script-runs.jsonl`. Direct invocation auto-reroutes through the wrapper by default; with `OPENCLAW_REQUIRE_WRAPPER_NO_AUTORUN=1` it exits `126`.
- Create new scripts with `~/.openclaw/scripts/new-script.sh <name>.{sh,py}` — never hand-write boilerplate.
- Before retiring a script, run `~/.openclaw/scripts/scripts-policy-lint.sh` and the inventory audit, and follow the deletion rule in the README.
