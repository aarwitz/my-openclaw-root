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

What is still missing:
- deterministic Task Manager state transition trigger
- enforced readiness gates at the Task Manager layer
- explicit PR creation/update as a standard completion contract
- clean exclusion rules so admin/planning tasks do not auto-launch

Current watcher status:
- scans ready `in_progress` issues
- respects readiness contract and executing-agent allowlist
- persists per-issue launch state
- dry-run does not claim the launch signature
- re-entry is keyed off issue state edits, not comments alone
- execute mode requires explicit environment gate
- single-issue execute wrapper exists:
  - `/home/aaron/.openclaw/scripts/tm-ready-launch-once.sh`
- still needs rollout/invocation wiring and broader execution hardening

## 3) Readiness contract

An issue may auto-launch only if all are true:
- `assigned_to` is one of `Jerry`, `Resi`, `Druck`, `Dwight`
- issue is code-backed and repo-executable
- repo is known via absolute `repo_path` or resolvable `repo_slug`
- branch is present for code-bearing work
- title/goal is concrete enough to execute
- acceptance criteria are present
- description contains explicit opt-in marker `AUTO_LAUNCH_READY`
- no unresolved blocker requiring a human first

An issue must not auto-launch if any are true:
- `assigned_to = Aaron`
- task is planning/admin/research-only with no repo execution path
- repo is unknown
- external approval is required before execution

## 4) Trigger model

Target trigger:
- Task Manager issue becomes `in_progress` while satisfying the readiness contract

Acceptable interim trigger:
- Dwight explicitly runs the issue launcher immediately after creating/updating the ready issue

Longer-term trigger choices:
1. Task Manager backend event hook on issue update
2. polling watcher that scans for newly ready issues
3. hybrid: backend marks ready, watcher launches

Recommended path:
- start with polling watcher for safety and easier rollback
- later move to backend event trigger if needed
- for immediate use, prefer the single-issue execute wrapper before enabling any persistent watcher loop

## 5) Execution contract

When a ready issue triggers:
1. Resolve owner agent from `assigned_to`
2. Resolve repo from `repo_path` or `repo_slug`
3. Resolve goal from issue title/description
4. Pass acceptance criteria through
5. Launch through `dwight-launch-from-issue.py`
6. Route through coding lane selection
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
- PR created or updated unless blocked by a concrete reason

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

### Phase 2: deterministic launch
- Implement a watcher or backend trigger
- Trigger on `in_progress` + readiness contract satisfied
- Persist launch metadata and de-duplicate launches

### Phase 3: PR automation
- Standardize PR title/body from Task Manager issue
- Post PR link back to Task Manager
- Require test/evidence summary before PR open

### Phase 4: hygiene and reporting
- Add dashboards/searches for:
  - ready but not launched
  - launched but no progress
  - in progress without PR
  - PR open but issue not updated

## 8) Safety rules

- Never auto-launch Aaron-assigned work
- Never auto-launch non-repo tasks
- Never auto-merge
- Never auto-deploy without explicit approval
- Never let one issue launch multiple concurrent executions without idempotency guard

## 9) Recommended first implementation

First pilot:
- use Task Manager issues assigned to `Jerry`
- watcher scans for ready issues every short interval
- on first ready transition, launch exactly once and write launch metadata back to Task Manager
- require PR creation for completion

Then extend to:
- `Resi`
- `Dwight`
- `Druck` where repo-backed coding applies

## 10) Source of truth

For rollout:
- this file

For lane behavior:
- `/home/aaron/.openclaw/ARCHITECTURE.md`

For Dwight execution policy:
- `/home/aaron/.openclaw/workspaces/dwight/AGENTS.md`
