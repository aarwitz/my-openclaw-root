# task-manager-mcp

Minimal MCP server for RSL Task Manager.

This server wraps the Task Manager API and exposes stable MCP tools for orchestration and issue tracking.

## Why this exists

- Replace duplicated task-manager skill logic across agents with one shared API surface.
- Keep orchestration semantics (`task_assign`, `task_complete`, `task_heartbeat`) centralized.
- Make Task Manager integration testable when backend routes evolve.

## Tools provided

- `health`
- `issue_get`
- `issue_list`
- `issue_search`
- `issue_create`
- `issue_update`
- `issue_assign_to_sprint`
- `issue_add_comment`
- `issue_upload_image`
- `issue_delete_image`
- `sprint_list`
- `sprint_active`
- `task_assign`
- `task_complete`
- `task_heartbeat`

## Quick start

```bash
cd /home/aaron/.openclaw/workspace/mcp/task-manager-mcp
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python server.py
```

Convenience launcher:

```bash
/home/aaron/.openclaw/scripts/start-task-manager-mcp.sh
```

## All-agent access

Use the shared config in `mcp-client.all-agents.json` to expose Task Manager MCP with per-agent default actor labels:

- `task-manager-main` (`TM_DEFAULT_ACTOR=Jerry`)
- `task-manager-resi` (`TM_DEFAULT_ACTOR=Resi`)
- `task-manager-dwight` (`TM_DEFAULT_ACTOR=Dwight`)
- `task-manager-druck` (`TM_DEFAULT_ACTOR=Druck`)

Write policy:

- all agent profiles in `mcp-client.all-agents.json` are writable (`TM_READ_ONLY=false`)
- write attempts from any intentionally read-only profile fail fast with an explicit error

File:

```bash
/home/aaron/.openclaw/workspace/mcp/task-manager-mcp/mcp-client.all-agents.json
```

Note: native OpenClaw chat flows in `openclaw.json` currently run through skills. MCP configs are for MCP-capable clients/containers and should be wired into those runtimes.

Environment variables:

- `TM_BASE_URL` (default inside the gateway container: `http://taskmanager:8000`)
- `TM_TIMEOUT_SECONDS` (default: `15`)
- `TM_DEFAULT_ACTOR` (default: `Dwight`)
- `TM_READ_ONLY` (`true|false`, default `false`)
- `TM_WRITE_ACTOR` (default `Dwight`, used in read-only error guidance)

## Maintenance workflow (when Task Manager changes)

Treat MCP as a compatibility layer with explicit checks.

Default runtime note:

- Agents running inside the OpenClaw gateway container should use the internal Docker-network origin `http://taskmanager:8000`.
- `https://tm.lidisolutions.ai` is the browser-facing public host and may enforce sign-in and edge filtering that do not apply to internal agent traffic.

1. Make Task Manager backend change in `/home/aaron/.openclaw/workspaces/dwight/rsl-task-manager/`.
2. Run contract check:

```bash
cd /home/aaron/.openclaw/workspace/mcp/task-manager-mcp
python scripts/check_tm_contract.py
```

3. Run smoke test:

```bash
python scripts/smoke_test.py
```

4. If either fails:
- Update `task_manager_contract.json` only for intentional API changes.
- Update `server.py` tool mapping for new/renamed fields or routes.
- Re-run both scripts until clean.

5. Record compatibility note in Task Manager issue/comment for traceability.

## Contract file

`task_manager_contract.json` defines the minimum route/method surface that this MCP server requires.

Current required endpoints:

- `GET /api/issues`
- `POST /api/issues`
- `GET /api/issues/{issue_id}`
- `PATCH /api/issues/{issue_id}`
- `GET /api/issues/search`
- `POST /api/issues/{issue_id}/assign-to-sprint`
- `POST /api/issues/{issue_id}/comments`
- `POST /api/issues/{issue_id}/images`
- `DELETE /api/issues/{issue_id}/images/{image_id}`
- `GET /api/sprints`
- `POST /api/sprints`
- `GET /api/sprints/active`

## Notes

- `task_id` is represented as `issue-<id>` to avoid introducing another persistence layer.
- `task_complete` marks issue status as `done` and writes a completion evidence comment.
- `task_heartbeat` writes a timestamped comment for long-running work.
