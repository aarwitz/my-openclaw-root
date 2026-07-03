# Task Manager Workflow

This is the canonical operating workflow for `https://tm.lidisolutions.ai`.

## Core Model

- Hosted Task Manager at `https://tm.lidisolutions.ai` is the only canonical Task Manager runtime.
- Local docker runtime under this workspace is for development and validation only.
- Agents must not switch to localhost, docker aliases, or local DB/API paths when hosted auth or hosted API access fails.

## Accounts

### Owner accounts

- Owner accounts can access `/public-approvals`.
- Owner accounts can approve/reject/disable users.
- Owner accounts can promote approved users to owner via the backend owner-role endpoint.
- At least one owner account must always remain.

### Collaborator accounts

- Collaborators create accounts through `/public-auth`.
- Auto-approved accounts can sign in immediately after verification.
- Non-auto-approved accounts require owner approval.

### Agent-admin account

- `aaronclawrsl@gmail.com` is approved and owner-enabled on the live hosted deployment.
- Owner-role management (`POST /api/public/users/{id}/owner`) is live on `tm.lidisolutions.ai`.
- Canonical deploy source: `/home/aaron/repos/lidi-task-manager`, branch `reconcile-live-owner-fixes-20260702` (worker + frontend are byte-identical to production; deploy only from that reconciled state via `npm run deploy`).

## Issue Lifecycle

### Human-created work

1. Create issue
2. Assign owner
3. Add branch for code work
4. Move through `to_do -> in_progress -> in_review -> done`

### Agent-created work

1. Agent uses hosted API or hosted MCP only.
2. Agent must not fall back to localhost or local DB/API.
3. Agent leaves concise evidence comments.
4. Auto-launch only applies when the issue is explicitly launch-ready.

## Coding Flow

1. Use Task Manager issue as the execution anchor.
2. For code tasks, set `branch` immediately.
3. Reconcile git merge state before resuming linked issues.
4. Keep status aligned with actual code state.
5. Add evidence comments when meaningful work lands.

## Owner Admin Flow

1. Sign in as owner.
2. Open `/public-approvals`.
3. Review pending users.
4. Approve or reject collaborator accounts.
5. Promote approved collaborator to owner when needed.

## Deployment Reality

The current workspace now contains the code for owner-role management, but this workspace does **not** yet contain a wired production deployment target for `tm.lidisolutions.ai`.

Use this command to check whether a real hosted deployment path is configured:

```bash
/home/aaron/.openclaw/workspaces/dwight/task-manager/scripts/tmctl.sh deploy-preflight
```

Expected current result:

- hosted base URL is correct
- production deploy is blocked because this workspace has no dedicated deploy manifest or dedicated upstream deploy repo

## Local Development Flow

1. Edit code in this workspace.
2. Run local validation through `scripts/tmctl.sh`.
3. Verify hosted-only URL guards still hold.
4. Treat local runtime as development-only, never as production truth.

## Commands

```bash
/home/aaron/.openclaw/workspaces/dwight/task-manager/scripts/tmctl.sh status
/home/aaron/.openclaw/workspaces/dwight/task-manager/scripts/tmctl.sh verify
/home/aaron/.openclaw/workspaces/dwight/task-manager/scripts/tmctl.sh deploy-preflight
```

## Non-Negotiables

- Do not use localhost Task Manager endpoints.
- Do not use local DB/API as a substitute for hosted Task Manager.
- Do not silently fall back to docker-exec API behavior.
- Do not treat this workspace as production-deployable until `deploy-preflight` passes.