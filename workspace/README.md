# OpenClaw Workspace Repo

This repository tracks the **human-curated workspace** for Aaron's OpenClaw setup.

## Why the repo root is `workspace/` and not `~/.openclaw/`

`~/.openclaw/` contains a mix of:
- durable docs/config worth preserving
- sensitive secrets and tokens
- volatile runtime state
- device pairing / identity data
- logs, queues, and local databases

To avoid accidentally committing secrets or noisy machine-local state, the git repo is rooted at:

- `~/.openclaw/workspace`

## What is tracked

Examples:
- assistant identity / behavior docs
- workspace memory markdown files
- curated plans, audits, and notes
- selected skill files
- sanitized admin/config snapshots in `openclaw-config/`

## What is intentionally not tracked

Examples:
- `credentials/`
- device identity / pairing state
- logs
- delivery queue
- sqlite databases
- live tokens / API keys
- temporary scratch files
- nested cloned repos / vendored working trees

## Repo layout

- `AGENTS.md`, `IDENTITY.md`, `SOUL.md`, `USER.md`, `TOOLS.md`, `HEARTBEAT.md`
  - core assistant operating docs
- `MEMORY.md`, `memory/`
  - durable memory + daily logs
- `CONTRIBUTING.md`
  - bot contribution model and branch conventions
- `ELITE_KANBAN.md`, `ELITE_PROJECT_BRIEF.md`, `EWAG_DEV_BUILD_INFRA_GUIDE.md`
  - active EWAG project docs
- `GOOGLE_SETUP.md`
  - Google Workspace / gog OAuth setup reference
- `people/`
  - person-specific routing / delegation context
- `skills/`
  - local custom skills
- `openclaw-config/`
  - sanitized snapshots of higher-level OpenClaw config

## Safe config snapshots

`openclaw-config/` contains redacted copies of selected items from `~/.openclaw/`, such as:
- sanitized `openclaw.json`
- local CLI guide
- safe cron declarations

## Git hygiene rules

Before committing changes:
- do not add secrets, tokens, passwords, or private keys
- do not commit raw runtime state from `~/.openclaw/`
- prefer sanitized exports when higher-level config should be preserved
- keep nested repos ignored unless there is a deliberate vendoring decision
