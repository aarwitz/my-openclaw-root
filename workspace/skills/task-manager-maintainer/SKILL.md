---
name: task-manager-maintainer
description: "Deterministic maintainer workflow for Task Manager source changes across backend model/schema/API and frontend issue/sprint surfaces."
metadata: {"clawdbot":{"emoji":"🔧"}}
---

# Task Manager Maintainer Router (Lean + Deterministic)

Use this skill only for Task Manager source-code changes in `~/repos/Task-Manager/`.
For routine issue/sprint operations through API, use `task-manager`.

## Operation Table

| Operation | Deterministic Steps | Required Validation |
|---|---|---|
| Add/change issue field | update `backend/models.py` -> additive migration in `backend/main.py` -> schema updates in `backend/schemas.py` -> route wiring | `py_compile` + create/update API smoke |
| Change issue create defaults | update create handler logic + schema/default handling + all create modals (`backlog/sprint/issue`) | create issue from each UI path |
| Change sprint/backlog behavior | backend sprint route + issue assignment flow + affected labels/UI text | sprint list/active/end behavior smoke |
| Change metadata rendering | backend response fields + frontend issue/backlog/sprint renderers | field visible in all relevant views |

## Hard Rules

1. Keep backend model, migration, schema, API, and UI payloads in sync in one change set.
2. SQLite migrations must be additive and safe for existing DB.
3. Preserve behavior unless request explicitly changes behavior.
4. Never touch unrelated infra/deploy/auth architecture here unless requested.

## Invariants

- statuses: `to_do`, `in_progress`, `in_review`, `done`
- `sprint_id` nullable means backlog
- optional text fields should trim; empty string -> null

## Execution Sequence

1. Plan affected layers.
2. Backend first: model -> migration -> schemas -> endpoints.
3. Frontend second: all create/edit entry points and metadata displays.
4. Validate and report.

Validation commands:

```bash
python3 -m py_compile backend/main.py backend/models.py backend/schemas.py
pkill -f "uvicorn main:app" || true
cd ~/repos/Task-Manager && nohup bash start.sh > /tmp/task-manager.log 2>&1 &
```

## Output Contract

Return:
1. behavior change summary
2. file list by layer (model/migration/schema/api/ui)
3. validation results
4. residual risks

## On-Demand Deep Reference

For complete repository map, full checklists, pitfalls, and decision rules:
- `workspace/skills/task-manager-maintainer/REFERENCE_FULL.md`
