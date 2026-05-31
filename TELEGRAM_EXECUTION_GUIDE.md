# Telegram Human Interface Guide (Canonical)

Date: 2026-05-25
Status: authoritative
Audience: Aaron operating OpenClaw through Telegram.

## 1) Operating model

- Default mode: Telegram-first.
- Server CLI is only for maintenance/recovery.
- Human loop:
	1. Send precise intent.
	2. Agent executes.
	3. You approve/redirect.
	4. Agent closes the loop with evidence.

## 2) What is currently true in this deployment

- Telegram accounts are active for `jerry`, `dwight`, `resi`, `druck`.
- Allowed Telegram user IDs are allowlisted.
- Naming contract: the primary general bot account key is `jerry` and routes to agent id `main` (identity name Jerry). Transport account keys are not bot names.

Routing matrix (authoritative):
- Human-readable table: [workspaces/trading-intel/reference/TELEGRAM_ROUTING_MATRIX.md](workspaces/trading-intel/reference/TELEGRAM_ROUTING_MATRIX.md)
- Machine source: [workspaces/trading-intel/reference/telegram_routing_matrix.json](workspaces/trading-intel/reference/telegram_routing_matrix.json)

Surface distinction:
- DM: one-to-one with a bot account; no group chat id or topic id.
- Group: shared chat room identified by chat id.
- Topic: thread inside a forum-enabled group; topic ids are only meaningful within that chat id.
- Coding execution path is detached task routing with lanes:
	- `inline`
	- `codex-subagent`
	- `acp-external`
- Dwight has an explicit run contract for coding:
	- `run issue <id> --repo <abs-path> [--scope ... --expected-files ... --risk ... --acp-available ... --acp-agent ... --agent-timeout ...]`
- Dwight policy already says ready coding issues should be launched immediately when repo path is known.
- Task Manager is now the default deterministic launch source for ready code-backed issues via `auto_launch_enabled` + readiness fields.
- `run issue ...` remains the operator override when you want to force immediate launch manually.

## 3) Message format that gets best results

Use this shape in Telegram:

1. Objective: one sentence.
2. Inputs: links/attachments/constraints.
3. Done criteria: concrete acceptance checks.
4. Deadline and risk tolerance.
5. Output format you want.

Example skeleton:

```text
Objective: <what outcome you want>
Context: <relevant facts, links, files>
Constraints: <budget/time/risk/legal/style>
Done means: <tests, artifacts, decisions>
Deadline: <time>
Return: <bullet summary | table | draft message | task list>
```

## 4) High-leverage AI usage pattern

Use AI in this order:

1. Triage: summarize and classify incoming information.
2. Plan: produce options with tradeoffs.
3. Execute: run one chosen path with checkpoints.
4. Verify: require evidence and failure modes.
5. Log: push outcomes to Task Manager/docs.

This avoids "chat drift" and turns messages into measurable work.

## 5) Real-world playbooks

### A) New client sends info in Gmail

Send to Telegram (Jerry or Dwight):

```text
New client intake from Gmail.
Pull all messages from <client/email> in last <N> days.
Extract: stakeholders, goals, budget, deadlines, risks, open questions.
Produce: (1) 1-page brief, (2) proposed project plan, (3) reply draft.
Done means: I can forward the reply with minimal edits.
```

Best follow-up:
- "Create Task Manager issues for the top 5 actions and assign owner."

### B) Build a robot vision pipeline and grant SSH access

Send to Telegram (Dwight for orchestration):

```text
Start robot vision pipeline project.
Goal: camera ingest -> detection -> tracking -> event output.
Infra: provision SSH access to <host>, least-privilege user, key-based auth.
Need: architecture, repo bootstrap, test plan, deployment checklist, security checklist.
Done means: reproducible setup docs + first end-to-end demo script + rollback steps.
```

Then create coding run:

```text
run issue <tm_id> --repo <abs-path> --scope high --expected-files 12 --risk high --acp-available true
```

### C) Personal ops task (roof repair)

Send to Telegram (Jerry):

```text
Help me get roof repair done this week.
Collect local vendors, compare licensing/reviews/pricing/warranty/timeline.
Draft outreach messages, call script, and quote comparison sheet.
Output: ranked top 3 with recommendation and why.
```

Then:
- "Set reminder follow-ups and draft confirmation message for selected vendor."

## 6) Coding command contract (literal)

```text
run issue <id> --repo <absolute-path> [--scope low|medium|high] [--expected-files N] [--risk low|medium|high] [--acp-available true|false] [--acp-agent <id>] [--agent-timeout <seconds>]
```

Rules:
- Use Task Manager issue ID, not GitHub issue number.
- One line only.
- No trailing punctuation.
- Include absolute repo path.

## 7) Task Manager automation boundary

What should auto-start:
- repo-backed coding issues
- assigned to `Jerry`, `Resi`, `Druck`, or `Dwight`
- branch/repo/acceptance criteria present
- ready to execute without another human clarification loop

What should not auto-start:
- stories assigned to `Aaron`
- admin/planning-only work
- stories with no repo execution path
- work that requires external approval first

Target behavior:
- Dwight creates or updates the issue
- once the readiness contract is satisfied, execution starts automatically
- assigned agent owns implementation, tests, evidence, branch updates, Task Manager comments, and PR creation
- only one active `queued|launched` coding issue is allowed per executing agent by default
- merge/deploy stay approval-gated

## 8) What to request in every completion

- Outcome summary in 5 lines or less.
- Evidence (tests/build/checks/links).
- What changed.
- Remaining risks.
- Exact next action.

## 9) When to use server CLI

Only if unhealthy or blocked. Core commands:

```bash
openclaw health
openclaw gateway status
~/.openclaw/scripts/safe-restart.sh
~/.openclaw/scripts/test-coding-lane-regression.sh
TM_READY_WATCHER_ALLOW_EXECUTE=true ~/.openclaw/scripts/tm-ready-watcher.sh --execute --issue-id <tm_id>
~/.openclaw/scripts/tm-ready-launch-once.sh --issue-id <tm_id>
```

Never use `systemctl --user restart` for gateway restarts.

## 10) Failure recovery (fast)

If execution fails:

1. Re-check issue ID and absolute repo path.
2. Re-check Task Manager readiness fields: `assigned_to`, `repo_slug`, `branch`, acceptance criteria, and `auto_launch_enabled`.
3. Re-run with explicit scope/risk/expected-files if using manual override.
4. If ACP path fails at runtime, rely on codex-subagent fallback and request metadata path.
5. If still blocked, run deterministic CLI launcher from server.

Config regression guardrail:

```bash
python3 ~/.openclaw/scripts/audit_telegram_routing.py
```

## 11) Safe First Live Use

For controlled live verification of the auto-launch system on a real issue:

1. Ensure the issue is code-backed and `in_progress`
2. Ensure it has:
   - `assigned_to`
   - `repo_slug`
   - `branch`
   - acceptance criteria
3. Ensure `auto_launch_enabled=true`
4. Make one real issue edit so Task Manager recomputes readiness and shows `launch_state=ready` or `queued`
5. If you want a watcher-path canary instead of relying on the normal TM trigger, run exactly one controlled execute pass:

```bash
~/.openclaw/scripts/tm-ready-launch-once.sh --issue-id <tm_id>
```

Normal/default path now:
- update the issue into a valid ready state in Task Manager
- Task Manager queues the canonical Dwight launcher itself
- launcher posts back `launched|failed` evidence into the issue
- completion evidence should include `branch=... pr_status=...` and `pr_url=...` when a PR is opened

The watcher path is now optional backup/inspection tooling, not the primary source of truth.

Useful operator checks in Task Manager Search now:
- `Ready, Not Queued`
- `Queued/Launched, No Recent Evidence`
- `In Progress, No PR`

## 12) Bottom line

- Treat Telegram as your AI operating console.
- Send structured intent, not vague requests.
- Force evidence on every completion.
- Use automation for triage, planning, execution, and follow-through, not just chat answers.
- For trading-desk usage, also use `workspaces/trading-intel/HUMAN_USE_GUIDE.md` and `workspaces/trading-intel/OPERATOR_GUIDE.md`.
