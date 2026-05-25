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

- Telegram accounts are active for `default`, `dwight`, `resi`, `druck`.
- Allowed Telegram user IDs are allowlisted.
- Coding execution path is detached task routing with lanes:
	- `inline`
	- `codex-subagent`
	- `acp-external`
- Dwight has an explicit run contract for coding:
	- `run issue <id> --repo <abs-path> [--scope ... --expected-files ... --risk ... --acp-available ... --acp-agent ... --agent-timeout ...]`
- Dwight policy already says ready coding issues should be launched immediately when repo path is known.
- Fully deterministic Task Manager state-triggered launch is planned, but not yet the current source of truth.

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
2. Re-run with explicit scope/risk/expected-files.
3. If ACP path fails at runtime, rely on codex-subagent fallback and request metadata path.
4. If still blocked, run deterministic CLI launcher from server.

## 11) Safe First Live Use

For the first live auto-launch on a real issue:

1. Ensure the issue is code-backed and `in_progress`
2. Ensure it has:
   - `assigned_to`
   - `repo_slug`
   - `branch`
   - acceptance criteria
3. Add `AUTO_LAUNCH_READY` to the issue description
4. Run exactly one controlled execute pass:

```bash
~/.openclaw/scripts/tm-ready-launch-once.sh --issue-id <tm_id>
```

This is preferred over enabling a permanent background loop immediately.

## 12) Bottom line

- Treat Telegram as your AI operating console.
- Send structured intent, not vague requests.
- Force evidence on every completion.
- Use automation for triage, planning, execution, and follow-through, not just chat answers.
- For the implementation rollout, use `workspace/docs/task-manager-execution-automation-plan.md`.
