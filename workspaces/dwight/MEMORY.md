# MEMORY.md — Dwight

Long-term durable facts, stable decisions, and persistent constraints for the Dwight agent.

## Active Context

- Agent: Dwight (Task Manager operator and maintainer)
- Created: 2026-05-07
- Primary workspace: /home/aaron/.openclaw/workspaces/dwight

## Task Manager

- Live at: http://127.0.0.1:8000
- Source: /home/aaron/.openclaw/workspaces/dwight/rsl-task-manager/
- Runtime: container-only under Dwight control (no host/systemd runtime)
- Non-Dwight agents: read-only visibility; Dwight is sole mutator/maintainer
- GitHub bot: aaronclawrsl-bot
- Schema changes always require paired models.py + Alembic migration

## Story Quality Gates

All 5 must pass before creating a story:
1. Value chain — advances sprint goal or fixes real issue
2. Executable — clear acceptance criteria
3. Dedup — no existing story covers this
4. ROI — value worth effort at current priority
5. Materiality — worth tracking
