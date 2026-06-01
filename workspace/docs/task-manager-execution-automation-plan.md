# Task Manager Execution Automation Plan

Date: 2026-05-25
Status: authoritative
Owner: Jerry
Scope: deterministic automation from Task Manager issue readiness to agent execution and PR creation.

## 1) Goal

Move from policy-driven execution to deterministic execution:
- Dwight creates or updates a ready code-backed issue in Task Manager
- the assigned agent begins work automatically
- the assigned agent produces code, tests, Task Manager evidence, and a PR
- human approval remains at merge/deploy/external-action boundaries

## 2) Current state

What already exists:
- Task Manager issue launcher:
  - `/home/aaron/.openclaw/scripts/dwight-launch-from-issue.py`
- Owner-agent wrapper:
  - `/home/aaron/.openclaw/scripts/dwight-assign-coding-task.sh`
- Lane router:
  - `/home/aaron/.openclaw/scripts/launch-coding-task.sh`
- Dwight policy already says ready coding issues should launch immediately when repo path is known.
- Task Manager now performs the deterministic first queue step itself when a code-backed issue becomes ready.
- Launcher result postback now exists through Task Manager `POST /api/issues/{id}/launch-result`.

What is still missing:
- broader end-state hygiene around `launched` vs completed implementation
- automatic PR opening remains a separate policy choice; current contract requires explicit PR status evidence even when no PR is opened

Polling watcher path has been retired from runtime orchestration.

## 3) Readiness contract

An issue may auto-launch only if all are true:
- `auto_launch_enabled = true`
- Task Manager `launch_state` becomes `ready`
- `assigned_to` is one of `Jerry`, `Resi`, `Druck`, `Dwight`
- issue is code-backed and repo-executable
- repo is known via absolute `repo_path` or resolvable `repo_slug`
- branch is present for code-bearing work
- title/goal is concrete enough to execute
- acceptance criteria are present
- no unresolved blocker requiring a human first

An issue must not auto-launch if any are true:
- `assigned_to = Aaron`
- task is planning/admin/research-only with no repo execution path
- repo is unknown
- external approval is required before execution
- Task Manager `launch_state = queued` and the current signature was already adopted by the watcher

## 4) Trigger model

Current trigger:
- Task Manager issue becomes `in_progress` while satisfying the readiness contract
- backend recomputes readiness on issue create/update
- backend detaches the canonical Dwight launcher immediately on the first ready signature

Current recommended path:
- default: rely on Task Manager issue update trigger
- use `run issue ...` as the operator override
- use webhook events to keep PR/issue state synchronized without polling

## 5) Execution contract

When a ready issue triggers:
1. Resolve owner agent from `assigned_to`
2. Resolve repo from `repo_path` or `repo_slug`
3. Resolve goal from issue title/description
4. Pass acceptance criteria through
5. Launch through `dwight-launch-from-issue.py`
6. Route through coding lane selection
7. Post launcher outcome back to Task Manager as `launched` or `failed`
7. Executing agent owns:
   - implementation
   - tests/checks
   - Task Manager progress comment(s)
   - branch updates
   - PR creation

Re-entry standard:
- one launch per ready signature
- comment-only progress should not trigger another launch
- a real Task Manager issue edit while still ready may create a new launch signature
- this supports multi-pass Codex execution without poll-loop duplicates

## 6) Completion contract

For code-backed issues, done means:
- code changed or blocker clearly documented
- targeted validation run
- Task Manager evidence comment posted
- PR status explicitly recorded as one of:
  - `opened`
  - `not-opened`
  - `not-needed`
  - `unknown`
- PR URL included when `pr_status=opened`

Completion does not imply:
- auto-merge
- auto-deploy
- auto-send external messages
- irreversible destructive actions

## 7) Rollout phases

### Phase 1: readiness enforcement
- Add/confirm required Task Manager issue fields
- Standardize branch/repo/acceptance criteria requirements
- Mark code-backed issues explicitly or infer them cleanly
- Status: implemented for the live TM path

### Phase 2: deterministic launch
- Trigger on `in_progress` + readiness contract satisfied
- Persist launch metadata and de-duplicate launches
- Post launcher result back into the issue
- Status: implemented for the live TM path

### Phase 3: queue discipline + PR automation
- Decide whether one agent may have multiple `queued` launches at once
- Standardize PR title/body from Task Manager issue
- Post PR link back to Task Manager
- Require test/evidence summary before PR open
- Status: queue discipline and explicit PR-status evidence are implemented; automatic PR opening is not

### Phase 4: hygiene and reporting
- Add dashboards/searches for:
  - ready but not launched
  - queued but no real execution evidence
  - in progress without PR
  - PR open but issue not updated
- Status: first operator views now exist in Task Manager Search as preset filters for:
  - `Ready, Not Queued`
  - `Queued/Launched, No Recent Evidence`
  - `In Progress, No PR`
- Legacy normalization note:
  - completed audit/canary issues should be moved out of `in_progress` once evidence lands
  - older non-executable items should not remain in `in_progress` without branch/repo/acceptance criteria

## 8) Safety rules

- Never auto-launch Aaron-assigned work
- Never auto-launch non-repo tasks
- Never auto-merge
- Never auto-deploy without explicit approval
- Never let one issue launch multiple concurrent executions without idempotency guard

## 9) Recommended first implementation

First pilot result:
- live canary issue `#131` proved the clean loop end-to-end:
  - Task Manager issue update
  - automatic queue
  - canonical launcher execution
  - structured postback through `POST /api/issues/{id}/launch-result`
  - branch evidence plus explicit `pr_status`

Next pilot hardening:
- decide whether PR opening itself should become automatic
- extend operator reporting if PR-open and stale-evidence views become necessary

## 10) Source of truth

For rollout:
- this file

For lane behavior:
- `/home/aaron/.openclaw/ARCHITECTURE.md`

For Dwight execution policy:
- `/home/aaron/.openclaw/workspaces/dwight/AGENTS.md`

## 11) Orchestration ownership (who does what)

- Aaron:
  - sets policy, priorities, approvals, and final reviewer decisions
  - may assign launch ownership, but should avoid being auto-launch target
- Dwight (orchestrator):
  - owns launch readiness validation and deterministic launch routing
  - ensures code-backed issue fields are complete before launch
  - posts launch/evidence status back to Task Manager
- Assigned execution agents (`Jerry|Resi|Druck`):
  - own code changes, tests, progress comments, PR creation/update, and blocker notes
  - keep issue state current (`in_progress`, `in_review`, `done`)
- Task Manager backend:
  - source of truth for readiness and queue state (`ready -> queued -> launched|failed`)
  - first-step queue trigger (detached launcher)
- Watcher (`tm-ready-watcher.sh`):
  - observer/recovery path only
  - may execute in controlled mode during canary/recovery runs

## 12) Signal strategy (event-first, low compute)

Priority order:
- Task Manager issue create/update signal (primary)
- Launcher postback signal (`launch-result`) (primary)
- GitHub PR/issue updates linked from execution comments (primary for review state)

Rules for efficiency:
- Do not run polling launch watchers.
- Keep launch ownership in Task Manager + webhook event handlers.
- Use Task Manager Search preset views as operator surface before running any sweeps:
  - `Ready, Not Queued`
  - `Queued/Launched, No Recent Evidence`
  - `In Progress, No PR`
- Trigger manual or scheduled reconciliation only when one of these is non-empty.

Recommended lightweight cadence:
- Event-driven immediate path: Task Manager readiness triggers launch instantly.
- Event-driven PR lifecycle path: GitHub webhooks update issue review/completion state and enforce PR contract checks.
- Manual reconciliation only when operator views show drift.

## 13) iOS execution conflict policy

- iOS mac-node operations are globally serialized through a shared queue lock.
- Conflicting commands (`build`, `test`, `capture`, `sim`) must wait for lock; they must not fail-open or run in parallel.
- Queue lock implementation lives in `~/.openclaw/scripts/lib/ewag-node-queue.sh` and is enforced by EWAG scripts.
- If lock wait exceeds timeout, command should fail with explicit timeout evidence and no partial parallel work.

## 14) Minimal-human autonomous path to PR

For a launch-ready code issue, autonomous path should be:
- `ready` issue auto-queues and launches via Dwight launcher
- agent implements with targeted tests and evidence comments
- agent opens PR with:
  - issue link
  - problem/solution summary
  - test evidence
  - UI screenshots when UI changed
- issue updated to `in_review` with `pr_status=opened` and PR URL
- Aaron only intervenes for policy decisions, blockers, and final review/merge
