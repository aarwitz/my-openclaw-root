---
name: task-manager-maintainer
description: "Use when editing Task Manager source code or changing Task Manager issue creation, sprint assignment, backlog behavior, issue metadata fields, FastAPI endpoints, backend/main.py, backend/models.py, backend/schemas.py, or matching frontend modals/scripts. Do not use for infra, CI, deployment, unrelated refactors, or routine story/sprint management through the API."
metadata: {"clawdbot":{"emoji":"🔧"}}
---

# Task Manager Maintainer

## Purpose
Apply safe, end-to-end changes to the Task Manager repo where backend API, DB model/migrations, and frontend issue workflows must stay in sync.

**Source code:** `/home/aaron/.openclaw/workspaces/dwight/task-manager/`
**Database:** `/home/aaron/.openclaw/workspaces/dwight/taskmanager.db` (SQLite, persists across restarts)
**Tech stack:** FastAPI + SQLAlchemy + SQLite + vanilla JS

## Use When
- Adding or changing issue fields (example: assignee, sprint, branch).
- Changing issue creation defaults (backlog vs active sprint).
- Updating sprint lifecycle behavior affecting issues.
- Updating create-issue modals or issue detail metadata rendering.
- Fixing backend/frontend contract drift for issue or sprint flows.

## Do Not Use
- CI/CD, Docker, infrastructure, or deployment tasks.
- Broad UI redesign unrelated to issue/sprint workflows.
- Auth architecture changes unless explicitly requested.
- Using the TM API to manage stories/sprints (use `task-manager` skill for that).

## Repository Map

### Backend (Python/FastAPI)

| File | Purpose |
|------|---------|
| `backend/main.py` | API routes, additive SQLite migrations (`run_safe_migrations()`), startup |
| `backend/models.py` | SQLAlchemy ORM models (source of truth for DB schema) |
| `backend/schemas.py` | Pydantic request/response contracts |
| `backend/database.py` | Engine and session setup |

### Frontend (vanilla JS + HTML)

| File | Purpose |
|------|---------|
| `frontend/backlog.html` + `frontend/js/backlog.js` | Backlog view + create-issue modal |
| `frontend/sprint.html` + `frontend/js/sprint.js` | Sprint board + create-issue modal |
| `frontend/issue.html` + `frontend/js/issue.js` | Issue detail view + metadata display |
| `frontend/search.html` + `frontend/js/search.js` | Search view |
| `frontend/css/styles.css` | Shared styling |
| `frontend/index.html` | Main Kanban board |

### Other

| File | Purpose |
|------|---------|
| `start.sh` | Server startup script |
| `requirements.txt` | Python dependencies |

## Hard Requirements
- Keep model, schema, API, and UI payloads synchronized in one change.
- Use additive migrations only (`ALTER TABLE ... ADD COLUMN`) for SQLite compatibility with existing data.
- Preserve existing behavior unless task requires behavior changes.
- Prefer minimal diffs; avoid unrelated formatting.
- Never revert unrelated dirty worktree changes.

## Domain Invariants
- Issue status values: `to_do`, `in_progress`, `in_review`, `done`.
- `sprint_id` is nullable; null means backlog.
- New issue creation should support explicit sprint selection.
- If no sprint is provided, default behavior should use active sprint when policy requires it.
- Optional text fields (e.g. `branch`) must be trimmed; empty string becomes null.

## Required Workflow

### 1. Plan before editing
Read the full request and identify all affected layers before touching code:
- DB model + migration
- Pydantic schemas (create, update, response)
- API handlers
- All issue creation entry points in frontend (there are 3 modals)

### 2. Implement backend first
1. Add field to `models.py` (ORM model)
2. Add additive migration in `run_safe_migrations()` in `main.py`
3. Add field to schemas in `schemas.py` (IssueCreate, IssueUpdate, IssueResponse as needed)
4. Wire field in endpoint logic (create handler, update handler via `setattr` loop)

### 3. Implement frontend second
1. Add/adjust form fields in **all** relevant modals (backlog, sprint, issue detail)
2. Update submission payloads in **all** related JS files
3. Update display surfaces for any new metadata

### 4. Validate
- Python syntax: `python3 -m py_compile backend/main.py backend/models.py backend/schemas.py`
- Restart server and verify:
  ```bash
  pkill -f "uvicorn main:app" || true
  cd /home/aaron/.openclaw/workspaces/dwight/task-manager && ./scripts/tmctl.sh start
  ```
- Smoke test API endpoints with curl

### 5. Report
Summarize: behavior changes, files changed, validation results, residual risks.

## API Contract Checklist

When issue creation/update changes, verify all are true:
- [ ] `POST /api/issues` accepts expected fields
- [ ] `POST /api/issues` applies defaulting rules correctly
- [ ] `POST /api/issues` validates explicit sprint IDs
- [ ] `PATCH /api/issues/{id}` accepts the changed editable fields
- [ ] `IssueResponse` includes newly added fields used by UI
- [ ] Search/list behavior remains consistent for sprint/backlog filters

## Frontend Sync Checklist

For issue creation changes, verify all are true:
- [ ] Backlog modal updated: `frontend/backlog.html` + `frontend/js/backlog.js`
- [ ] Sprint modal updated: `frontend/sprint.html` + `frontend/js/sprint.js`
- [ ] Issue detail updated: `frontend/issue.html` + `frontend/js/issue.js`
- [ ] Sprint selector options load from API and handle active sprint preselection
- [ ] Payload keys match backend schema exactly
- [ ] New metadata is rendered where users expect it

## Common Pitfalls
- Adding schema field but forgetting ORM model + migration.
- Updating one create-issue modal and missing the other two.
- Updating create endpoint but not response schema.
- Not trimming optional inputs before persistence.
- Failing to validate explicit sprint_id.
- Changing sprint behavior without updating user-visible labels.

## Decision Rules
- If request is ambiguous between "auto active sprint" and "force backlog option", ask once and proceed.
- If explicit user policy conflicts with existing behavior, implement policy and note migration/UX impact.
- If a patch corrupts a file, stop and repair immediately before further edits.
