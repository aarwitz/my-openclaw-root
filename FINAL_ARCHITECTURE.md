# RSL / Lidi Solutions — Final Agent Architecture (v2)

**Author:** Aaron (aarwitz)
**Date:** 2026-05-23
**Status:** Proposal — design only, no changes made yet
**Supersedes:** `FINAL_ARCHITECTURE.v1.md` (kept for traceability; v1 was over-engineered — see §2 for what changed and why)

---

## 0. TL;DR

We are **not** rebuilding from scratch. We are doing three concrete things on top of the OpenClaw setup we already have, in this order, with rollback at each step:

1. **Isolate the coding agents.** Move the agents that touch source code (Resi today; Dwight when he edits Task Manager code; Jerry when he edits internal tools and openclaw-config; Druck editing skills, api access, and editing stock trading repo code) into Docker containers with git-worktree-per-task. Chat-only sessions stay native in OpenClaw — they don't need the overhead.
2. **Add ACP only where it earns its keep.** Wrap the coding containers with `codex-acp` / `claude-agent-acp` and front them with a small bridge so coding sessions get streaming, inline permission buttons, and edit-review UX. Non-coding agents keep using OpenClaw's native chat path.
3. **Upgrade the supporting fabric.** Adopt three concrete pieces lifted from the field: praktor's encrypted vault (replaces file-mode-600), praktor's per-agent SQLite memory with FTS5+vector hybrid search (replaces today's opaque `*.sqlite`), and telegram-acp-bot's UX patterns (compact/normal/verbose modes, inline permission buttons, stage emoji reactions on the user's message).

Implementation scope stays intentionally small: `task-manager-mcp` and `druck-finance-mcp` as custom MCP services, plus lightweight OpenClaw wiring. Everything else should be OpenClaw-as-is, off-the-shelf binaries, or upstream MCP servers.

The design is not done at "three things" though. §8 specifies the cross-agent orchestration semantics (Dwight as Task Manager orchestrator, with `task.assign` / `task.complete` over the relay hook). §9 specifies the operational spine — concurrency, container lifecycle, networking, observability, cost caps, backup/DR, GitHub-account isolation, hot reload, and a weekly eval loop. A design that doesn't answer those questions isn't a final design.

If, six months from now, somebody proposes adding another bespoke skill that calls `subprocess.run` from inside an agent process — say no, point at the MCP server for it.

---

## 1. Reference architecture audit

Before we design anything new, here is what people who have already done this are shipping. All three target roughly our problem (Telegram-fronted multi-agent personal AI), but with different trade-offs.

### 1.1 ductor — [PleasePrompto/ductor](https://github.com/PleasePrompto/ductor) (377★, MIT, Python)

**What it is.** A Python CLI that runs `claude` / `codex` / `gemini` as subprocesses and proxies them to Telegram or Matrix. Single binary, no Docker required (Docker is an optional *sandbox sidecar*, not the runtime). State is JSON + Markdown under `~/.ductor/`.

**Shape that matters for us:**

- **Multi-transport via `BotProtocol`.** Telegram + Matrix are pluggable; core is transport-agnostic.
- **"Sub-agents"** = a fully separate bot with its own BotFather token, own workspace under `~/.ductor/agents/<name>/`, own CLI auth, own config. This is *exactly* what Jerry/Resi/Druck/Dwight already are in OpenClaw.
- **Named sessions.** `/session <prompt>` inside any chat spawns a side context with its own history; `@<session-name>` follows up. Lets you start "fix the CSV export" without losing your auth thread.
- **Background tasks.** `TASKMEMORY.md` per task, can ask questions back via the agent. Three priorities: interactive / background / batch.
- **Cron / webhooks / heartbeat** all in-process.
- **Cross-tool skill sync** across `~/.claude/`, `~/.codex/`, `~/.gemini/` — same skill is visible to all three CLIs.

**Take.** This is essentially **what OpenClaw already is, in smaller and CLI-only form.** ductor is a strong existence proof that JSON + Markdown + CLI subprocesses is enough for most people; we don't need to leave that paradigm to ship. The features it has that OpenClaw doesn't — *named sessions* and *background tasks with TASKMEMORY.md* — are real UX wins we should steal.

### 1.2 telegram-acp-bot — [mgaitan/telegram-acp-bot](https://github.com/mgaitan/telegram-acp-bot) (11★, alpha, Python)

**What it is.** A thin Telegram ↔ ACP bridge. Bot reads chat, agent speaks ACP, bridge translates. Internal MCP tools for attachments, reactions, and deferred follow-ups (scheduling).

**Shape that matters for us:**

- **Three activity display modes.** `normal` = separate activity messages, no streaming edits. `compact` = one in-progress status message that becomes the final answer. `verbose` = append-only streaming.
- **Inline permission buttons.** Risky tool calls get a permission message with `Always` / `This time` / `Deny`. This is ACP-native.
- **Stage emoji labels.** 💡 Thinking, ⚙️ Running, 📖 Reading, ✏️ Editing, ✍️ Writing, 🌐 Searching web, 🔎 Querying — surfaced as the agent advances.
- **Workspace per `/new`.** Cheap context isolation per conversation.
- **Slash commands:** `/new`, `/resume`, `/session`, `/cancel`, `/stop`, `/clear`, `/restart`, `/mode`.

**Take.** This is **alpha, 11 stars, one maintainer**. We are not adopting it as production infrastructure. But its UX vocabulary is the right one — those three modes, the inline permission buttons, the stage emojis — and we should reproduce that vocabulary inside OpenClaw rather than fork the bridge. The protocol surface (pure ACP) is also the right surface for our actual coding agents (Resi especially).

### 1.3 praktor — [mtzanidakis/praktor](https://github.com/mtzanidakis/praktor) (29★, MIT, Go)

**What it is.** A Go gateway that receives Telegram messages, routes to named agents, each running **Claude Agent SDK in its own Docker container**, with a real-time Mission Control web UI. Self-hosted, single binary, Docker Compose deployment.

**Shape that matters for us — and this is the closest reference architecture to what we want:**

- **Per-agent Docker isolation.** One container per agent, own filesystem. Exactly the model I proposed in v1 §3.3.
- **Per-agent SQLite memory with pragmatic local search.** FTS5 keyword search first. Simple, local, and enough for current single-user scale.
- **`AGENT.md` + `USER.md`** per agent — directly matches our `IDENTITY.md` / `USER.md` / `AGENTS.md` convention.
- **Encrypted vault.** AES-256-GCM, secrets injected as env vars or files at container start, never visible to the LLM. Materially better than our file-mode-600 model.
- **Smart routing in 3 tiers.** `@agent_name` prefix → AI-powered classification by default agent → default fallback. Our `bindings[]` are essentially tier 3 only.
- **Mission Control web UI** with WebSocket live updates. We have the skeleton of this in `~/.openclaw/canvas/`.
- **Scheduled tasks** — cron, interval, one-shot. Up to 3 parallel.
- **Hot config reload** on `praktor.yaml`. Matches our OpenClaw hybrid mode.
- **Nix-on-demand** — agents install ffmpeg, LaTeX, Python packages at runtime via MCP tools or `/nix`. Avoids pre-baking everything into the image.
- **agent-browser** (Vercel) for browser automation. Off-the-shelf.
- **AgentMail** for email. Off-the-shelf.
- **Voice** — Whisper + TTS via OpenAI.
- **Agent swarms** — graph-based fan-out / pipeline / collaborative patterns.
- **Backup/restore** of all Docker volumes as zstd tarballs.

**Take.** Praktor is **the production form of what v1 of this document was trying to describe**, minus ACP. It uses Claude Agent SDK directly instead of going through ACP. That's a real trade-off: praktor is Claude-locked; ACP gives us provider flexibility. But the rest of praktor's choices — Docker per agent, FTS5+vector memory, vault, mission control, hot reload — are all things we should adopt verbatim. The fact that praktor exists and works in production means **we don't have to invent any of this**.

### 1.4 What the audit changes about v1

| v1 claim | Reality from the audit | v2 position |
|---|---|---|
| "ACP is the only protocol between bot and agent." | Praktor (29★) doesn't use ACP at all and is fine. Ductor (377★) doesn't use ACP at all and is fine. Telegram-acp-bot uses ACP and is 11★ alpha. | Use ACP **only for coding agents**, where the permission UI and edit-review pay for themselves. Chat agents stay native. |
| "MCP is the only protocol between agent and tools." | Praktor and ductor both use a mix of native SDK tools, off-the-shelf libraries (agent-browser, AgentMail), and MCP. None enforce purity. | Use MCP for things multiple agents share or for tools that don't have a native equivalent. Don't custom-build MCP servers when a library or an SDK tool exists. |
| "We can predict exact custom code size up front." | Real systems evolve during implementation and maintenance; fixed LOC promises are misleading. | Keep custom scope intentionally narrow: two MCP servers (`task-manager`, `druck-finance`) plus minimal OpenClaw glue. Avoid hard LOC promises. |
| "Build our own bridge based on telegram-acp-bot." | telegram-acp-bot is 11★ alpha with 1 maintainer. ductor (377★, 15 contributors, active) is a more honest "off-the-shelf bridge with batteries". And OpenClaw already does the bridge job. | Don't fork telegram-acp-bot. Keep OpenClaw as the bridge. Use telegram-acp-bot only as a UX reference (the three modes, the permission buttons, the emoji labels). |
| "Retire OpenClaw skills entirely; replace with MCP." | Praktor still has skills/plugins per agent in addition to MCP. ductor keeps skills as files under `workspace/skills/`. Neither retires the skill concept. | Keep OpenClaw skills as the **default tool surface**. Migrate to MCP **only** when (a) a skill is needed by ≥2 agents and currently exists in two places, or (b) the upstream MCP ecosystem already ships it better than our skill (e.g., `@modelcontextprotocol/server-github`). |

---

## 2. What changed from v1

Honest list of things v1 got wrong and v2 corrects:

1. **Over-applied "ACP everywhere".** None of the reference architectures do this; the two most-starred ones don't use ACP at all. Reverted to "ACP for coding agents where the UX matters; native for the rest."
2. **Over-applied "MCP everywhere".** Real systems use MCP plus SDK tools plus off-the-shelf libraries. Reverted to "MCP where it deduplicates or where the ecosystem already ships the server."
3. **Underestimated OpenClaw's role.** v1 demoted OpenClaw to a "control plane" and rebuilt the data plane. The audit shows praktor's gateway does basically what OpenClaw's gateway already does; ductor's core does what OpenClaw's core already does. We keep OpenClaw whole and add to it.
4. **Proposed forking telegram-acp-bot.** The repo is alpha, one maintainer. Bad load-bearing dependency. Treat as UX inspiration only.
5. **Did not name the vault upgrade.** The single most concrete security improvement from the audit is praktor's AES-256-GCM secrets vault. Added in §5.2.
6. **Did not pick a pragmatic memory baseline.** v1 hand-waved on memory. v2 now chooses SQLite + FTS5 first, and defers embeddings/vector until retrieval quality actually requires it.
7. **Migration was too ambitious.** v1 had 6 phases. v2 has 3, and the first one is just "put Resi in a container."

---

## 3. Principles (revised, softer, enforceable)

These are the rules we actually intend to live by. Each one is testable.

1. **Telegram stays the only human ingress.** Hooks (cron, Gmail, webhooks) may create work, but they create it by injecting an ACP-shaped or OpenClaw-shaped prompt into a session — they do not open a second protocol surface to humans.
2. **Coding agents run in containers with git worktrees.** A coding agent is any agent that writes source code that gets committed. Resi always. Dwight when he edits the Task Manager codebase. Jerry when he edits internal tools or openclaw-config. Druck stays out by default: he is the financial manager and only enters coding mode when explicitly doing trading-system development.
3. **ACP is the protocol for coding agents.** Permission buttons, edit review, streaming, sub-agents — ACP earns its keep here. The bridge that fronts these containers is the only place we add new protocol code.
4. **For non-coding agents, OpenClaw's existing native path is fine.** Druck (financial management and market operations), Jerry (internal tools, routing, and business-internal context), and Dwight (strict Task Manager orchestration) talk through OpenClaw as today.
5. **MCP for shared and ecosystem tools; OpenClaw skills for one-off and local.** A new MCP server is justified when it would otherwise become two skills (one per agent) or when the upstream ecosystem ships it. Otherwise, keep using OpenClaw skills.
6. **Secrets go through the vault, not through file-mode-600.** Existing file-backed secrets stay where they are during migration; new secrets are vault-injected.
7. **Memory is per-agent, queryable, and durable.** Each agent has its own SQLite with FTS5. The markdown daily logs remain the human-readable surface and are imported into SQLite nightly.
8. **Off-the-shelf > custom, and custom scope stays small.** Prefer upstream tools and simple adapters over bespoke frameworks.
9. **Hot reload, backups, never auto-restart Codex.** All the discipline from `memories/openclaw-gateway-rules.md` carries over verbatim. Containers use `on-failure:5`, never `always`.
10. **Transparency without noise.** Default activity mode is `compact` (one editable status message), reactions for stage, separate messages only for permission prompts and final answers. Alerts go to a dashboard, not to chat.

---

## 4. Current state (kept from v1, lightly refreshed)

- **Gateway:** OpenClaw 2026.4.15 on loopback:18789, file-backed token, hot-reload hybrid. Hardened per `memories/repo/hardening-2026-04-16.md`.
- **Agents:** 4 personas — `main`/Jerry, `druck`, `dwight`, `resi`. Four Telegram bot accounts. Routed via `bindings[]`.
- **Workspaces:** `~/.openclaw/workspace/` (Jerry/main) and `~/.openclaw/workspaces/{druck,dwight,resi}/` with the `IDENTITY.md` / `SOUL.md` / `AGENTS.md` / `USER.md` / `MEMORY.md` convention. Druck additionally has `phase2/` (trading code) co-located.
- **Models:** mostly `openai-codex/gpt-5.4`; Dwight is on `openai/gpt-5.4`. Codex single-use refresh token fragility is known and managed.
- **Sandbox:** off. Exec on host.
- **MCP servers configured:** zero. Tool surface today is 31/71 OpenClaw skills.
- **Memory:** per-agent SQLite (`memory/{main,druck,dwight,resi}.sqlite`) — opaque, no schema docs, no semantic search. Markdown daily logs + `MEMORY.md` are the human surface.
- **Hooks:** `credential-preflight`, `newsapi-credentials`, `group-topic-memory`, `telegram-agent-relay`.
- **What's working we keep:** Telegram multi-account routing, group/topic policy, hot reload, safe-restart, file-backed secrets, credential-preflight, persona files, daily markdown memory, cron + webhooks + hooks as event triggers, telegram-agent-relay semantics (`@peer` reply / `FYI @peer` listen).
- **What hurts we fix:** no isolation between agents; coding work mutates the shared host filesystem; no concurrent-task safety on repos; memory is opaque; secrets are file-permission-protected only; permission UX is "the agent decides + audit later"; no smart routing tier above static bindings.

---

## 5. Target architecture

### 5.1 The picture

```
┌──────────────────────────────────────────────────────────────────────────┐
│                   LAYER 1 — INGRESS (unchanged)                           │
│  Telegram (4 bot accounts: jerry, resi, druck, dwight)                    │
└──────────────────────────────┬───────────────────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────────────────┐
│              LAYER 2 — OPENCLAW GATEWAY (kept, hardened)                  │
│   • Channels, bindings, hooks, cron, webhooks, doctor, dashboard          │
│   • Secrets vault (NEW: AES-256-GCM upgrade alongside file-backed)        │
│   • Smart routing (NEW: @agent → AI-classify → default tier)              │
│                                                                           │
│  Native path                                  ACP path                    │
│  (chat, financial ops, internal ops)          (coding sessions)            │
└──────────┬───────────────────────────────────┬───────────────────────────┘
           │                                   │ ACP/stdio
           ▼                                   ▼
┌────────────────────────────┐ ┌────────────────────────────────────────────┐
│   LAYER 3a — NATIVE AGENTS │ │ LAYER 3b — CODING AGENTS (NEW)              │
│   (in-process, as today)    │ │ Docker containers, one per coding session  │
│   • jerry (chat, ops)       │ │   • resi-coder    (claude-agent-acp)        │
│   • druck (financial mgr)   │ │   • dwight-coder  (claude-agent-acp, opt.) │
│   • dwight (TM orchestrator)│ │   • jerry-coder   (codex-acp, opt.)         │
│                             │ │ git worktree per task; chromium per agent  │
└────────────┬───────────────┘ └────────────────────────┬────────────────────┘
             │ OpenClaw skills                          │ MCP (mixed transports)
             ▼                                          ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                       LAYER 4 — TOOL FABRIC                               │
│                                                                           │
│  OpenClaw skills (kept as default for native agents):                     │
│     31 ready today (alpaca, github, github-ssh, schwab, taskflow, ewag-*, │
│     newsapi-ai, ...) — no migration required                              │
│                                                                           │
│  MCP servers (NEW, only for coding agents):                               │
│     SHARED HTTP:                                                          │
│       task-manager-mcp     ← custom                                           │
│       druck-finance-mcp    ← custom (paper-only by default)                  │
│       github-mcp           ← upstream @modelcontextprotocol/server-github  │
│       shared-memory-mcp    ← thin wrapper (shared across agents)            │
│     PER-SESSION STDIO (launched by container):                            │
│       fs-mcp, git-mcp, bash-mcp  ← from MCP community                      │
│       agent-browser        ← Vercel's library                              │
└──────────────────────────────────────────────────────────────────────────┘
```

### 5.2 Per-layer detail

**Layer 1 — Ingress.** Unchanged. OpenClaw's `channels.telegram.accounts.{default,druck,dwight,resi}` and `bindings[]` keep doing what they do.

**Layer 2 — OpenClaw gateway, kept and upgraded.** OpenClaw remains the single process that owns: channel I/O, routing, hooks, cron, webhooks, secrets, doctor, sessions for native agents, dashboard. Three concrete additions:

- **Vault upgrade.** Add an AES-256-GCM secrets provider next to today's `env`/`file`/`exec` providers. New secrets go into the vault. Existing file-backed secrets stay until rotation. Master key is a passphrase on the host, loaded into the gateway at startup (and into agent containers at start time via the same loader).
- **Smart router.** Add a routing tier above `bindings[]` so a Telegram message in the shared "RSL Ops" group can land on the right agent: (1) explicit `@agent_name` prefix wins; (2) if Jerry is the default and the message is ambiguous, Jerry runs a 1-shot classification prompt and forwards; (3) otherwise Jerry handles it. This replaces today's static binding for shared groups.
- **Coder dispatcher.** A new OpenClaw subcommand (or hook) `openclaw coder spawn --agent resi --task <id>` that builds a worktree, brings up the agent container, and bridges its ACP stdio to the Telegram session that asked for the work. This is the only new piece of OpenClaw-side glue we write.

**Layer 3a — Native agents.** No change for Druck, no change for day-to-day Jerry, no change for Dwight when he's operating Task Manager orchestration. They keep running in-process, keep using OpenClaw skills, keep their existing memory file, keep their persona docs. We are not breaking what works.

**Layer 3b — Coding containers (the new part).** When a coding task is needed — "Resi, finish PR #142 on resilife-ios" — the dispatcher:

1. Creates a worktree: `git -C ~/.openclaw/repos/<agent>/<repo>.git worktree add ~/.openclaw/repos/<agent>/worktrees/<task-id> <branch>`
2. Brings up `agent-resi-coder` (Docker, `claude-agent-acp` inside) with the worktree mounted as the working directory, the vault-resolved credentials mounted read-only, a chromium for `agent-browser`, and a stdio MCP bundle (fs, git, bash, browser).
3. Connects the container's ACP stdio to the Telegram session — streaming, permission buttons, edit review all light up.
4. On `/done` or session timeout: commits/pushes any in-progress work, prunes the worktree, tears down the container.

Containers are *per-task*, not *per-agent-forever*. Idle containers cost nothing because they don't exist.

**Layer 4 — Tool fabric.** OpenClaw skills remain the default tool surface for the native agents. The MCP fabric is small and only used by coding containers:

- **`task-manager-mcp`** — RSL Task Manager API behind MCP. Shared by Resi (writes evidence, updates story status), Jerry (creates stories), Dwight (admin). Replaces today's two separate skill installs (`task-manager` and `task-manager-maintainer`).
- **`druck-finance-mcp`** — Schwab / Alpaca / FMP / Finnhub / Massive behind MCP. Druck calls this *from his native OpenClaw session* (not from a container); also reachable from a "jerry-coder" container if Jerry needs to write trading code. Live trading is permission-gated through ACP; paper trading is the default.
- **`shared-memory-mcp`** — shared cross-agent memory (who Aron is, what RSL means, current sprint goals, recent decisions). Read at session start by every agent (native or container).
- **`github-mcp`** — upstream `@modelcontextprotocol/server-github` from the official MCP org. Not custom.
- **Per-session stdio MCP** — `fs`, `git`, `bash`, `agent-browser`. All upstream packages launched by the container's entrypoint.

That's the whole MCP server list: two custom servers (`task-manager-mcp`, `druck-finance-mcp`), one small shared-memory shim (`shared-memory-mcp`), one upstream server, and upstream stdio servers.

### 5.3 Memory model (pragmatic single-user baseline)

Per-agent SQLite at `~/.openclaw/memory/<agent>.sqlite`, schema:

```sql
-- Free-form facts ingested from sessions, daily logs, and explicit /remember calls
CREATE TABLE facts (
  id INTEGER PRIMARY KEY,
  text TEXT NOT NULL,
  source TEXT,           -- 'session:<id>', 'daily:<date>', 'manual', 'graph'
  ts INTEGER NOT NULL,
  agent TEXT NOT NULL
);
CREATE VIRTUAL TABLE facts_fts USING fts5(text, content='facts', content_rowid='id');

-- Structured task records
CREATE TABLE tasks (
  id TEXT PRIMARY KEY,
  status TEXT,
  owner TEXT,
  repo TEXT,
  worktree TEXT,
  started INTEGER,
  ended INTEGER,
  outcome TEXT
);

-- Decision journal
CREATE TABLE decisions (
  id INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL,
  context TEXT,
  choice TEXT,
  rationale TEXT
);
```

- Retrieval for v1/v2 is FTS5-only. This is intentionally simple for personal scale and avoids embedding/model/vector dependencies.
- Optional future upgrade path: add embeddings + vector search only if real retrieval misses appear in production.
- Daily markdown logs (`workspaces/<agent>/memory/YYYY-MM-DD.md`) continue to be written by hand and by the agent. A nightly job reads new lines and inserts into `facts` with `source='daily:<date>'`.

Cross-agent shared knowledge lives in **one** additional SQLite at `~/.openclaw/memory/shared.sqlite` with the same schema, behind `shared-memory-mcp`. Every agent reads at session-start, writes are rate-limited (one fact per session) to prevent noise.

### 5.4 Secrets vault (concrete, lifted from praktor)

- New OpenClaw secret provider `provider: "vault"`, backed by `~/.openclaw/credentials/vault.enc` (AES-256-GCM).
- Master key: passphrase, loaded into the gateway at startup from `~/.openclaw/credentials/.vault-pass` (mode 600), and into agent containers at start time by the entrypoint script that reads `/run/secrets/vault-pass`.
- CLI:
  - `openclaw vault set <key>` — prompts for value, encrypts, writes.
  - `openclaw vault inject --container <name> --as ENV_NAME --key <key>` — injects into a running container as env var or file.
  - `openclaw vault list` — keys only, never values.
- Migration: file-backed secrets stay where they are. New secrets go to vault. Annual rotation moves the old ones over.
- LLM is **never** shown vault values directly; MCP tool calls reference secrets by *name* and the MCP server resolves them at call time.

### 5.5 Routing (concrete, lifted from praktor's 3-tier)

In any chat (DM or group), incoming message is routed to an agent by:

1. **Explicit prefix.** Message starts with `@jerry`, `@resi`, `@druck`, `@dwight` → that agent. (Today this only works in groups via the relay hook; v2 makes it universal.)
2. **AI classification.** If no prefix and the chat is configured with `routing: smart`, the default agent (Jerry) runs a 1-shot classifier prompt and forwards to the matched agent. Classifier is small and fast (could be the same model the chat uses, with a tight instruction).
3. **Default agent.** If classification is ambiguous or `routing: static`, the chat's bound agent handles it (today's `bindings[]` behavior).

The `telegram-agent-relay` hook becomes a special case of tier 1 — replaced by a tier-1 dispatch in the router, not a separate hook.

---

## 6. Per-agent footprint (revised)

Canonical identity guardrail for this architecture: Jerry, Dwight, and Druck are male personas; Resi is a female persona.

### 6.1 Jerry (`@a_rslbot`)

- **Default mode:** native, OpenClaw skills (today's setup).
- **Coding mode:** when asked to edit `openclaw-config` or write a script, spawns `jerry-coder` (codex-acp container).
- **Tools:** OpenClaw skills today; `shared-memory-mcp`, `task-manager-mcp` when in coding mode.
- **Special:** he develops internal tools, owns smart-router classification when group routing is ambiguous, and is the agent with deepest business-internal context.

### 6.2 Resi (`@resi_rslbot`)

- **Default mode:** **always container** (claude-agent-acp). Resi is the only agent for whom every meaningful task is a coding task — iOS, EWAG website, builds, screenshots. Native chat exists only for status / Q&A and stays in OpenClaw.
- **Repos:** `resilife-ios`, `ewag-website`.
- **Tools:** `task-manager-mcp`, `github-mcp` (account-routed to `EWAG-dev`), stdio fs/git/bash, `agent-browser`, plus a `qa-bridge` MCP that wraps today's `ewag-capture.sh` / `ewag-test.sh` (so the iOS build/screenshot loop is reachable as MCP calls, but the actual shell scripts stay).
- **Special:** worktree-per-PR. Two simultaneous Resi tasks no longer collide.

### 6.3 Druck (`@druck_rslbot`)

- **Default mode:** native, OpenClaw skills (today's setup).
- **Coding mode:** when actively developing `phase2/` (the trading research code), spawns `druck-coder` (codex-acp container).
- **Tools:** `druck-finance-mcp` (in both modes), `shared-memory-mcp`, today's news/research skills.
- **Special:** he is the official financial manager; `druck-finance-mcp` defaults to paper trading, and live Schwab calls require ACP permission button approval per call.

### 6.4 Dwight (`@dwight_rslbot`)

- **Default mode:** native (official Task Manager orchestrator).
- **Coding mode:** when maintaining `task-manager` codebase or migrations, spawns `dwight-coder` (claude-agent-acp container) — same Claude model he uses for native chat, just containerized.
- **Tools:** `task-manager-mcp` (admin scope, server-side ACL), `github-mcp`, `shared-memory-mcp`.
- **Special:** he is the official Task Manager orchestrator and developer; the `task-manager-maintainer` capability becomes an MCP server-side ACL, not a separate skill load. His communication style is intentionally strict, checklist-heavy, and lightly comedic (Dwight-from-The-Office energy) without sacrificing clarity.

### 6.5 A future fifth agent (template)

To add a customer-support or marketing agent:

1. New BotFather token, registered as `channels.telegram.accounts.<name>`.
2. New workspace `workspaces/<name>/` with `IDENTITY.md` etc.
3. New `bindings[]` entry + `mentionPatterns`.
4. Pick: native (chat-only) or container (coding) by default.
5. Done. No new code.

---

## 7. Feedback and UX (concrete, lifted from telegram-acp-bot)

Replace today's "agent posts whatever it wants" with the three-mode vocabulary, default `compact`:

| Mode | Behavior | Default for |
|---|---|---|
| `compact` | Single message that updates in place from `💡 Thinking` → `⚙️ Running` → final answer. One notification. | All DMs and most groups. |
| `normal` | Separate activity messages, no streaming edits. Final answer is its own message. | When the user explicitly wants the breadcrumb trail visible. |
| `verbose` | Append-only streaming, every tool call, every progress note. | Debugging only. |

Stage emoji vocabulary, applied as Telegram reactions on the *user's* message (not new bot messages):

- 💡 thinking / planning
- ⚙️ executing a tool
- 📖 reading files
- ✏️ editing files
- ✍️ writing new content
- 🌐 fetching web / searching
- 🔎 querying APIs
- ✅ done
- ❌ failed

Permission requests are **always** separate messages with inline buttons `Always`, `This time`, `Deny` — never inlined into a status edit. Pre-approved sets per session (an ACP capability) handle the common case so we don't ask 50 times.

Slash commands we adopt across both native and ACP paths:

- `/new` — fresh context
- `/session <prompt>` — named side-context (ductor pattern)
- `/sessions` — list active
- `/cancel`, `/stop`, `/stop_all`
- `/mode {compact|normal|verbose}` — per chat
- `/status`, `/memory`, `/showfiles`, `/diagnose`, `/where`, `/agents`

OpenClaw already supports several of these; we standardize the rest.

Alerts (auth drift, container crash loop, MCP server down, drift-check fail) go to the dashboard (loopback HTML at `~/.openclaw/canvas/index.html`, served by the gateway), **not** to chat. Only true human-attention items get a DM to Aron.

---

## 8. Cross-agent orchestration

Today, cross-agent coordination happens through the `telegram-agent-relay` hook (`@peer ...` to ask, `FYI @peer` to inform). It works and we keep it as the *transport*. What we add is the *semantics*, so that "Jerry, have Resi finish PR 142" is a structured task hand-off and not a Telegram message guess.

### 8.1 Dwight as task orchestrator

Dwight is the default task orchestrator for goals that map to other agents' work. The pattern:

1. Aron tells Dwight a goal in plain English.
2. Dwight decides: do I handle this inside Task Manager, or does this belong to Resi / Druck / Jerry for execution?
3. If it's a hand-off, Dwight calls `task-manager-mcp::task.assign(owner='resi', goal='…', context=[…], origin_chat=<id>)`. This:
   - Creates a row in the `tasks` table (the agent-shared one, see §5.3).
   - Fires a relay message to the target agent (the existing hook path).
  - Returns a task-id Dwight can echo back to Aron ("OK, assigned to Resi as `task-9f2a`").
4. The target agent picks it up via the relay, reads the task row for full context, does the work in their own session (containerized if coding, native if chat).
5. On completion, the target agent calls `task.complete(id, outcome='merged PR #142', artifacts=['https://github.com/…/pull/142'])`. This fires a relay back to the origin chat with a tagged update.
6. Dwight sees the relay, updates Task Manager state, and summarizes for Aron in the original conversation.

This works whether the target is native or in a container. It works whether the origin is Aron or another agent (for example, Dwight can route a financial check to Druck). It uses the relay hook for transport and the MCP server for state.

### 8.2 What is *not* in scope

- **No agent message bus.** No Redis, no NATS, no pub/sub broker. Cross-agent is point-to-point through the relay, persisted in `tasks`.
- **No ACP-over-network agent-to-agent.** ACP sub-agents inside a single container are fine (claude-agent-acp supports them); ACP between *different* agents would require both ends containerized and a connection-management layer we don't want to write.
- **No autonomous goal decomposition by any agent.** Dwight dispatches when Aron asks him to, or when an inbound hook explicitly says "assign to <agent>". No autonomous task spawning.

### 8.3 Concrete examples

- *"Dwight, have Resi finish PR 142 on resilife-ios."* → Dwight: `task.assign(owner='resi', goal='Finish PR #142 on resilife-ios', context=['https://github.com/…/pull/142'])` → relay fires → Resi's binding receives, spawns `agent-resi-coder` container with worktree `resi/task-9f2a-pr-142`, opens ACP stream, posts permission prompts as needed → on merge, Resi calls `task.complete(...)` → Dwight sees the relay, updates TM, replies in Aron's original DM.
- *Cron fires Druck's morning pre-market job.* → Cron writes `task.assign(owner='druck', goal='Pre-market scan: SPY, QQQ, IWM', context=[...], origin='cron:premarket-0830')` → Dwight tracks it in TM, Druck runs it natively, then calls `task.complete`.
- *Gmail webhook says a customer emailed support.* → Hook writes `task.assign(owner='jerry', goal='Triage customer email', context=[gmail_thread_url])` → Dwight tracks assignment, Jerry executes and reports back.

---

## 9. Operational design

This is the spine that makes the design survive contact with reality.

### 9.1 Concurrency model

- **Per-agent message queue.** Each agent (native or containerized) processes one Telegram message at a time by default. Inbound messages while busy receive an immediate 👀 reaction and queue.
- **Coding agents: parallel tasks, not parallel messages.** Up to **3 simultaneous active worktrees per coding agent**, each in its own container. A 4th task queues until one completes. The single in-chat session is still one-at-a-time; parallel tasks happen in the background and are tracked via the `tasks` table.
- **Idle reaper.** A container with no ACP activity for 15 minutes is killed (worktree preserved). Background work that legitimately runs longer (a 30-min build) calls `task.heartbeat(id)` every 5 min via the task MCP to defer the timer; the entrypoint script also defers on `git`/`npm`/`pip`/`xcodebuild` process activity.
- **Hard task cap.** No more than 8 coding containers total across the whole system at once (system-wide RAM/CPU sanity bound). Configurable.

### 9.2 Worktree & container lifecycle

- **Branch naming:** `<agent>/<task-id>-<short-slug>`, e.g. `resi/task-9f2a-pr-142`.
- **Worktree creation:** `git worktree add ~/.openclaw/repos/<agent>/worktrees/<task-id> <branch>` at task start; entrypoint mounts that directory as the container's `/workspace`.
- **Worktree GC (daily cron):** prune any worktree whose (a) PR is merged or closed, OR (b) remote branch is deleted, OR (c) has had no commits and no container activity for 14 days. Pruning is `git worktree remove --force`; the branch is preserved on the remote if it still exists.
- **Container lifecycle:** built on demand from a small set of base images (`Dockerfile.agent-claude`, `Dockerfile.agent-codex`). Pull/build happens lazily; cached. Container is `docker rm`'d on idle-reap or task-complete; no persistent agent containers between tasks.
- **Disk pressure alert:** daily cron checks `~/.openclaw/repos/` total usage; warns on dashboard at 80% of a configured cap (default 50 GB).

### 9.3 Networking & egress policy

- **Each container gets its own Docker network.** No container can see another container's network.
- **Egress allowlist by default:** github.com, api.github.com, codeload.github.com, raw.githubusercontent.com, registry.npmjs.org, pypi.org, files.pythonhosted.org, the Anthropic / OpenAI / Google model API hosts, and `host.docker.internal` for reaching host-side MCP servers. Everything else blocked.
- **Per-container overrides:** `network: open` (full egress) for cases where a build legitimately needs more — annotated in the agent's container config with a one-line reason.
- **Host-side MCP reachability:** Linux containers get `--add-host=host.docker.internal:host-gateway` and the egress allowlist includes it; MCP servers bind to `127.0.0.1` on the host and accept from the docker bridge IP only.
- **DNS:** containers use the host's resolver — no DoH/DoT inside the container by default.

### 9.4 GitHub account isolation

This is its own subsection because cross-account bleed is the failure mode most likely to embarrass us.

- Each agent has a vault-stored SSH key pair and a vault-stored gh CLI token, namespaced by `<agent>:<gh-account>`. Today: `resi:ewag-dev`, `jerry:lidi-aaron`, `dwight:lidi-aaron`, `druck:lidi-aaron` (Druck shares Aron's account for research repos; he doesn't push code by default).
- Container entrypoint at startup:
  1. Resolves `vault:ssh:<agent>:<gh-account>` and writes it to `/root/.ssh/id_ed25519` mode 600.
  2. Writes `/root/.ssh/config` with `Host github.com → IdentityFile ~/.ssh/id_ed25519 User git IdentitiesOnly yes`.
  3. Resolves `vault:github:<agent>:<gh-account>` and runs `gh auth login --with-token`.
  4. Sets git config: `user.name` and `user.email` from `workspaces/<agent>/IDENTITY.md` front-matter.
- The existing `gh-account-router.sh` and `github-credential-router.sh` scripts become wrappers around this entrypoint logic for native (non-container) work; same source of truth.
- A commit signed under the wrong account is a P1 alert (dashboard + DM); the daily cron audits the last 50 commits per agent and verifies author/committer match expected identity.

### 9.5 Observability

- **Structured logs.** Every tool call, MCP request/response (payloads truncated to 2 KB), permission decision, container spawn/kill: one JSONL line at `~/.openclaw/logs/<agent>/<date>.jsonl`. Existing `logs/config-audit.jsonl` schema extended.
- **Append-only audit log.** `~/.openclaw/logs/audit.jsonl` records every: secret read (by name, never value), permission grant, vault unlock, container start/stop, cross-agent dispatch, model auth token refresh. Daily archived to `audit/<date>.jsonl.zst`.
- **Daily digest.** Cron at 06:00 writes `~/.openclaw/logs/daily-<date>.md`: tasks completed, tasks failed, tokens spent per agent, secrets accessed, anomalies. This is the file Aron skims with coffee.
- **Dashboard.** Existing `~/.openclaw/canvas/index.html` extended with live tiles: per-agent message rate, active tasks, container count, queue depth, today's spend, alerts panel. Loopback only, served by the gateway.
- **Tracing.** Out of scope for v1. Logs are enough; OpenTelemetry is a future addition.

### 9.6 Cost controls

- **Per-agent monthly budget** in `~/.openclaw/budgets.json`. Soft warn at 80% (Telegram DM to Aron), hard refuse new sessions at 100% (existing sessions complete; user gets a message; can be overridden with `/override-budget`).
- **Per-task token cap.** A coding container that consumes more than its budget-multiplier without calling `task.heartbeat` is auto-killed with `outcome='token-cap-exceeded'`. Defaults: Resi 400k tokens/task, Dwight/Jerry 200k.
- **Container resource caps.** `--memory=4g --cpus=2` default. Per-agent override in config.
- **Disk quota** as in §9.2.
- **API rate limits.** `druck-finance-mcp` enforces server-side rate limits per provider (Schwab, Alpaca, FMP) regardless of what Druck asks. Caching layer on read paths.

### 9.7 Backup & disaster recovery

- **Daily backup cron** (existing `scripts/token-backup.sh` extended): vault + all `memory/*.sqlite` + `workspaces/*/MEMORY.md` + `workspaces/*/IDENTITY.md` + `openclaw.json` + `cron/jobs.json` → `~/.openclaw/backups/<date>.tar.zst`. Retain 30 daily, 12 monthly.
- **Off-host copy:** optional rsync to a second machine if/when Aron has one. Documented in RUNBOOK.md; not required.
- **Vault master key backup.** Printed QR code in a physical safe. Without it, all encrypted vault contents are unrecoverable on host loss. This is the single most important paper artifact in the system.
- **Restore drill:** quarterly. Stand up a fresh VM, restore latest backup, verify a session round-trip per agent. Tracked as a Dwight-owned recurring Task Manager task.
- **Per-task rollback.** Worktrees make this trivial: a botched Resi task is `git worktree remove` + branch delete; the main repo is never touched.

### 9.8 Persona hot reload

- `workspaces/<agent>/{IDENTITY,SOUL,USER,AGENTS,TOOLS,MEMORY,HEARTBEAT}.md` continue to be read at session start.
- Native agents pick up changes on the next message (current behavior).
- Container agents pick up changes on the next *task* (each task is a fresh container, so each task reads fresh files via the bind-mount). For mid-task updates, the `/restart` slash command tears down and re-spawns the container preserving the worktree.
- `IDENTITY.md` front-matter (YAML) holds the structured fields used by entrypoint scripts: `git_user_name`, `git_user_email`, `github_account`, `model`, `acp_command`. Source of truth.

### 9.9 Evals & regression

Without evals we don't know when an agent silently degrades.

- **Per-agent golden set:** 10–20 canonical prompts with expected behavioral assertions (not exact text). Stored as `workspaces/<agent>/evals/*.yaml`.
- **Weekly cron** runs the set against the live agent in a dedicated test session (sandbox repos / paper-trading endpoints), records pass/fail and token cost, posts to the dashboard. Failures trigger a DM only on second consecutive week.
- **Druck-specific:** paper-trading backtest harness runs nightly against the previous day's market data; P&L tracked over time as a sparkline on the dashboard.
- **Resi-specific:** weekly "bootstrap a fresh PR for a known-good story and merge it" against a sandbox repo.
- **Drift detection:** model + ACP version + MCP server versions are recorded with each eval run; a regression that correlates with a version bump is auto-flagged.

### 9.10 The single entry-point document

- `~/.openclaw/RUNBOOK.md` is the file future-Aron reads when something breaks. Sections: where things live, how to bring the gateway up/down safely (link to `safe-restart.sh`), how to add an agent, how to add an MCP server, how to rotate a secret, how to restore from backup, how to read the audit log.
- **This document (FINAL_ARCHITECTURE.md) explains *why*. RUNBOOK.md explains *how*.** Both kept in sync — a phase-3 deliverable is the first RUNBOOK draft.
- `memories/repo/openclaw-config-deep-dive.md` and `memories/openclaw-gateway-rules.md` continue to be the agent-facing operational memory; RUNBOOK is the human-facing one.

---

## 10. Migration plan (3 phases, smaller than v1)

Each phase is independently shippable and reversible. Production keeps working at every step.

### Phase 1 — Resi in a coding container

**Goal:** prove the container + ACP + worktree loop with one agent before touching anything else.

- Pick `agent-resi-coder` as the first container. Build `Dockerfile.agent-claude` on top of a base image with python+node+git+chromium.
- Bring up the container via `docker compose up -d agent-resi-coder` (managed by OpenClaw via a new `openclaw coder` subcommand or by a hook on `command:start_coding`).
- Write the `task-manager-mcp` server (FastMCP) and the `github-mcp` server config (upstream package, no code).
- Wire the ACP bridge: when Resi receives a coding intent, OpenClaw spawns the container, opens the ACP stream, and pipes Resi's Telegram session to it.
- **Operational floor for Phase 1 (cheap, do them now, not later):**
  - 👀 ack-reaction on every inbound message before any work starts (§9.1).
  - Per-agent message queue (depth 1 for Resi initially).
  - Container `--memory=4g --cpus=2` caps and `on-failure:5` restart policy.
  - Egress allowlist (§9.3) on Resi's container.
  - Audit log scaffold: `~/.openclaw/logs/audit.jsonl` capturing container spawn/kill and permission decisions (§9.5).
  - GitHub-account entrypoint (§9.4) wired for `resi:ewag-dev` — do **not** skip this; it's the single highest-blast-radius bug.
- Live-test for one full PR cycle (story → branch → code → screenshots → PR → merge).
- **Reversibility:** if it breaks, Resi falls back to native OpenClaw immediately. Nothing about the existing setup changes.

### Phase 2 — Memory upgrade + UX upgrade + cross-agent orchestration

**Goal:** every agent (native or container) gets searchable memory and the 3-mode UX, plus the operational floor that makes the system reviewable.

- Define the SQLite schema (§5.3). Migration script reads existing per-agent `*.sqlite` and daily markdown into the new schema, writes to a new file, keeps the old file as backup.
- Keep memory stack simple: SQLite + FTS5 only.
- Stand up `shared-memory-mcp` (single shared `shared.sqlite`); add session-start read to all agents.
- Implement the compact/normal/verbose mode rendering in OpenClaw's response layer; add the stage-emoji reactions; add `/mode` slash command.
- **Cross-agent orchestration goes live:** stand up the `tasks` table and the `task.assign` / `task.complete` / `task.heartbeat` MCP calls (§8). Dwight handles explicit hand-offs only ("Dwight, have Resi do X") — auto-classification stays off.
- **Operational additions:**
  - Daily backup cron writing `~/.openclaw/backups/<date>.tar.zst` (§9.7). Test restore on a scratch directory before declaring Phase 2 done.
  - Daily digest cron writing `~/.openclaw/logs/daily-<date>.md` (§9.5).
  - Dashboard tiles for active tasks, queue depth, today's spend (§9.5).
  - Golden-set scaffold per agent: empty `workspaces/<agent>/evals/` + 3 starter prompts each, manually validated (§9.9). Weekly eval cron not yet enabled.
- **Reversibility:** old sqlite files are kept untouched. Mode defaults to compact but `/mode normal` returns to today's behavior. `shared-memory-mcp` is read-only at first; writes are opt-in.

### Phase 3 — Vault + smart routing + remaining coding containers

**Goal:** harden secrets and routing; extend the container pattern to Dwight and Jerry on demand.

- Add the AES-256-GCM vault provider. Rotate one secret as proof.
- Add the smart router. Default to today's static behavior; opt-in `routing: smart` per chat/group.
- Stand up `dwight-coder` and `jerry-coder` on the same pattern as Resi. Stand up `druck-coder` only when actually needed.
- Write `druck-finance-mcp`.
- **Operational close-out:**
  - Weekly eval cron enabled; golden sets grown to 10–20 prompts per agent (§9.9).
  - Druck nightly paper-trading backtest live (§9.9).
  - GitHub-account-audit daily cron live (§9.4).
  - First draft of `~/.openclaw/RUNBOOK.md` written (§9.10).
  - First quarterly restore drill executed and documented.
  - Smart router promoted from "shared group only" to default — *only if* the month of opt-in data was clean.
- **Reversibility:** every change is a new provider/router-tier/container that can be removed without affecting the rest.

After phase 3 we **stop**. We do **not** retire OpenClaw skills, we do **not** retire the `telegram-agent-relay` hook (it remains the cross-agent reply path until something obviously better exists), we do **not** rebuild the dashboard from scratch.

---

## 11. Risks, non-goals, decision log

### 11.1 Risks

| Risk | Mitigation |
|---|---|
| **Codex single-use refresh tokens.** Containers restarting can chew tokens. | `on-failure:5`, never `always`. `safe-restart.sh` discipline. Token backups in `credentials/token-backups/`. Vault stores codex creds; entrypoint copies into `/root/.codex/` at container start. |
| **claude-agent-acp / codex-acp upstream churn.** Both projects ship rapidly. | Pin specific versions in Dockerfiles. Update on a scheduled cadence (monthly), test on Resi first. |
| **Premature memory complexity.** Embeddings/vector stack can add complexity without clear value at current scale. | Start with FTS5-only and instrument misses. Add embeddings/vector later only if retrieval quality data shows a real need. |
| **Smart router classification cost.** Every ambiguous message becomes a small LLM call. | Cache by hash of the message text. Default chats stay static; smart routing is opt-in per chat. |
| **MCP server uptime.** If `task-manager-mcp` is down, Resi can't update stories. | Each MCP server is its own systemd-user unit (not Docker yet) with health-check restart. Resi can run with degraded tool set rather than refusing to start. |
| **Vault master key on disk.** `~/.openclaw/credentials/.vault-pass` is mode 600 plaintext. | This is the praktor model and is the standard trade-off. If we want more, integrate with the system keyring later. Out of scope for v1. |
| **Container sprawl.** Many concurrent worktrees + containers on one host. | Idle container reaper at 10 min (praktor uses this). Max 3 parallel coding containers per agent. Disk-pressure alert. |

### 11.2 Non-goals (explicitly choosing not to do)

- **No web UI for users.** Dashboard is operator-only, loopback.
- **No Kubernetes.** Docker Compose on this host is enough.
- **No vector layer in v1/v2.** FTS5-only memory retrieval by default.
- **No agent message bus.** Cross-agent goes through ACP prompt re-injection (Phase 3) or the relay hook (today).
- **No rebuild of OpenClaw.** It's the gateway; we add to it.
- **No iMessage / Slack / Discord ingress in v1.** Telegram only.
- **No "agent that writes new agents at runtime."** Adding an agent is a deliberate human edit.
- **No live trading by default.** `druck-finance-mcp` is paper-only at server level; live mode is a server-side flag plus per-call permission button.

### 11.3 Decision log — open questions for Aron

These need an answer before Phase 1 starts. None are reversible-free.

1. **Resi's coding container model:** `claude-agent-acp` (Claude Agent SDK behind ACP) vs. straight `claude` CLI (ductor-style subprocess, no ACP)? ACP gives the permission UI and edit-review; subprocess is simpler and matches what 377★ ductor does. **Recommendation:** claude-agent-acp for Resi specifically (iOS+web is exactly the use case ACP was designed for); leave the door open for ductor-style for the others.
2. **Vault master-key location:** file (`~/.openclaw/credentials/.vault-pass`, mode 600) vs. system keyring (libsecret on Linux) vs. prompt-at-startup? **Recommendation:** file for v1, document the trade-off, revisit if/when this host stops being trusted.
3. **Smart routing scope:** every chat by default, or only the shared "RSL Ops" group? **Recommendation:** only the shared group at first; promote to default only after we've watched it for a month.
4. **Druck coding container:** spawn `druck-coder` (codex-acp) for `phase2/` edits, or keep using native OpenClaw and just edit the files in-process like today? Today's setup actually works for him — `phase2/` is in his workspace, smoke tests pass, FMP adapter live. **Recommendation:** keep native for now, revisit only if you actually want to add concurrent Druck tasks.
5. **Customer-support agent:** in scope for this design (just template usage) or out of scope until we have customers asking for it? **Recommendation:** out of scope; the design supports it but we don't build for it.
6. **Retire `telegram-agent-relay`?** v1 said yes; v2 says keep it. The hook works, it's small, replacing it with ACP cross-agent prompt requires both ends to be containerized. **Recommendation:** keep, document as "to be retired once all sender + receiver pairs are containerized."
7. **Dwight orchestrator activation:** turn on `task.assign` dispatch in Phase 2 (early), or wait until all coding agents are containerized in Phase 3? **Recommendation:** Phase 2, explicit hand-offs only; auto-classification stays off until Phase 3.
8. **Vault master-key paper backup:** QR-in-safe (§9.7), or Shamir-split with N=3 / K=2 across multiple physical locations? **Recommendation:** single QR for now; Shamir is correct but overkill at this scale.
9. **Default model for `jerry-coder`:** stay on `openai-codex/gpt-5.4` (matches native Jerry, but means single-use codex refresh tokens inside a container — gnarly), or switch the coding-mode Jerry to `claude-agent-acp` (different model, stabler token story)? **Recommendation:** `claude-agent-acp` for the coding container only; native Jerry stays on codex. Document as "Jerry uses two brains, on purpose."
10. **Per-agent monthly budget defaults** in `~/.openclaw/budgets.json` need actual numbers. **Recommendation:** after Phase 1 lands enough audit data, set soft limits at 1.5× the trailing-month median spend per agent.
11. **`/override-budget` authorization:** anyone with the bot, or gated to Aron's Telegram user-id? **Recommendation:** gated. Add a `secure_user_ids` list in `openclaw.json` and reuse it for any other destructive slash command.

---

## 12. The honest summary

We already have most of what we need. OpenClaw is doing the gateway, channel, routing, hook, cron, dashboard, persona, secret-management, hot-reload work — and doing it well enough that two of the three projects in the audit (ductor, praktor) are essentially reinventions of it. What we **don't** have is process isolation for agents that write code, structured per-agent memory, encrypted secrets, smart routing, and the UX vocabulary (compact-mode + permission buttons + stage emojis) that makes a chat-fronted agent feel like a tool and not a chat partner.

The plan is to add exactly those four things, in three phases, starting with Resi in a container. Phase 1 proves coding isolation. Phase 2 lands memory + UX + orchestration with FTS5-only memory. Phase 3 adds vault + smart routing. Keep implementation scope intentionally narrow so this stays maintainable for a single-user deployment.

When somebody six months from now asks "where does this new capability live?", the answer is one of:

- Coding work that touches source? → MCP tool inside a coding container.
- Tool surface multiple agents need? → MCP server (probably upstream, or a small custom shim).
- Agent persona / behavior? → markdown file under `workspaces/<agent>/`.
- Secret? → vault.
- Memory? → `facts` table.
- New ingress channel? → not in scope; redesign instead of adding.

— Jerry
