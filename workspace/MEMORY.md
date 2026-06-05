# MEMORY.md

Durable memory only. Stable preferences, long-term context, recurring constraints.
Use `memory/YYYY-MM-DD.md` for daily logs and short-lived notes.
Procedures and reference data belong in skills or repo docs (not here).

**Last pruned: 2026-05-06** — consolidated EWAG docs, removed duplicated Drive table and skill list.

## Owner

- Name: Aaron (he/him), timezone America/Chicago
- Primary channel: Telegram

## Communication

- Direct, action-first, minimal fluff
- Confirm before external sends, deletes, or shares
- TM stories: outcome-driven with strong acceptance criteria

## Privacy

- Never expose secrets/tokens in chat
- Never share private inbox or Drive content in group chats
- If channel context is unclear, ask before revealing private info

## RSL

- RSL = Aaron, Taylor, Jerry. Jerry is the autonomous engineering org.
- Redstone Laboratories LLC is DBA Lidi Solutions (Aaron and Taylor's business).
- Jerry owns broad business continuity context for Redstone/Lidi and should retain durable high-level awareness of Aaron's life context for correct orchestration.
- Linux gateway is the control plane (Ubuntu, RTX 3060). All non-iOS work is local.
- Task Manager: http://127.0.0.1:8000 (FastAPI + SQLAlchemy + SQLite at `/home/aaron/.openclaw/workspaces/dwight/rsl-task-manager/`, container-owned by Dwight)
- Before resuming a TM issue or branching in EWAG, run `/home/aaron/.openclaw/scripts/reconcile-task-manager-with-git.py --apply`
- iOS work: see `EWAG_INFRA.md` (single source of truth for Mac node, ios-agent, Drive folders, scripts, test catalog)

## Google

- Default account: `aaronclawrsl@gmail.com`
- Act immediately via `gog` — only surface a problem after a live tool failure
- Memory embeddings: local ollama `nomic-embed-text`

## Update Rules

- Add here only if information is stable across weeks/months
- Daily/weekly drift → `memory/YYYY-MM-DD.md`
- Procedures → skills; reference data → repo docs
- Skill catalog and routing live in `AGENTS.md`, not here

## Group / Topic Memory Convention

- Per-group memory: `memory/groups/<channel>--<groupId>.md` (note: double-dash before negative ID)
- Per-topic memory: `memory/groups/<channel>--<groupId>--topic-<topicId>.md`
- Jerry now auto-creates these stubs on first inbound Telegram group/topic message via the `group-topic-memory` hook.
- Stub contents: group name, ID, purpose (one line), durable notes, working defaults, topics table.
- Group files hold ONLY stable group-specific norms. Never copy private 1:1 context.
- See existing files under `memory/groups/` for the template.
