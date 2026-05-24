# New Architecture

## Decision

- Control-plane runtime: native Codex.
- Coding runtime: three lane.
- Heavy coding default lane: ACP external worker.
- First fallback lane: native Codex subagent detached task.
- Final fallback lane: inline owner-agent detached task.
- Canonical coding model refs remain `openai/gpt-*` for Codex lane.
- Execution shape: detached task, not interactive Telegram turn.
- Orchestration shape: Task Flow only for multi-step pipelines.
- Repo isolation: git worktree per task.
- Output surfaces: GitHub PR and Task Manager update.
- Telegram role: control and status only.

## Diagram

```text
[Task source]
Task Manager issue / webhook / operator command
    |
    v
[OpenClaw orchestration]
- single job -> detached task
- multi-step job -> Task Flow -> child tasks
- orchestrator agents can call control-plane tools directly
    |
    +-------------------------------> [Control-plane tool access]
    |                                 - Task Manager MCP or API bridge
    |                                 - GitHub MCP for issue/PR metadata ops
    |                                 - scheduling / flow / notification surfaces
    |                                 - optional memory / search surfaces
    |
    v
[OpenClaw gateway]
- sessions
- delivery
- task ledger
- transcript mirror
- approvals bridge
    |
    v
[Lane router]
- route=acp-external when task is symbol-heavy or long-running
- route=codex-subagent for medium complexity or ACP-unavailable escalation
- route=inline when task is small and bounded
    |
    +-------------------------------> [ACP external lane]
    |                                 - runtime: acp via acpx backend
    |                                 - worker profile: cursor/copilot/claude wrapper
    |                                 - strengths: concurrency, approvals, richer code-edit loop
    |
    +-------------------------------> [Native Codex subagent lane]
    |                                 - model ref: openai/gpt-5.5
    |                                 - runtime policy: codex
    |                                 - strengths: lower overhead than ACP, isolated subtask execution
    |
    +-------------------------------> [Inline owner-agent lane]
    |                                 - model ref: openai/gpt-5.5
    |                                 - runtime policy: codex
    |                                 - strengths: fastest path for bounded changes
    |
    v
[Repo execution plane]
- bare mirror: ~/.openclaw/repos/<agent>/<repo>.git
- task worktree: ~/.openclaw/repos/<agent>/worktrees/<task-id>
- branch: <agent>/<task-id>-<slug>
    |
    v
[Tool plane]
- Runtime-plane access from coding run:
    - ACP lane: harness-native coding and tool operations
    - Codex lane: Codex native file/shell/MCP actions
    - OpenClaw dynamic tools for messaging, browser, search, notifications
    - GitHub MCP
    - repo-specific test/build wrappers
    |
    v
[Completion]
- tests run
- PR opened or updated
- Task Manager updated
- Telegram optional summary
```

## Why This Is Optimal

- It keeps OpenClaw ownership clear. OpenClaw owns orchestration, task state, and delivery.
- It separates control-plane work from coding-plane work with explicit lane routing.
- It fixes the main control-plane error. A coding job is detached work, not a chat session that must be pinged.
- It keeps resilient fallback. ACP external is primary for heavy coding, Codex subagent is first fallback, inline is final fallback.
- It avoids custom code-intelligence R&D by delegating heavy edit loops to ACP-capable harnesses.

## Required Components

- `plugins.entries.codex.enabled = true`
- `plugins.entries.acpx.enabled = true` for ACP external lane
- coding agents in Codex lane use `openai/gpt-5.5` or another `openai/gpt-*` ref
- provider or model runtime policy set to `agentRuntime.id = "codex"` for Codex subagent and inline lanes
- `auth.order.openai` with Codex OAuth first, API-key fallback second
- git worktree-per-task
- GitHub integration
- Task Manager integration
- ACP worker profile(s) installed and authenticated on host

## Tool Access Model

- Orchestrator bots should have direct access to control-plane tools.
- Typical orchestrator tools: Task Manager, GitHub metadata operations, flow/task controls, notifications, memory/search.
- Codex coding runs should have direct access to runtime-plane tools.
- ACP coding runs should have direct access to runtime-plane tools.
- Typical coding-run tools: repo file operations, shell/test/build wrappers, GitHub PR operations, selected OpenClaw dynamic tools.
- The same external system can be reachable from both planes, but for different purposes.
- Example: GitHub MCP can be used by the orchestrator to create or label work, and by the coding run to open or update a PR.

## Lane Routing Policy

- Default to ACP external lane when at least one condition is true:
    - multi-file refactor across modules
    - expected runtime above quick-fix budget
    - high review risk requiring tighter edit-loop control
- Default to inline lane when all conditions are true:
    - low-to-medium scope
    - bounded change set
    - low operational risk and quick turnaround required
- Route to codex-subagent if ACP external is unavailable and task is medium/high complexity.
- Force ACP external lane if task is explicitly tagged `heavy-coding` by orchestrator policy.

Fallback triggers from ACP external to Codex subagent lane:
- worker launch failure
- timeout budget exceeded
- unrecoverable tool/approval deadlock
- worker crash or transport loss

## What This Does Not Solve

- No documented bundled IDE-grade symbol graph
- No documented bundled live LSP diagnostics surface
- No documented bundled active-cursor context like Cursor or Copilot
- ACP lane introduces more moving parts than pure Codex lane (worker lifecycle and routing logic)

## Practical Implication

- The main quality gain still comes from detached execution shape.
- The second quality gain comes from routing heavy coding to ACP external lane instead of forcing all work through one Codex loop.
- Codex subagent and inline lanes remain critical for resilience and low-overhead fallback.
- If you want task -> PR without babysitting, detached tasks are mandatory in both lanes.

## Detached Task Lifecycle (Exact)

1. Intake:
     - Source creates a work item (Task Manager issue, webhook event, or operator command).
     - Orchestrator resolves `owner`, `repo`, `goal`, `acceptance`, `priority`.
2. Create detached run:
     - Start one detached run (CLI/automation path) and register it as a task record.
     - Expected task state transition: `queued -> running`.
3. Route coding lane:
    - Apply lane policy to choose `acp-external`, `codex-subagent`, or `inline`.
    - Persist lane choice in task metadata for audit and retries.
4. Preflight gate:
     - Validate repo access, credentials, branch policy, required tools, and test command availability.
     - If preflight fails, write explicit blocker and finalize `failed`.
5. Workspace materialization:
     - Ensure bare mirror exists.
     - Create or reuse task worktree.
     - Create deterministic branch `<agent>/<task-id>-<slug>`.
6. Worker bind/resume:
    - ACP external lane: spawn ACP worker session and attach to task workspace.
    - Codex subagent lane: use native Codex runtime and spawn a coding subagent task.
    - Inline lane: execute on the assigned owner agent in its bound workspace.
7. Execute coding loop:
     - Read task context and repo bootstrap docs.
     - Apply edits.
     - Run lint/test/build.
     - Repeat until acceptance criteria pass or blocker encountered.
8. Publish:
     - Open/update PR.
     - Attach test output summary, artifact links, and risk notes.
9. Persist outcomes:
     - Update Task Manager with terminal details.
     - Update detached task record to terminal state (`succeeded`, `failed`, `timed_out`, `cancelled`, or `lost`).
10. Notify:
     - Push completion to primary surfaces (GitHub + Task Manager).
     - Telegram gets optional summary only.
11. Fallback on failure (conditional):
    - If lane is `acp-external` and fallback trigger fires, requeue task once on `codex-subagent` lane with same task id and branch continuity.
    - If fallback also fails, finalize with explicit blocker payload.
12. Cleanup:
     - Keep worktree while active/reviewed.
     - Prune stale/merged worktrees via retention policy.

Notes:
- For one coding step, use plain detached task with explicit lane selection.
- For multi-step pipelines (preflight -> code -> validate -> publish), use Task Flow managed mode.
- Queue steering can guide an active run but should not be the primary liveness mechanism.

## Design-Time Config Schema

Reference shape (architecture-level; adapt ids/paths to your deployment):

```json5
{
    plugins: {
        allow: ["acpx", "codex", "openai", "telegram", "brave"],
        entries: {
            acpx: {
                enabled: true
            },
            codex: {
                enabled: true,
                config: {
                    appServer: {
                        mode: "guardian",
                        // Optional for latency-sensitive lanes:
                        // serviceTier: "priority"
                    }
                }
            }
        }
    },

    auth: {
        order: {
            openai: [
                "openai-codex:<subscription-profile>",
                "openai:<api-key-fallback-profile>"
            ]
        }
    },

    agents: {
        defaults: {
            model: { primary: "openai/gpt-5.5" },
            workspace: "/home/<user>/.openclaw/workspace"
        },
        list: [
            {
                id: "orchestrator",
                // control-plane bot
                model: "openai/gpt-5.5"
            },
            {
                id: "coder-acp-external",
                // heavy coding bot via ACP external backend
                runtime: {
                    type: "acp",
                    acp: {
                        agent: "cursor",
                        backend: "acpx"
                    }
                }
            },
            {
                id: "coder-codex-subagent",
                // medium-scope Codex subagent lane
                model: "openai/gpt-5.5"
            },
            {
                id: "coder-inline-owner",
                // bounded-scope inline lane on owner agent
                model: "openai/gpt-5.5"
            }
        ]
    },

    models: {
        providers: {
            openai: {
                // fail-closed coding lanes
                agentRuntime: { id: "codex" }
            }
        }
    },

    // Queue steering controls for active run behavior
    messages: {
        queue: {
            mode: "steer",
            debounceMs: 1200
        }
    }
}
```

Control-plane and runtime-plane tool policy split:
- Orchestrator agent policy:
    - allow Task Manager APIs, GitHub metadata actions, flow/task controls, notifications, memory/search.
- ACP external policy:
    - allow repo file/shell actions, test/build wrappers, PR operations, selected dynamic tools.
- Codex subagent policy:
    - allow repo file/shell actions, test/build wrappers, PR operations, selected dynamic tools.
- Inline owner-agent policy:
    - allow repo file/shell actions, test/build wrappers, PR operations, selected dynamic tools.

## Repo Indexing and Retrieval: Research Findings

Documented capabilities:
- `memory_search` uses hybrid retrieval (vector + BM25) and merges both paths.
- `memory_search` supports quality knobs (MMR and temporal decay) for large histories.
- `memory-lancedb` provides active vector memory with configurable embedding providers and dimensions.
- `memory-wiki` provides compiled, provenance-rich knowledge pages and wiki-specific search/get/apply flows.
- PI Tool Search exists for large tool catalogs, but this is PI-focused and not the Codex-native tool-search surface.

Documented non-equivalence to Cursor/Copilot:
- No documented bundled editor-LSP symbol graph.
- No documented bundled active cursor/open-buffer ranking input.
- No documented bundled inline editor diff-review loop.

Design implication:
- Use strong repo bootstrap docs + deterministic task templates + hybrid memory retrieval to reduce the gap.
- Expect remaining delta versus Cursor/Copilot on deep symbol-aware refactors and editor-context ranking.
- Do not assume PI-specific Tool Search features are the Codex-native path.
