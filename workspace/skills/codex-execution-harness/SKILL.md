---
name: codex-execution-harness
description: Enforce an execution-first coding harness for Telegram/OpenClaw Codex agents. Use for any coding/devops request to improve reliability and throughput: scope tasks, inspect files first, apply minimal edits, validate with targeted checks, and report outcomes with evidence.
metadata: {"clawdbot":{"emoji":"🧰"}}
---

# Codex Execution Harness

This harness makes Telegram agents behave like a high-output coding assistant instead of a raw text model.

## Core Policy

For coding or infra requests, default to execution, not brainstorming.

Always run this loop:

1. Clarify target outcome in one sentence.
2. Gather context with fast reads/search (`rg`, file reads, targeted logs).
3. Make the smallest safe change that satisfies the request.
4. Validate with focused checks (syntax/tests/lint/health/probes).
5. Return concise evidence: what changed, what passed, what remains.

## Tooling Priority

Use specialized skills/tools first, general shell second:

1. Task tracking and orchestration: `task-manager` / `task-manager-maintainer`
2. Repo ops: `github-ssh` (fallback path while MCP parity is still stabilizing)
3. Google workflows: `gog`
4. OpenClaw runtime/config: `openclaw-ops`
5. Shell commands for glue, probes, and validation

Do not skip available domain skills and jump straight to ad-hoc shell scripts.

## Coding Workflow Contract

When asked to implement code changes:

1. Inspect affected files before proposing edits.
2. Prefer one coherent patch per file; avoid unrelated formatting churn.
3. Preserve existing style and public interfaces unless the task requires API changes.
4. Run at least one direct validation relevant to the change:
   - Shell scripts: `bash -n <script>`
   - Python: import/syntax or targeted script run
   - Service integrations: health/smoke probes
5. If validation fails, fix or clearly report the blocker.

## Execution Quality Gates

Before saying done, verify all five:

1. Correctness: change actually satisfies request.
2. Safety: no risky/destructive commands unless explicitly approved.
3. Scope control: no unrelated edits.
4. Evidence: include command outcomes, not assumptions.
5. Operability: include next command the operator should run when useful.

## Telegram Response Shape

Keep responses short and operational:

1. Outcome summary.
2. Concrete changes made.
3. Validation results (pass/fail + key line).
4. Next action (single best step).

Avoid long essays unless explicitly requested.

## Anti-Patterns (Do Not Do)

- One-shot answer without checking files.
- Writing code without running any validation.
- Large refactors when user asked for a small fix.
- Suggesting manual steps that can be executed directly by the agent.
- Claiming success without command evidence.

## Reliability Notes

- Prefer deterministic scripts already in `/home/aaron/.openclaw/scripts`.
- For OpenClaw restarts, use safe operational paths from `openclaw-ops`.
- Treat auth-backed tools as drift-prone: run quick probe before expensive chains.
