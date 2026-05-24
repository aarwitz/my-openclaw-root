# ACP Rollout Plan for RSL

## Current State Audit

### What exists now
- OpenClaw is already functioning well as the outer orchestrator.
- Telegram routing is live and healthy for Jerry, Resi, Dwight, and Druck.
- The primary default model is `openai-codex/gpt-5.4`.
- OpenClaw is configured for multi-agent work, memory search, cron, and subagents.
- Aaron is already using OpenClaw as the primary command surface.

### What is missing
- No visible ACP agent configuration is currently set in OpenClaw config.
- No `acp.defaultAgent` is configured.
- No visible `allowedAgents` ACP configuration was found in local OpenClaw config output.
- No repo-scoped persistent ACP coding sessions are currently established.
- Coding work is still too dependent on direct chat-driven execution rather than durable coding harness sessions.

### Important implication
OpenClaw is already good enough to be the orchestration layer. The main upgrade is not replacing OpenClaw. The upgrade is attaching better coding harnesses to it through persistent ACP sessions.

## Target Architecture

### Layer 1: OpenClaw orchestrator
Use OpenClaw for:
- Telegram interface
- routing and delegation
- long-lived memory
- reminders and cron
- session supervision
- approvals and status reporting

### Layer 2: Persistent ACP coding sessions
Create one persistent ACP coding session per repo or major workstream.

Recommended session set:
- `session:lidi-task-manager-dev`
- `session:lidi-site-dev`
- `session:task-manager-dev`
- `session:ewag-ios-dev`

These sessions should do the actual implementation work:
- inspect code
- edit files
- run tests
- fix failures
- summarize results

### Layer 3: Evidence gate
No coding task is done unless the ACP coding session returns:
- files changed
- test/build result
- exact blocker if not complete

## Literal Workflow

1. Aaron sends request in Telegram.
2. OpenClaw triages and chooses the target repo/session.
3. OpenClaw sends the task into the persistent ACP coding session.
4. ACP coding session executes in the repo with persistent context.
5. ACP coding session returns result and evidence.
6. OpenClaw summarizes, stores memory if needed, and requests approval only at external boundaries.

## Recommended Harness Preference

Use the strongest reliable coding harness available in this order:
1. VS Code Copilot ACP-capable harness, if available and controllable
2. Claude Code or Cursor ACP harness
3. Codex ACP harness
4. Fallback to direct OpenClaw local execution only when needed

## Immediate Next Steps

### Aaron actions
1. Confirm which ACP harnesses you want available first:
   - GitHub Copilot / VS Code
   - Claude Code
   - Cursor
   - Codex ACP
2. Decide the first repos to convert to persistent coding sessions:
   - `lidi-task-manager`
   - `Task-Manager`
   - `lidi-solutions`
   - EWAG repo
3. Prefer starting with one repo first: `lidi-task-manager`

### Jerry actions
1. Keep auditing local OpenClaw ACP capabilities and config surface.
2. Create session naming conventions and workflow checklist.
3. Prepare the exact spawn commands and task contract for persistent coding sessions.
4. Once an ACP agent id is known, create the first persistent ACP coding session.

## Exact Recommended First Pilot

Pilot repo:
- `/home/aaron/repos/lidi-task-manager`

Pilot session:
- `session:lidi-task-manager-dev`

Pilot task types:
- bug fixes
- auth flow iteration
- deployment fixes
- test repair

Success criteria:
- OpenClaw can route tasks into the persistent coding session
- the session preserves repo context across tasks
- code changes come back with tests and evidence
- Aaron experiences noticeably better coding quality and speed than direct chat-driven Codex

## Minimal Migration Sequence

1. Stand up one persistent ACP coding session for `lidi-task-manager`.
2. Route all `lidi-task-manager` implementation tasks there.
3. Validate quality/speed improvement.
4. Repeat for `Task-Manager`.
5. Repeat for `lidi-solutions`.
6. Repeat for EWAG with a repo-specific session.

## What not to do first
- Do not redesign memory first.
- Do not build a knowledge graph first.
- Do not migrate every repo at once.
- Do not remove OpenClaw from the control plane.

## Bottom line
The shortest path to a materially better RSL agent system is:
- keep OpenClaw as the orchestrator
- add persistent ACP coding sessions as the execution layer
- migrate one repo at a time
