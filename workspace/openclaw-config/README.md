# OpenClaw Config Snapshot

This directory contains safe, versionable snapshots of higher-level OpenClaw configuration.

Runtime source of truth:
- Active runtime config path: `~/.openclaw/openclaw.json`
- Files in this directory are snapshots/reference only and are not read by the OpenClaw gateway at runtime.

Included:
- `openclaw.sanitized.json` — redacted export of the live `~/.openclaw/openclaw.json`
- `cron.jobs.json` — current cron job declarations when safe to share
- `CLOUDFLARE_OFFICIAL.md` — Cloudflare operational source of truth for Jerry

Excluded on purpose from git:
- credentials
- tokens / API keys
- device identity / pairing state
- logs
- delivery queue
- sqlite databases
- update offsets / command hashes
- backups and other volatile runtime state
