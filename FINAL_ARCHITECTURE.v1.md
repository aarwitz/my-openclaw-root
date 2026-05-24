# RSL / Lidi Solutions — Final Agent Architecture

> Historical archive only. Superseded by `FINAL_ARCHITECTURE.md` and not authoritative for current role/gender definitions or operating policy.

**Author:** Jerry (Copilot, Claude Opus 4.7) for Aron
**Date:** 2026-05-23
**Status:** Proposal — design only, no changes made yet
**Scope:** Final target architecture for the RSL agent fleet (Jerry, Resi, Druck, Dwight, and any future specialists), the protocols between them, the tools they use, and the operational discipline that keeps it from drifting back into a tangle of bespoke bridges.

---

## 0. Reading this document

This document is opinionated. It is meant to be the **one** place we look when somebody asks "where should this new capability live?". If the answer is not on the diagram, the answer is "redesign, do not add a new path."

The document is structured as:

1. **Principles** — non-negotiable invariants. Everything else derives from these.
2. **Current state** — honest snapshot of `~/.openclaw` as it exists today.
3. **Target architecture** — the layers, the protocols, the runtime, the data.
4. **Component catalog** — every box on the diagram, what owns it, what it ships.
5. **Per-agent footprint** — what each of Jerry / Resi / Druck / Dwight gets.
6. **Feedback & observability** — how we get transparency without noise.
7. **Migration plan** — phased path from today to target. Reversible at every step.
8. **Risks & non-goals** — what we are explicitly choosing not to do.
9. **Decision log** — open questions Aron still has to resolve.

---

## 1. Principles (the invariants)

These rules are how we keep the system simple as it grows. They are deliberately strict. **If you find yourself adding a path that violates one, redesign instead of adding it.**

1. **Telegram is the only human ingress.** No web UI, no REST endpoint, no email-to-agent shortcut. Hooks (Gmail, cron, webhooks) may *create work* but only by posting an internal message into a Telegram-shaped session; humans only ever talk to the system through Telegram.
2. **ACP (Agent Client Protocol) is the only protocol between the chat bridge and an agent.** Not raw stdin/stdout, not HTTP shims, not OpenClaw-internal RPC. The bridge speaks ACP and the agent speaks ACP. Period.
3. **MCP (Model Context Protocol) is the only protocol between an agent and a tool.** No bespoke `subprocess.Popen("gh ...")` from inside an agent, no in-process Python adapter for Schwab, no shell skill that hides credentials. If an agent needs a capability, it is exposed as MCP tools, resources, or prompts.
4. **Streamable HTTP MCP for shared services. Stdio MCP for ephemeral, per-agent tools.** Shared servers (task-manager, qa, memory, github, search) run as long-lived HTTP services that every agent connects to. Per-agent tools (a workspace-scoped filesystem, a per-agent browser) are launched as stdio subprocesses by the agent container.
5. **One container per agent, one git worktree per task.** Each agent (Jerry, Resi, Druck, Dwight) runs in its own Docker container with its own filesystem, its own credentials mount, its own browser, its own MCP stdio servers. Each *task* that touches a repo runs in a dedicated git worktree, so concurrent tasks never stomp on each other's branches.
6. **Memory is layered, durable, and queryable.** Three tiers: (a) per-session ephemeral context, (b) per-agent long-term semantic store (SQLite + embeddings), (c) cross-agent knowledge graph (shared MCP server). Markdown daily logs continue to exist as the human-readable surface.
7. **Off-the-shelf > custom.** If Zed, Anthropic, OpenAI, Google, or the MCP community ships it, we use it. Custom code is a liability that has to be maintained against an upstream that is moving faster than we are.
8. **Custom code is small, obvious, and lives behind a stable protocol.** Anything we write ourselves should be ≤ a few hundred lines, with a one-paragraph explanation, behind an ACP or MCP surface so we can replace it without touching anything else.
9. **Hot reload over restart. Backup before destruction.** Every change is reversible. We keep the [openclaw-gateway-rules](memory) discipline: never `systemctl restart` what hot-reload can handle, always snapshot tokens before structural changes.
10. **Transparency without overwhelm.** Every action produces a structured event (started / progress / completed / failed). The Telegram surface shows only the headline; the dashboard and Task Manager show the detail. Defaults are "tell me less, log everything."

---

## 2. Current state (honest snapshot)

What we have today (`~/.openclaw/` on this host):

- **Gateway:** OpenClaw 2026.4.15, single process on loopback:18789, file-backed gateway token, hot-reload "hybrid" mode. Hardened per `memories/repo/hardening-2026-04-16.md`.
- **Agents:** 4 personas (`main`/Jerry, `druck`, `dwight`, `resi`) sharing one host, four Telegram bot accounts, routed via `bindings[]`. Each has its own workspace under `workspaces/<name>/` with `IDENTITY.md`, `SOUL.md`, `AGENTS.md`, `MEMORY.md`, etc.
- **Model:** mostly `openai-codex/gpt-5.4`. Codex has single-use refresh tokens — a known fragility.
- **Sandbox:** **disabled** (`agents.defaults.sandbox.mode: "off"`). Exec runs directly on host. No container isolation.
- **MCP servers configured:** **zero** (`openclaw mcp list` returns empty). All tool access today goes through OpenClaw "skills", which are in-process bash/python adapters living in `workspace/skills/` and bundled with OpenClaw.
- **ACP:** OpenClaw ships an ACP bridge (`openclaw acp`), but it is currently used only as an outbound surface for connecting external editors *into* an OpenClaw session. We do **not** use ACP as the bot↔agent protocol; the bot and agent are the same OpenClaw process.
- **Skills:** 31/71 ready. Mostly bundled, a few workspace-owned (`alpaca`, `browser_app_QA`, `cloudflare`, `druck-research`, `ewag-*`, `financialmodeling-prep-api`, `finnhub`, `gh-issues`, `github`, `github-ssh`, `gog`, `healthcheck`, `massive`, `newsapi-ai`, `node-connect`, `openclaw-ops`, `resilife-product`, `schwab`, `session-logs`, `skill-creator`, `task-manager`, `task-manager-maintainer`, `taskflow`, `tmux`, `video-frames`, `weather`).
- **Memory:** Per-agent SQLite (`memory/{main,druck,dwight,resi}.sqlite`) plus markdown (`workspaces/<agent>/memory/YYYY-MM-DD.md`, `MEMORY.md`). Druck additionally has a domain-specific `phase2_cache/`.
- **Hooks:** `credential-preflight`, `newsapi-credentials`, `group-topic-memory`, `telegram-agent-relay` (the relay that lets bots address `@peer` to coordinate without going through Telegram's actual bot-to-bot path).
- **Repos:** A growing set of long-running RSL repos (Task Manager, EWAG/ResiLife iOS, Druck phase2, etc.). Today they are checked out *flat* under workspace dirs; concurrent edits collide.

**What works well** and we should keep:

- Telegram channel + multi-account routing + group/topic policy.
- Hot reload with safe-restart.sh; file-backed secrets; credential-preflight gating.
- Per-agent persona files (`IDENTITY.md`, `SOUL.md`, `AGENTS.md`, `USER.md`, `MEMORY.md`).
- Daily markdown memory logs.
- Cron + webhooks + hooks as *event triggers* (not as protocols).
- `telegram-agent-relay` semantics (`@peer` = reply, `FYI @peer` = listen-only).

**What is missing or weak** today (and motivates this redesign):

- No process isolation between agents — one crash can take all four bots offline.
- No tool isolation — every "skill" runs in the gateway's user with full host filesystem access.
- No concurrent-task safety on shared repos.
- Tool/skill API is OpenClaw-specific; nothing ports to Zed, Cursor, Claude Desktop, Copilot, etc.
- No standard, ecosystem-supported way for a third agent (e.g., a future iOS-build node, a customer-service worker, a trading agent) to plug in.
- No protocol-level permission UI — today permission is "the agent decides", with audit logs after the fact.
- Druck's trading code is co-mingled with its agent workspace; the boundary between "production code" and "scratch agent context" is fuzzy.

---

## 3. Target architecture

### 3.1 The three-layer picture

```
┌────────────────────────────────────────────────────────────────────────┐
│                          LAYER 1 — INGRESS                              │
│  Telegram (4 bot accounts: jerry, resi, druck, dwight)                  │
│       │   one direction only — humans + relay messages                   │
└───────┼─────────────────────────────────────────────────────────────────┘
        │
┌───────▼─────────────────────────────────────────────────────────────────┐
│                  LAYER 2 — BRIDGE  (telegram-acp-bot)                   │
│  Routes per-account Telegram traffic → per-agent ACP session.            │
│  Renders ACP UpdateNotifications (text, diffs, tool calls, permissions) │
│  back to Telegram as messages, inline buttons, edits, and reactions.    │
│  Stateless beyond session-id ↔ chat-id mapping (SQLite).                 │
└───────┬───────────┬───────────┬───────────┬─────────────────────────────┘
        │ ACP/stdio │ ACP/stdio │ ACP/stdio │ ACP/stdio
        ▼           ▼           ▼           ▼
┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐
│  jerry   │ │   resi   │ │  druck   │ │  dwight  │     ← LAYER 3 — AGENTS
│ (codex)  │ │ (claude) │ │ (codex)  │ │ (claude) │       one Docker container
│  ACP     │ │   ACP    │ │   ACP    │ │   ACP    │       each, one git worktree
│  agent   │ │  agent   │ │  agent   │ │  agent   │       per active task
└────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘
     │ MCP (stdio + streamable-HTTP)        │
     ▼                                       ▼
┌────────────────────────────────────────────────────────────────────────┐
│                       LAYER 4 — MCP TOOL FABRIC                         │
│                                                                         │
│  Shared servers (streamable-HTTP, multi-tenant):                        │
│     task-manager-mcp     github-mcp           memory-graph-mcp          │
│     qa-mcp               drive/gmail-mcp      search-mcp                │
│     finance-mcp                                                         │
│                                                                         │
│  Per-agent stdio servers (launched by agent container):                 │
│     fs-mcp (scoped to worktree)   browser-mcp (chromium, sandbox'd)     │
│     git-mcp (worktree-scoped)     bash-mcp (approval-gated)             │
└────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Protocol surfaces — the bright lines

| Boundary | Protocol | Transport | Why |
|---|---|---|---|
| Human ↔ system | Telegram Bot API | HTTPS long-poll | Only ingress. Already there. |
| Bridge ↔ agent | **ACP** | stdio (local) | Standard, ecosystem-supported, streaming, permission UI baked in, swappable agent backends. |
| Agent ↔ shared tool | **MCP** | **Streamable-HTTP** | Multi-tenant, sessionful, resumable, one process serves all agents, supports SSE for progress. |
| Agent ↔ per-agent tool | **MCP** | **stdio** | Cheap to spawn, scoped to one process lifetime, no network surface. Spec recommends stdio whenever possible. |
| Bridge ↔ agent (cross-agent relay) | ACP `prompt` re-injection | n/a | Replaces today's bespoke `telegram-agent-relay` hook with an ACP-native call from one agent's session into another's. |
| Cron / Gmail / webhook → agent | ACP `prompt` | stdio | Same path as Telegram, just a different *caller*. No second code path. |

### 3.3 Runtime — Docker per agent, worktrees per task

**Container model.** Each agent gets a dedicated Docker container, built from a thin Dockerfile (~15 lines) on top of a shared base image. The container holds:

- The ACP agent binary (`claude-agent-acp`, `codex-acp`, or `gemini --acp`).
- A workspace mount: `~/.openclaw/workspaces/<agent>/` mounted read-write.
- A repos mount: `~/.openclaw/repos/<agent>/` mounted read-write — this is where worktrees live.
- A credentials mount: only the *specific* secret files that agent needs, mounted read-only.
- A trusted network to reach shared MCP servers; **no** access to the host network or to other agents' containers.
- A chromium binary for browser-mcp, run with `--no-sandbox` *inside* the container (the container itself is the sandbox).

OpenClaw's existing `sandbox` subsystem already supports Docker-backed isolation; we are turning it on and making it the default rather than the exception.

**Worktree model.** Each repo we work on (e.g., `resilife-ios`, `task-manager`, `druck-phase2`) has one canonical bare clone under `~/.openclaw/repos/<agent>/<repo>.git`. When an agent starts a task, the agent container runs:

```bash
git -C ~/.openclaw/repos/<agent>/<repo>.git worktree add \
    ~/.openclaw/repos/<agent>/worktrees/<task-id> <branch-name>
```

The ACP session's working directory is set to that worktree. When the task ends (PR merged, abandoned, or timed out) the worktree is pruned. This means:

- Two Resi tasks can edit two different branches of `resilife-ios` simultaneously without `git stash` collisions.
- A failed task leaves a worktree on disk for forensic inspection.
- `git-mcp` only ever sees one branch at a time per session.

**Lifecycle.** Containers are long-lived (one per agent, restart on failure). Worktrees are short-lived (one per task). ACP sessions are short-lived (one per Telegram conversation thread). MCP shared servers are long-lived (one per service, restart on failure, behind a thin healthcheck).

### 3.4 Memory architecture

Three tiers, each with a clear owner:

**Tier 1 — Session context (ephemeral, per-conversation).** Lives inside the ACP agent's process. Cleared on `session/reset`. This is what the model actually sees in its context window. Bridge stores a session-id ↔ chat-id mapping but never reads the contents.

**Tier 2 — Agent long-term memory (durable, per-agent).** SQLite + embeddings, exposed via a stdio `memory-mcp` subprocess inside the agent's container. Replaces today's per-agent `memory/<name>.sqlite` with a properly-versioned schema:

```
memory.sqlite
├── facts(id, text, embedding, source_session, ts, agent)
├── tasks(id, status, owner, repo, worktree, started, ended)
├── decisions(id, ts, context, choice, rationale)
└── relations(subject_id, predicate, object_id)
```

Markdown daily logs (`memory/YYYY-MM-DD.md`) continue to be written as the human-readable surface and are imported into the SQLite store nightly.

**Tier 3 — Shared knowledge graph (durable, cross-agent).** A single `memory-graph-mcp` HTTP server, backed by SQLite (or Chromium SQLite for vector ops if we want zero deps), holds facts that all agents need: who Aron is, who Taylor is, what RSL/Lidi/EWAG/ResiLife mean, current sprint goals, recent decisions, escalation paths. Read by every agent at session start, write-gated to one tool call per session to prevent noise.

This is the structure called out in the prompt — "memory graphs" — and it gives us the cross-agent context that today is duplicated (and drifts) across four `MEMORY.md` files.

### 3.5 The "no other paths" enforcement

The principle says: no other paths exist. To make that enforceable, not just aspirational:

- The agent container's outbound firewall is allowlisted to (a) the MCP fabric and (b) any external APIs explicitly mounted via an MCP tool.
- There is **no** OpenClaw-style "skill" inside the container. Skills become MCP servers or they do not exist.
- The bridge container has only Telegram + the four agent stdio pipes open. No HTTP listener. No webhook ingress.
- A weekly `architecture-drift-check` cron runs `openclaw doctor` + a custom lint that scans for any direct `subprocess.run`, `requests.get`, `http.server` outside the four blessed containers. Any hit posts to Jerry's Telegram with a one-line summary.

---

## 4. Component catalog

### 4.1 Off-the-shelf (use as-is, do not fork)

| Component | What it is | Where it runs | Source |
|---|---|---|---|
| `telegram-acp-bot` | Telegram ↔ ACP bridge | Container `bridge` | [mgaitan/telegram-acp-bot](https://github.com/mgaitan/telegram-acp-bot) (Python, MIT-style, ~11★ today — small but exactly the right shape; we may pin a vendored fork) |
| `claude-agent-acp` | Wraps Claude Agent SDK as ACP | Container `resi`, `dwight` | [agentclientprotocol/claude-agent-acp](https://github.com/agentclientprotocol/claude-agent-acp) (Apache-2.0, official) |
| `codex-acp` | Wraps OpenAI Codex CLI as ACP | Container `jerry`, `druck` | [zed-industries/codex-acp](https://github.com/zed-industries/codex-acp) |
| `gemini --acp` | Native ACP in Gemini CLI | (optional 5th container) | [google-gemini/gemini-cli](https://github.com/google-gemini/gemini-cli) |
| `@modelcontextprotocol/server-github` | Official GitHub MCP server | Shared HTTP, container `mcp-github` | [modelcontextprotocol](https://github.com/modelcontextprotocol) |
| `mcp` Python SDK (FastMCP) | Framework for our custom MCP servers | Inside each `mcp-*` container | modelcontextprotocol.io |
| Docker / Compose | Container runtime + orchestration | Host | — |
| `tailscale` | Already used for EWAG node | Host | — |
| `chromium` | Browser inside agent containers | Per-container | — |

### 4.2 Custom (we own and maintain)

Total budget: **~500 lines of Python, ~100 lines of bash, ~150 lines of YAML/Dockerfile.** Anything beyond that is a smell.

| File | Purpose | Approx size |
|---|---|---|
| `compose/docker-compose.yml` | One service per container; declares the shared network, mounts, and restart policy. | ~120 lines |
| `compose/Dockerfile.base` | Shared base image: python, node, git, chromium, tini. | ~25 lines |
| `compose/Dockerfile.bridge` | Adds `telegram-acp-bot` on top of base. | ~10 lines |
| `compose/Dockerfile.agent-codex` | Adds `codex-acp` + agent entrypoint. | ~15 lines |
| `compose/Dockerfile.agent-claude` | Adds `claude-agent-acp` + agent entrypoint. | ~15 lines |
| `compose/Dockerfile.mcp-base` | Python + FastMCP + uvicorn. | ~10 lines |
| `agents/<name>/entrypoint.sh` | Worktree-aware ACP wrapper. Identical across agents. | ~40 lines, shared |
| `agents/<name>/mcp.json` | Declares which shared HTTP MCP servers to attach + which stdio servers to launch. | ~20 lines per agent |
| `agents/<name>/AGENTS.md` + `SOUL.md` + etc. | Persona files. Already exist; keep as-is. | unchanged |
| `mcp-servers/task-manager/server.py` | MCP shim over the RSL Task Manager API (Dwight's domain). | ~120 lines |
| `mcp-servers/qa/server.py` | MCP shim for the EWAG/ResiLife QA loop (screenshots, video capture, ios-build-node). | ~80 lines |
| `mcp-servers/memory-graph/server.py` | Cross-agent knowledge graph; thin wrapper over SQLite. | ~100 lines |
| `mcp-servers/finance/server.py` | Wraps Schwab + Alpaca + FMP + Finnhub + Massive. Druck's tools. | ~150 lines |
| `mcp-servers/drive-gmail/server.py` | Google Workspace ops, scoped per agent. | ~80 lines |
| `scripts/worktree-add.sh` | Idempotent `git worktree add` with cleanup of stale worktrees. | ~30 lines |
| `scripts/architecture-drift-check.sh` | Weekly lint enforcing the "no other paths" rule. | ~40 lines |

That is the entire custom surface. If it grows past 1,000 lines, we have drifted from the design.

### 4.3 OpenClaw — what role does it still play?

OpenClaw today is doing the work of *all four* layers above in a single process. After this migration, OpenClaw shrinks to:

**Kept (OpenClaw stays the manager):**
- Multi-account Telegram channel config (we keep `channels.telegram` for the four bot accounts and let `telegram-acp-bot` consume them, OR retire OpenClaw's Telegram and let the bridge own it directly — see decision log §9).
- File-backed secrets, `safe-restart.sh`, credential-preflight, token rotation discipline.
- Cron + webhook ingress (these now `POST` an ACP `prompt` to the bridge instead of running a skill in-process).
- `openclaw doctor`, health monitoring, log rotation.
- The agent personas (`workspaces/<agent>/*.md`) — these stay on disk and are loaded by the agent containers at session start.

**Retired:**
- OpenClaw "skills" as the primary tool surface — replaced by MCP servers. The skills directory becomes a *reference library* (read by humans), not a runtime.
- OpenClaw's in-process agent loop — replaced by the per-agent ACP container.
- `telegram-agent-relay` hook — replaced by an ACP-native cross-agent prompt call.
- `group-topic-memory` hook — replaced by the memory-graph MCP server's session-init read.

The net effect: OpenClaw becomes the **control plane** (config, secrets, supervisor, monitor) and we strip out the **data plane** (the ad-hoc tool calls and per-agent skill stacks).

---

## 5. Per-agent footprint

### 5.1 Jerry (`@a_rslbot` — main / default)

- **Container:** `agent-jerry`, image `agent-codex`.
- **Model:** `openai-codex/gpt-5.4` (keep the codex token-fragility discipline).
- **Workspace mount:** `~/.openclaw/workspace/` (legacy "main" workspace).
- **Repos:** `task-manager` (read-only outside Dwight), `openclaw-config` (read-write — Jerry owns platform), `ewag-infra` (read).
- **Shared MCP attachments:** task-manager, github, memory-graph, drive-gmail, search.
- **Stdio MCP attachments:** fs-mcp, git-mcp, bash-mcp, browser-mcp.
- **Special role:** default router. If a Telegram message comes in that the bridge can't classify, it lands on Jerry. Jerry can re-route via cross-agent ACP prompt.

### 5.2 Resi (`@resi_rslbot`)

- **Container:** `agent-resi`, image `agent-claude`.
- **Model:** Claude (via Claude Agent SDK).
- **Workspace mount:** `~/.openclaw/workspaces/resi/`.
- **Repos:** `resilife-ios`, `ewag-website` (read-write).
- **Shared MCP attachments:** task-manager, github (account-routed to `EWAG-dev`), qa, drive-gmail.
- **Stdio MCP attachments:** fs-mcp, git-mcp, bash-mcp (with `ios-build-node` SSH proxy), browser-mcp.
- **Special:** the QA MCP server owns the iOS build/screenshot loop (today's `ewag-capture.sh`, `ewag-test.sh`); the agent calls `qa.capture(view="home")` instead of invoking a skill.

### 5.3 Druck (`@druck_rslbot`)

- **Container:** `agent-druck`, image `agent-codex`.
- **Model:** `openai-codex/gpt-5.4`.
- **Workspace mount:** `~/.openclaw/workspaces/druck/` (keep `phase2/` co-located).
- **Repos:** `druck-phase2` (read-write).
- **Shared MCP attachments:** finance, memory-graph, search, news (a slim wrapper over today's `newsapi-ai`).
- **Stdio MCP attachments:** fs-mcp, git-mcp, bash-mcp (no browser — Druck doesn't need one).
- **Special:** Druck never gets `task-manager.write` or `github.write`. Trading decisions go through `finance.propose_trade` which writes to `alpaca_paper_ledger.json` and posts a Telegram message; live trading is permission-gated through ACP's permission UI.

### 5.4 Dwight (`@dwight_rslbot`)

- **Container:** `agent-dwight`, image `agent-claude`.
- **Model:** Claude.
- **Workspace mount:** `~/.openclaw/workspaces/dwight/`.
- **Repos:** `task-manager` (read-write, sole owner), `task-manager-mcp` (read-write).
- **Shared MCP attachments:** task-manager (with admin scope), github, memory-graph, qa (read).
- **Stdio MCP attachments:** fs-mcp, git-mcp, bash-mcp.
- **Special:** the `task-manager-maintainer` capability stays scoped to Dwight via MCP server-side ACLs, not just via skill loading rules.

### 5.5 A future fifth agent (template)

Adding a "Marketing" or "Customer Success" agent is now a copy-paste:

1. `compose/docker-compose.yml` — add a service.
2. `agents/marketing/{AGENTS.md, SOUL.md, IDENTITY.md, mcp.json}` — write persona + tool attachments.
3. `channels.telegram.accounts.marketing` — provision a bot token in OpenClaw's channel config.
4. Bridge `accounts.toml` — map the bot token to the agent container.
5. Done. No new code.

---

## 6. Feedback and observability — transparency without noise

The hardest part of multi-agent systems is *knowing what the hell they are doing* without drowning in their internal monologue. The design:

### 6.1 Four event severities, four surfaces

| Severity | Example | Surface |
|---|---|---|
| `chat` | The actual reply to the user | Telegram message (the only thing humans see by default) |
| `progress` | "Started PR #142, running tests" | Telegram *edit* of the same message + reaction emoji (🛠️ → ✅) — no new notification |
| `audit` | Every tool call, every file write, every permission grant/deny | Structured JSONL in `~/.openclaw/logs/audit/YYYY-MM-DD.jsonl`; dashboard view |
| `alert` | Auth drift, container crash loop, MCP server down, drift-check fail | Telegram DM to Aron with a one-line summary + dashboard link |

Defaults are conservative: humans see `chat`, the original message gets *edited* with progress, and only true alerts produce a new notification.

### 6.2 The Task Manager as the persistent feedback substrate

Every non-trivial task ends with a Task Manager comment (Dwight's domain). The agent posts a single dense update: outcome, evidence link (screenshot, PR, log file), next action, owner. This is the audit log humans actually read.

### 6.3 Per-agent heartbeat

Keep today's heartbeat config but reduce target to `none` by default and rely on the dashboard. The heartbeat becomes diagnostic, not a user-facing thing.

### 6.4 Dashboard

A single static HTML page (already half-built as `~/.openclaw/canvas/index.html`) showing:

- Container health (all 4 agents + bridge + each MCP server).
- Active ACP sessions and their worktrees.
- Last 50 audit events.
- Token expiry countdowns.
- Drift-check status.

No login — bound to loopback. This is operator-grade transparency; the chat surface stays clean.

---

## 7. Migration plan

Each phase is **independently shippable and reversible**. We do not break the current Telegram experience at any point.

### Phase 0 — Snapshot and prep (1 sitting)

- Full `openclaw backup` of state.
- Pin current OpenClaw version; record the exact commit.
- Document the current `bindings[]` and `channels.telegram.accounts` so we can rebuild.
- Bring up a `~/.openclaw-dev` profile (already supported via `--dev`) to run the new stack side-by-side without touching production.

### Phase 1 — Stand up the MCP fabric in dev (1–2 sittings)

- Write the four custom MCP servers (`task-manager`, `qa`, `memory-graph`, `finance`) as standalone HTTP services in the dev profile. Each is one file.
- Bring up `mcp-github` from the official package.
- Verify with `mcp-inspector` (the standard tool) that each server speaks the protocol.
- **Reversibility:** these are new services on new ports; production is untouched.

### Phase 2 — Stand up one agent in a container (1 sitting)

- Pick **Druck** first (lowest blast radius — research, no shipping). Build `Dockerfile.agent-codex`, write `compose/docker-compose.yml` with just `agent-druck` + the MCP fabric.
- Run `codex-acp` inside it. Use `openclaw acp client` to drive it manually from the terminal.
- Verify tool calls land on the MCP servers, not on OpenClaw skills.
- **Reversibility:** Druck's Telegram bot still talks to the old OpenClaw process. The container is shadow.

### Phase 3 — Wire the bridge for Druck only (1 sitting)

- Bring up `telegram-acp-bot` in a container, configured with **only the Druck bot token**, pointing at `agent-druck`.
- In OpenClaw, disable the Druck binding (so the old path is dormant).
- Live test for 24h. Permission inline buttons, streaming, restart-on-crash, all of it.
- **Reversibility:** flip the OpenClaw binding back on, stop the bridge container.

### Phase 4 — Migrate the other three agents (3 sittings, one per agent)

- Resi next (claude-agent-acp + the qa MCP server gets its first real workout).
- Dwight (claude-agent-acp + task-manager MCP).
- Jerry last (codex-acp + everything wired up; the default fallback).
- After each agent migrates, retire the corresponding OpenClaw skill stack.

### Phase 5 — Retire the old paths (1 sitting)

- Delete the `telegram-agent-relay` hook (replaced by ACP cross-agent prompts).
- Delete the `group-topic-memory` hook (replaced by memory-graph reads).
- Move OpenClaw skill files into `~/.openclaw/skills.archive/` for reference; remove from `skills.entries`.
- Turn off OpenClaw's in-process agent loop.

### Phase 6 — Harden (ongoing)

- Add the weekly drift-check cron.
- Add per-agent firewall egress allowlist.
- Add `architecture-drift-check.sh` as a `git commit` pre-receive hook on the openclaw-config repo.
- Document everything in `~/.openclaw/repos/openclaw-config/README.md`.

**Total estimated complexity** (not time): ~5 small Docker images, ~5 MCP server files, ~5 days of focused work with rollback at every step. Most of the heavy lifting is OpenClaw's hardening discipline that already exists.

---

## 8. Risks and explicit non-goals

### 8.1 Risks we are taking on

| Risk | Mitigation |
|---|---|
| **Codex single-use refresh tokens still fragile.** Containers add restart-on-crash, which can chew tokens. | Per existing `openclaw-gateway-rules`: never auto-restart, use `safe-restart.sh`, token backups in `credentials/token-backups/`. Container restart policy is `on-failure:5`, not `always`. |
| **`telegram-acp-bot` is small (11★) and may diverge from upstream.** | Vendor a fork into our own repo, pin a commit, treat it as custom code under the 500-line budget. |
| **MCP servers become the new single point of failure.** | Each MCP server is its own container with healthchecks. The bridge degrades gracefully — if `qa-mcp` is down, Resi still works for non-QA tasks. |
| **Worktree sprawl on disk.** | `scripts/worktree-add.sh` prunes worktrees older than 30 days or with no recent git activity. Weekly Dwight task to review stale worktrees. |
| **Permission fatigue from ACP inline buttons.** | Pre-approve a workspace per session (an ACP capability) for trusted tool sets; only ask for explicit permission on destructive ops (file delete, force-push, real trades, send-message-to-stranger). |
| **Cross-agent context loss when ACP sessions are short-lived.** | Memory tiers 2 and 3 hold the durable context. Session start always reads from memory-graph. |

### 8.2 Non-goals (explicitly *not* doing)

- **No web UI for users.** Telegram is enough; building a web UI doubles the surface area.
- **No Kubernetes.** Compose on a single host is sufficient; introducing k8s would be the largest custom-code blowup in the design.
- **No vector database service (Pinecone, Weaviate, etc.).** SQLite + sqlite-vec is enough at our scale and zero ops.
- **No agent-to-agent message bus (Kafka, NATS).** Cross-agent communication goes through ACP prompts; if we feel the need for a bus, the design is wrong.
- **No bespoke per-skill auth flow.** All credentials live in OpenClaw secret files, mounted into the relevant container, accessed by the relevant MCP server. One pattern.
- **No "agent that writes new agents at runtime."** Adding an agent is a deliberate human edit to compose.yml. We can revisit when we have ten agents and feel real friction.
- **No iMessage, Slack, Discord ingress in v1.** Telegram only. If we add another channel later, it gets its own bridge container speaking ACP to the same agents. The agents do not change.

---

## 9. Decision log — open questions for Aron

These are the calls you need to make before we start Phase 1. None of them are reversible-free, but none are catastrophic.

1. **Telegram channel ownership.** Two options:
   - (a) OpenClaw keeps owning the Telegram channel config (today's `channels.telegram.accounts.*`) and the bridge container subscribes to a local OpenClaw message-bus event for each agent's traffic. *Pro:* keeps multi-account, group/topic, allowFrom policy in one place. *Con:* the bridge is no longer pure off-the-shelf — needs a custom adapter to OpenClaw's event stream.
   - (b) `telegram-acp-bot` owns Telegram directly with all four bot tokens, OpenClaw retires its Telegram code. *Pro:* purer architecture, one fewer hop. *Con:* we lose OpenClaw's group/topic policy and have to re-implement it in the bridge.
   - **Recommendation:** (a) for v1, revisit later. We keep what's working.
2. **Single host vs. split.** Run everything on this Linux host vs. put MCP fabric on a separate machine. **Recommendation:** single host, Tailscale-reachable, until we hit a real CPU/memory ceiling.
3. **Claude vs. Codex per agent.** Today everything is Codex. The plan above puts Claude on Resi and Dwight (long context, edit-heavy) and Codex on Jerry and Druck (cheaper, faster). **Confirm or override.**
4. **Memory-graph storage.** SQLite + sqlite-vec (zero ops) vs. Chromium's hosted SQLite (the prompt mentioned this — likely meant for embedded vector ops). **Recommendation:** SQLite + sqlite-vec; "chromium sqlite" feels like it's solving a problem we don't have.
5. **Trading agent autonomy.** Druck currently has paper-only via Alpaca. The `finance-mcp` server can be wired to live Schwab, but the ACP permission UI means every live trade is gated. **Confirm:** do you want live trading reachable at all in v1, or paper-only?
6. **Customer-facing agent.** Mentioned in the brief but not yet specified. Do we add a `support` agent in Phase 4, or defer to v2?

---

## 10. TL;DR

We are taking today's working-but-tangled OpenClaw process and pulling it apart along three protocol seams: **Telegram for humans, ACP for bot↔agent, MCP for agent↔tools**. Each of our four agents becomes a Docker container; each repo task gets its own git worktree; tools become small MCP servers we (and the entire ACP/MCP ecosystem) can reuse. Our custom code shrinks to ~500 lines that we actually want to maintain. OpenClaw becomes the control plane that watches over the whole thing. Migration is phased, reversible, and never breaks the current Telegram experience.

If, six months from now, somebody wants to plug Cursor or Zed or Claude Desktop into Druck's research flow, the answer is "point your ACP client at this container." If a new MCP capability lands upstream, every agent inherits it. If we want a marketing agent, it's a copy-paste of an existing container. If somebody proposes adding a new ingress, a new protocol, or a new tool-call path — **redesign instead of adding it**.

— Jerry
