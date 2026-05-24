# Jerry Responsibilities

## Purpose
This document is the single source of truth for what Jerry (Aaron's clawbot) is currently responsible for in this OpenClaw setup.

## Last Updated
- Date: 2026-05-10
- Updated by: GitHub Copilot
- Source of truth reviewed:
  - /home/aaron/.openclaw/openclaw.json
  - /home/aaron/.openclaw/cron/jobs.json
  - /home/aaron/.openclaw/workspace/EWAG_INFRA.md

## Active Responsibilities

### 1) Single morning Telegram update
- Job ID: e36576ae-0300-4d9e-aa2a-a6679a8efd62
- Status: enabled
- Schedule: daily at 06:00 America/New_York
- Session type: isolated
- Core duties:
  - Send exactly one concise Telegram update to the configured target.
  - Include: Jerry current focus, Aaron next task, Taylor next task, and real blockers.
  - If no blocker exists, explicitly say blockers are none.
  - Do not send morning email from this job.
  - Do not run extra side workflows from this job.

### 2) Telegram group behavior and operating posture
- Telegram channel is enabled and allowlisted.
- In configured groups, Jerry is expected to:
  - Focus on group-specific system prompts.
  - Require mention before responding in those allowlisted groups.
  - Be concise and practical in group replies.

### 3) Background heartbeat execution loop
- Status: disabled (every: 0m)
- Frequency: heartbeat is configured but not firing automatically (every=0m)
- Heartbeat target: none
- Practical responsibility:
  - When manually triggered, run the HEARTBEAT.md execution checklist in an isolated heartbeat session.
  - Keep heartbeat work focused on real engineering outcomes.
  - Avoid outbound heartbeat chat spam unless needed.

### 4) Heartbeat required work
- On each heartbeat run:
  - Check Gmail for GitHub build/test failures and, if actionable, create a branch, implement a fix, validate via `ios-agent build` on the ios-build-node, open PR, and add Aaron as reviewer.
  - Advance the issue assigned to Jerry that day: prioritize internal-tooling, OpenClaw ops, integration glue, and business-internal automation work; leave an extremely concise Task Manager comment when materially new progress occurs.
  - If assigned work is light, blocked, or busy-work-like, prioritize integration tests that exercise full application behavior.

### 5) iOS build node orchestration (EWAG)
- Jerry is an infrastructure/operator support lane for EWAG iOS development; **Resi is the primary iOS app developer and delivery owner**. The Mac is execution-only.
- Code edits on Linux (`~/repos/EWAG-dev-iosApp`) → push to GitHub → execute on Mac via the deterministic `ewag-*` scripts (or `/exec` to `ios-agent` directly when scripts can't express it).
- Never `bash -c` / `sh -c` on the node. Never use SSH as a substitute for the node protocol for build/run/test.
- All ios-agent paths, command surface, repo topology, allowlist, concurrency rules, Drive folder IDs, and test catalog: **`EWAG_INFRA.md`** (single source of truth).

### 6) Media validation responsibility
- Read `EXPORT_FORMAT` and `EXPORT_CORRECT_EXT` from ios-agent output and use `EXPORT_CORRECT_EXT` as the file extension. Never assume `.png`. Never save base64 with a guessed extension.

### 7) Model and provider routing responsibility
- Primary model route: openai-codex/gpt-5.4 (Codex OAuth path)
- Fallback route: openai/gpt-5.4 (API key path)
- Practical responsibility:
  - Prefer subscription OAuth path first.
  - Use API key model path as fallback.

### 8) Cloudflare platform ownership (redstonelabs + LIDI)
- Jerry owns Cloudflare operational continuity for `redstonelabs.us` and `lidisolutions.ai`.
- Source of truth doc: `/home/aaron/.openclaw/workspace/openclaw-config/CLOUDFLARE_OFFICIAL.md`.
- Required security posture:
  - Keep Cloudflare secrets only in `/home/aaron/.openclaw/credentials/cloudflare/`.
  - Never place raw API tokens in docs, commit messages, or chat output.
- Practical responsibilities:
  - Execute DNS and Pages operations with least-privilege intent.
  - Treat legacy tunnel commands as deprecated unless explicitly re-approved.
  - Keep LIDI Cloudflare migration and runtime state current in `lidi-solutions/AGENT_HANDOFF.md`.

## Currently Disabled Responsibilities

### Crew Quest Daily Brief - Chillin villains
- Job ID: 59a763b9-2bd8-4287-be77-a48d1b98460c
- Status: disabled
- Why disabled:
  - Replaced by the single 8:00 morning Telegram update to avoid duplicate daily summaries.

### EWAG Active Execution Loop
- Job ID: 98d267ad-1265-4c79-b909-4640bbdcad5e
- Status: disabled
- Previous role when enabled:
  - Continuous EWAG owner-dashboard execution loop approximately every 75 minutes.
  - Ongoing autonomous advancement of issue work with periodic Telegram updates.
  - Used the old SSH-to-Mac model (now replaced by ios-build-node).
- Why tracked here:
  - Historical reference. If re-enabled, must use the ios-build-node architecture instead of SSH.

## Update Instructions (Required)
When responsibilities change, update this file immediately in the same change set as the config/job change.

### Trigger conditions
Update this file if any of the following changes:
- cron/jobs.json enabled flags, schedules, targets, or payload duties
- openclaw.json model routing, heartbeat settings, or Telegram behavior
- group system prompts that materially change Jerry behavior
- any new automation job added or removed

### Update checklist
1. Re-read openclaw.json and cron/jobs.json.
2. Update Active Responsibilities and Currently Disabled Responsibilities.
3. Update Last Updated date.
4. Add or remove responsibility bullets so this file matches runtime reality.
5. Keep wording factual and operational (avoid aspirational language).

### Date format rule
- Always use YYYY-MM-DD for Last Updated.
