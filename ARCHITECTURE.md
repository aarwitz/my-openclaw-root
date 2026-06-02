# OpenClaw Architecture Spec (Canonical)

Date: 2026-05-25
Status: authoritative
Scope: control plane, coding lane routing, execution contract, operator safety.

## 1) Core model (literal)

- Runtime layering is hybrid and intentional:
  - OpenClaw agent runtime host executes turns.
  - Codex SDK is the model loop for openai/gpt-* turns.
  - ACP is disabled by policy (no fallback).
- Telegram is control/status UX, not the coding execution loop.
- Coding execution is detached task style, not a chat-only turn.
- Repo isolation target is git worktree per task.

## 2) Current hard facts from live config/scripts

- `models.providers.openai.agentRuntime.id = "codex"` is configured.
- ACP backend is disabled through config policy:
  - `acp.enabled = false`
  - `plugins.entries.acpx.enabled = false`
- Dwight execution trigger is defined in prompt policy:
  - `run issue <id> --repo <abs-path> ...` maps to
  - `/home/aaron/.openclaw/scripts/dwight-launch-from-issue.py ... --execute`
- Dwight workspace policy also requires immediate launch for ready coding issues:
  - actionable coding story
  - assigned to `Jerry|Resi|Druck|Dwight`
  - repo path known
  - do not wait for Aaron to repeat the request

## 3) Canonical coding lanes

- `inline`: owner agent executes directly in its Codex-backed turn.
- `codex-subagent`: owner/coder agent executes via native Codex subagent (`spawn_agent`) contract.

ACP external lane is retired and must not be used.

Lane meanings are strict. Do not use `codex` as a lane label.

## 4) Deterministic routing policy (implemented)

Inputs:
- `scope`: `low|medium|high`
- `risk`: `low|medium|high`
- `expected-files`: integer
- `tag-heavy`: `true|false`

Decision rules in order:
1. If `scope=medium|high` OR `risk=high` OR `expected-files>=2` -> `codex-subagent`
2. Else if `tag-heavy=true` -> `codex-subagent`
3. Else -> `inline`

Fallback chain:
- `codex-subagent -> inline`
- `inline -> inline`

## 5) ACP policy (retired)

ACP preflight and ACP runtime fallback are retired.
Any legacy ACP inputs are coerced to `codex-subagent` and annotated in routing metadata.

## 7) Metadata contract (implemented)

Each run writes JSON to:
- `~/.openclaw/tmp/coding-lane-runs/<task-id>.<timestamp>.json`

Routing payload fields:

```json
{
  "requestedLane": "codex-subagent|inline",
  "lane": "codex-subagent|inline",
  "reason": "...",
  "fallbackLane": "codex-subagent|inline",
  "fallbackApplied": true,
  "fallbackReason": "...",
  "scope": "low|medium|high",
  "expectedFiles": 8,
  "risk": "low|medium|high",
  "heavyTag": true,
  "acpAvailable": false,
  "acpAgent": "disabled",
  "acpPreflight": { "status": "not-run", "detail": "acp-disabled-by-policy" }
}
```

## 8) Execution entrypoints (canonical)

- Telegram to Dwight (preferred control plane):
  - `run issue <id> --repo <abs-path> [--scope ... --expected-files ... --risk ... --agent-timeout ...]`
- Deterministic server CLI path:
  - `/home/aaron/.openclaw/scripts/dwight-launch-from-issue.py --issue-id <id> --repo <abs-path> ... --execute`
- Direct lane launcher path:
  - `/home/aaron/.openclaw/scripts/launch-coding-task.sh --task-id <id> --repo <abs-path> --goal <text> ... [--execute]`

## 9) Task Manager execution state

Current implemented behavior:
- Dwight can launch repo-backed coding work from a Task Manager issue.
- Owner agent is inferred from `assigned_to`.
- Repo can be inferred from `repo_path`, `repository_path`, `repo`, `repository`, `workspace`, or `repo_slug`.
- Goal is inferred from title/summary/description.
- Acceptance criteria is passed through when present.
- Task Manager now owns the auto-launch readiness queue through `auto_launch_enabled`, `launch_state`, `launch_error`, `last_launch_at`, and the internal `launch_signature`.
- Task Manager now performs the first deterministic queue step itself when a code-backed issue becomes ready.
- The canonical launch path is:
  - Task Manager issue update
  - detached spawn of `/home/aaron/.openclaw/scripts/dwight-launch-from-issue.py --issue-id <id> --execute`
  - lane routing through `/home/aaron/.openclaw/scripts/launch-coding-task.sh`
  - launcher result postback to Task Manager via `POST /api/issues/{id}/launch-result`
- Polling watcher execution path is retired. Launch orchestration is event-driven only:
  - Task Manager issue readiness transition
  - detached canonical Dwight launcher
  - launcher result postback to Task Manager
  - GitHub webhook PR events for review/completion contract updates

Readiness contract for automatic launch:
- `auto_launch_enabled = true`
- `launch_state = ready` for Task Manager queueing
- `launch_state = queued` once Task Manager has detached the canonical launcher
- `launch_state = launched|failed` after launcher postback records the real execution outcome
- `assigned_to` present and maps to `Jerry|Resi|Druck|Dwight`
- repo path resolvable to an absolute local checkout
- branch present for code-bearing stories
- goal/title concrete enough to execute
- acceptance criteria present
- story is code-backed, not admin-only
- no approval-gated language such as "awaiting approval" or "requires sign-off"

Automation boundary:
- Auto-start is appropriate once readiness contract is satisfied.
- Auto-PR creation is appropriate after code/tests/evidence exist.
- Auto-merge, deploy, external sends, account changes, and destructive operations remain approval-gated.

Current operator visibility:
- Task Manager Search now exposes preset operator views for:
  - ready but not queued
  - queued/launched without recent evidence
  - in progress without PR-open evidence
- this is now the primary operator surface for backlog/launch hygiene before adding more automation

Event re-entry rule:
- one Task Manager launch signature launches once
- `launch_state=queued` is treated as already queued/launched by Task Manager and is never re-launched by polling
- comment-only progress does not requeue work
- a real issue edit in Task Manager can create a new ready signature and permit a new launch
- queue discipline default is one active `queued|launched` code task per executing agent at a time
- structured completion evidence now requires explicit branch and PR status, even when no PR is opened

## 10) ASCII architecture diagram

```text
[Task Source]
  Task Manager issue | operator command | future TM event trigger
          |
          v
[OpenClaw Orchestrator (Dwight/Jerry)]
  - validates readiness contract
  - resolves owner/repo/goal
  - starts detached coding task
          |
          v
[Lane Router: select-coding-lane.sh]
  - scope/risk/files/heavy
          |
          +------------------------------+
          |                              |
          v                              v
[inline]                        [codex-subagent]
 owner-agent direct             native spawn_agent path
                                      JSON contract check
          |                              |
          +---------------+--------------+
                          |
                          v
                 [Repo Execution Plane]
          ~/.openclaw/repos/<agent>/worktrees/<task-id>
                          |
                          v
                     [Completion]
           tests/evidence -> PR + Task Manager update
           Telegram gets status/summary
```

## 11) Operator safety rules (non-negotiable)

- Never use `systemctl --user restart` for gateway restarts.
- Use `~/.openclaw/scripts/safe-restart.sh` only when restart is truly needed.
- Hot reload is default for config changes.
- Codex refresh tokens are single-use and fragile; avoid unnecessary concurrent Codex token pressure.
- Do not let Task Manager state changes auto-launch non-code work or anything assigned to Aaron.

## 12) Known gaps (current)

- Queue discipline is still loose when several ready issues for the same agent become valid at once; a per-agent ordering policy would make operations cleaner.
- Evidence postback now exists, but the broader completion contract still needs stronger standardization around PR creation/update and end-state hygiene.

## 13) Decision boundary

- Use Task Flow only for multi-step pipelines.
- Use single detached task for one coding outcome.
- Keep lane vocabulary and fallback semantics unchanged unless script behavior is intentionally revised in the same change set.
- Use `workspace/docs/task-manager-execution-automation-plan.md` as the coding-lane implementation reference when you need low-level launcher details.
