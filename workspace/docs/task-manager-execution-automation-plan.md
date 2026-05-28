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
- Safe watcher scaffold:
  - `/home/aaron/.openclaw/scripts/tm-ready-watcher.sh`
- Dwight policy already says ready coding issues should launch immediately when repo path is known.
- Task Manager now performs the deterministic first queue step itself when a code-backed issue becomes ready.
- Launcher result postback now exists through Task Manager `POST /api/issues/{id}/launch-result`.

What is still missing:
- broader end-state hygiene around `launched` vs completed implementation
- automatic PR opening remains a separate policy choice; current contract requires explicit PR status evidence even when no PR is opened

Current watcher status:
- scans Task Manager auto-launch issues in `ready|queued`
- respects readiness contract and executing-agent allowlist
- persists per-issue launch metadata in watcher state
- dry-run does not claim the launch signature
- re-entry is keyed off the Task Manager launch signature fields, not comments alone
- execute mode requires explicit environment gate
- single-issue execute wrapper exists:
  - `/home/aaron/.openclaw/scripts/tm-ready-launch-once.sh`
- now acts primarily as observer/backup tooling rather than the primary source of truth

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

Optional backup trigger:
- polling watcher that scans for ready/queued issues and adopts or executes them when explicitly invoked

Current recommended path:
- default: rely on Task Manager issue update trigger
- use `run issue ...` as the operator override
- use the single-issue watcher execute wrapper only for controlled canaries or recovery

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
