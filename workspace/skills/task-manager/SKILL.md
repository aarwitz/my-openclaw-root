---
name: task-manager
description: Manage the RSL Task Manager at http://127.0.0.1:8000 (issues, sprints, comments, assignments, evidence uploads). Use for sprint/backlog execution, not Task Manager source-code changes.
metadata: {"clawdbot":{"emoji":"📋"}}
---

# Task Manager Router (Lean + Deterministic)

Purpose: run deterministic Task Manager operations with minimal prompt overhead.

Do not use this skill for Task Manager codebase changes. For backend/frontend/schema changes, load `task-manager-maintainer`.

## Deterministic First

Prefer structured tool/MCP actions when available for issues/sprints/comments/search.
Fallback to HTTP API calls only when structured tools are unavailable.

Canonical base URL: `http://127.0.0.1:8000`

## Non-Negotiable Story Gates

Before creating any new story, all gates must pass:
1. EWAG value-chain alignment
2. Executable within one sprint
3. No duplicate (search first)
4. High ROI versus current backlog
5. Material enough to matter

If a gate fails, do not create the story.

## Assignment and Branch Rules

- Every actionable story must have `assigned_to`.
- Every code-bearing story must have branch `issue-<id>-<short-slug>`.
- If issue exists without branch, patch branch immediately before coding.
- Before resuming `in_progress`/`in_review` work with linked branch, run:
  `/home/aaron/.openclaw/scripts/reconcile-task-manager-with-git.py --apply`
- If linked branch already landed on `main`, close as `done` instead of reopening.

## Core Operation Contracts

### Create story
- Run duplicate search first.
- Create only if all gates pass.
- Immediately set branch for code stories.

### Update story
- Allowed fields: status, assigned_to, title, description, branch, sprint assignment.
- Status flow: `to_do` -> `in_progress` -> `in_review` -> `done`.

### Progress comment
Keep concise with 3 bullets max:
- changed
- evidence
- next step

### Evidence upload
- For visual changes, attach screenshot evidence to issue/comment.
- Use allowed image types only.

## Output Contract (when reporting to user)

Return:
1. Action performed
2. Issue IDs changed
3. Branch/status/assignee deltas
4. Evidence links if uploaded
5. Exact next action

## On-Demand Deep Reference

For full API examples, story-writing standards, EWAG business context details, and autonomous loop guidance, read:
- `workspace/skills/task-manager/REFERENCE_FULL.md`

Load that file only when needed; keep normal runs on this lean router.
