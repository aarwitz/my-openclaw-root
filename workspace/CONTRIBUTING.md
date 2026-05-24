# Contributing

> **Scope:** This file applies to the **OpenClaw workspace config repo** (`aarwitz/workspace`), not the iOS app repo (`EWAG-dev/iosApp`). For the iOS app dev workflow, see `EWAG_INFRA.md`.

## Canonical ownership

- Primary repository: `aarwitz/workspace`
- Default branch: `main`
- Repository ownership stays with `aarwitz`

## Bot contribution model

The bot account (`aaronclawrsl-bot`) should contribute through **branches + pull requests**, not by owning the canonical repository.

Preferred flow:
1. Create a branch from `main`
2. Make the smallest useful change
3. Push the branch
4. Open a PR into `main`
5. Merge after review

## Branch naming

Suggested bot branch prefixes:
- `bot/docs/...`
- `bot/fix/...`
- `bot/chore/...`
- `bot/feat/...`

Examples:
- `bot/docs/repo-workflow`
- `bot/chore/gitignore-tighten`
- `bot/fix/memory-log-path`

## Guardrails

Do not commit:
- secrets, tokens, passwords, or private keys
- raw `~/.openclaw/` runtime state
- credentials, device identity, pairing state, logs, queues, or sqlite DBs
- temporary scratch files
- nested cloned repos unless intentionally vendored

When higher-level OpenClaw config should be preserved, prefer sanitized snapshots under `openclaw-config/`.

## Review expectations

Keep PRs:
- small
- reversible
- clearly titled
- scoped to a single purpose when possible

## Notes for future automation

If multiple threads are active at once:
- check for open PRs first
- avoid reusing another task's branch
- prefer one branch per task/thread
