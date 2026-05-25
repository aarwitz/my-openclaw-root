# OpenClaw Architecture Spec (Canonical)

Date: 2026-05-25
Status: authoritative
Scope: control plane, coding lane routing, execution contract, fallbacks, operator safety.

## 1) Core model (literal)

- Runtime layering is hybrid and intentional:
  - OpenClaw agent runtime host executes turns.
  - Codex app-server is the model loop for openai/gpt-* turns.
  - ACP is external harness escalation, not the default runtime.
- Telegram is control/status UX, not the coding execution loop.
- Coding execution is detached task style, not a chat-only turn.
- Repo isolation target is git worktree per task.

## 2) Current hard facts from live config/scripts

- `models.providers.openai.agentRuntime.id = "codex"` is configured.
- ACP backend is enabled through acpx:
  - `acp.enabled = true`
  - `plugins.entries.acpx.enabled = true`
- Non-interactive ACP permission policy is set:
  - `plugins.entries.acpx.config.permissionMode = "approve-all"`
  - `plugins.entries.acpx.config.nonInteractivePermissions = "deny"`
- Dwight execution trigger is defined in prompt policy:
  - `run issue <id> --repo <abs-path> ...` maps to
  - `/home/aaron/.openclaw/scripts/dwight-launch-from-issue.py ... --execute`

## 3) Canonical coding lanes

- `inline`: owner agent executes directly in its Codex-backed turn.
- `codex-subagent`: owner/coder agent executes via native Codex subagent (`spawn_agent`) contract.
- `acp-external`: orchestrator asks ACP runtime to spawn external harness (`runtime="acp"`, `agentId=<harness>`).

Lane meanings are strict. Do not use `codex` as a lane label.

## 4) Deterministic routing policy (implemented)

Inputs:
- `scope`: `low|medium|high`
- `risk`: `low|medium|high`
- `expected-files`: integer
- `tag-heavy`: `true|false`
- `acp-available`: `true|false`

Decision rules in order:
1. If `acp-available=false`:
   - if `scope=medium|high` OR `risk=high` OR `expected-files>=2` -> `codex-subagent`
   - else -> `inline`
2. Else if `tag-heavy=true` -> `acp-external`
3. Else if `scope=high` OR `risk=high` OR `expected-files>=8` -> `acp-external`
4. Else if `scope=medium` OR `risk=medium` OR `expected-files>=2` -> `codex-subagent`
5. Else -> `inline`

Fallback chain:
- `acp-external -> codex-subagent`
- `codex-subagent -> inline`
- `inline -> inline`

## 5) ACP preflight gate (implemented)

When selected lane is `acp-external`, launcher blocks or downgrades on these failures:

- `acp_or_acpx_disabled`
- `agent_not_allowed` (requested harness not in `acp.allowedAgents`)
- `permission_policy_mismatch` (`approve-all` + `deny` not satisfied)
- `harness_binary_missing` (local executable missing for selected harness)

If fallback lane is `codex-subagent`, launcher auto-downgrades and records fallback metadata.

## 6) ACP runtime fallback (implemented)

Even after preflight `ready`, launcher can auto-fallback if runtime output indicates hard ACP failure.

Runtime fallback trigger patterns:
- `Failed to spawn agent command`
- `spawn <bin> ENOENT`
- `AcpRuntimeError`
- `Permission prompt unavailable in non-interactive mode`

Behavior:
- First leg metadata: requested/selected `acp-external`.
- Auto-redispatch with `--acp-available false`.
- Second leg metadata: requested/selected `codex-subagent`.

## 7) Metadata contract (implemented)

Each run writes JSON to:
- `~/.openclaw/tmp/coding-lane-runs/<task-id>.<timestamp>.json`

Routing payload fields:

```json
{
  "requestedLane": "acp-external|codex-subagent|inline",
  "lane": "acp-external|codex-subagent|inline",
  "reason": "...",
  "fallbackLane": "codex-subagent|inline",
  "fallbackApplied": true,
  "fallbackReason": "acp-preflight-failed:<detail>|...",
  "scope": "low|medium|high",
  "expectedFiles": 8,
  "risk": "low|medium|high",
  "heavyTag": true,
  "acpAvailable": true,
  "acpAgent": "cursor|copilot|claude|codex|...",
  "acpPreflight": { "status": "ok|failed|not-run", "detail": "..." }
}
```

## 8) Execution entrypoints (canonical)

- Telegram to Dwight (preferred control plane):
  - `run issue <id> --repo <abs-path> [--scope ... --expected-files ... --risk ... --acp-available ... --acp-agent ... --agent-timeout ...]`
- Deterministic server CLI path:
  - `/home/aaron/.openclaw/scripts/dwight-launch-from-issue.py --issue-id <id> --repo <abs-path> ... --execute`
- Direct lane launcher path:
  - `/home/aaron/.openclaw/scripts/launch-coding-task.sh --task-id <id> --repo <abs-path> --goal <text> ... [--execute]`

## 9) ASCII architecture diagram

```text
[Task Source]
  Task Manager issue | webhook | operator command
          |
          v
[OpenClaw Orchestrator (Dwight/Jerry)]
  - resolves owner/repo/goal
  - starts detached coding task
          |
          v
[Lane Router: select-coding-lane.sh]
  - scope/risk/files/heavy/acp-available
          |
          +------------------------------+
          |                              |
          v                              v
 [acp-external]                    [codex-subagent]
  runtime="acp"                    native spawn_agent path
  agentId=<harness>                 JSON contract check
          |                              |
          +---------------+--------------+
                          |
                          v
                       [inline]
                    owner-agent direct
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

## 10) Operator safety rules (non-negotiable)

- Never use `systemctl --user restart` for gateway restarts.
- Use `~/.openclaw/scripts/safe-restart.sh` only when restart is truly needed.
- Hot reload is default for config changes.
- Codex refresh tokens are single-use and fragile; avoid unnecessary concurrent Codex token pressure.

## 11) Known gap (current)

- ACP-ready lane selection can still fail in some runtime contexts if harness binary is unavailable in that execution environment (example observed: `spawn copilot ENOENT`).
- This is mitigated by implemented runtime fallback to `codex-subagent`, but root environment parity should still be fixed.

## 12) Decision boundary

- Use Task Flow only for multi-step pipelines.
- Use single detached task for one coding outcome.
- Keep lane vocabulary, preflight checks, and fallback semantics unchanged unless script behavior is intentionally revised in the same change set.