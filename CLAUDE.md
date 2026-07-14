# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

This is **`~/.openclaw`** — the home/state directory of an **OpenClaw gateway** deployment, tracked
in git as a curated subset (the `.gitignore` is deny-by-default: everything is ignored unless
explicitly re-allowed). It is *not* a conventional application codebase. It is **config + agent
workspaces + deterministic ops/trading scripts as code** for a fleet of LLM agents that run inside a
single containerized gateway.

The headline workload is **AutoTrade**: a self-improving, agentic *paper*-trading desk (a hedge-fund
org of specialised agents) running a research → decision → execution → learning loop against an
Alpaca paper account. Alongside it run general-purpose assistant/dev agents (Jerry, Dwight, Resi) and
a Task Manager integration.

## Authoritative docs (read these first, in this order)

1. **`SYSTEM_ARCHITECTURE.md`** — the single source of truth for AutoTrade (topology v4, DB schema
   v11/v12). **When any other doc disagrees, this one wins.**
2. **`TELEGRAM_EXECUTION_GUIDE.md`** — operator (human-over-Telegram) interface and the `run issue`
   coding command contract.
3. **`cli_guide.md`** — bring the gateway up/down, Tailscale exposure, pairing.
4. Per-desk deep docs live under `workspaces/trading-intel/` (`DOC_INDEX.md`, `docs/`, `sql/`,
   `OPERATOR_GUIDE.md`, `HUMAN_USE_GUIDE.md`, `DECISION_LOG.md`).

(Superseded docs — `ARCHITECTURE.md`, `FULL_DESIGN_ASCII.md`, `02_ARCHITECTURE.md` — were archived
2026-07-02 to `archive/docs-retired-20260702/`.)

## Non-negotiable safety invariants

These are the rules most likely to cause real damage if violated:

- **Never `systemctl restart` / `systemctl --user restart` the gateway, and never casually recreate
  the `openclaw-gateway` container** (`docker compose up -d`, `docker rm`). Both restart the gateway
  and risk corrupting the **single-use `openai-codex` OAuth refresh token** that the whole fleet
  shares — invalidating auth for every agent. Back up first with `scripts/token-backup.sh` and get
  operator confirmation.
- **Config hot-reloads.** Editing `openclaw.json` or `cron/jobs.json` is applied within seconds; an
  invalid config is skipped and the last-good retained (`openclaw.json.last-good`). You almost never
  need a restart. If one is truly unavoidable, use **`~/.openclaw/scripts/safe-restart.sh`** only.
- **Paper account only** — this is not live trading capital, but treat broker/order paths as
  outward-facing.
- **Human-gated boundaries:** auto-merge, deploy, external sends, account changes, and destructive
  ops are always approval-gated. Structural/parameter changes to the trading logic flow as
  `rule_proposals` that a human approves; **agents never self-approve.** Task Manager state must
  never auto-launch non-code work or anything assigned to Aaron.
- Host Docker/root actions are **operator-owned** (terminal / Claude Code session on the host) —
  the gateway container has no `docker.sock` mounted and every agent, including `jerry`, executes
  inside it. `jerry` (the one default-assistant agent, bound to Telegram) gets real-filesystem
  reach via the container's bind mounts (notably `/home/aaron/repos:rw` and `~/.openclaw`), not
  via host exec. A paired-but-unused `rsl-host` node host exists (systemd user service,
  `openclaw-node.service`, stopped) — the Codex harness runs its own in-process shell and does
  not honor `tools.exec.host=node`, so node routing is a dead end in this build.
  `scripts/jerry-host-poll.py` (host cron) is a supplementary Task-Manager-triggered dispatch
  path into the same `jerry` agent, not a separate agent.

## Governed-script policy (applies to every script you write or run here)

`scripts/README.md` + `scripts/policy.json` are canonical. Directories listed in `policy.json`
`governedDirs[]` (currently `scripts/` and `workspaces/trader/scripts/`) MUST begin with the wrapper
guard and may only be executed through the trace runner:

```bash
# Run any governed script (writes a JSONL trail to logs/script-runs.jsonl):
~/.openclaw/scripts/run-with-trace.sh [--tag manual|audit|hook|cron|verify|test] <script> [args...]

# Create a new governed script (wrapper guard baked in — never hand-write the boilerplate):
~/.openclaw/scripts/new-script.sh <name>.{sh,py} [target-dir]

# Enforce wrapper coverage (CI/pre-commit gate; non-zero on violations):
~/.openclaw/scripts/scripts-policy-lint.sh
```

The guard (`scripts/lib/require-wrapper.sh` / `require_wrapper.py`) auto-reexecs through
`run-with-trace.sh` by default; with `OPENCLAW_REQUIRE_WRAPPER_NO_AUTORUN=1`, direct invocation
exits `126`. Scripts in the `quant`, `critic`, `risk`,
`archivist`, `executor`, and `trading-intel` workspaces are **plain Python** (not governed). Don't
delete scripts on age alone — follow the four-point deletion rule in `scripts/README.md`.

## Runtime topology

- **One containerized gateway** (`openclaw-gateway`, host `127.0.0.1:18789`, compose:
  `docker-compose.openclaw.yml`). **All** fleet agents run *inside this single container* — there is
  no process boundary per agent. `~/.openclaw` is bind-mounted at the same absolute path, so
  script paths/permissions match the host.
- **Sandbox is OFF** (`agents.defaults.sandbox.mode = "off"`, `tools.exec.host = "gateway"`): exec
  runs directly on the gateway host.
- **Model harness:** every agent uses the bundled Codex app-server harness
  (`models.providers.openai.agentRuntime.id = "codex"`), OAuth-based — **no API keys, no per-agent
  model selection**. ACP is disabled by policy.
- **Task Manager** runtime is hosted at **`https://tm.lidisolutions.ai`**. All TM client scripts honor
  `TASK_MANAGER_URL`; local host-port aliases are retired and forbidden.
- Exposure: localhost-only gateway, fronted by Tailscale serve, controlled over Telegram (paired,
  allowlisted users). See `cli_guide.md`.

## The agent fleet & workspace convention

Agents are defined in `openclaw.json` (`agents.list`): `jerry` (default assistant, bound to
Telegram), `resi` (AutoTap, untouched), and the AutoTrade desk — `researcher`, `quant`, `critic`,
`risk`, `trader` (PM), `executor`, `archivist`, `overseer` (CIO/orchestrator), `developer`. `dwight`
was **decoupled from the desk pipeline 2026-06-17** and since **2026-07-14 is the PM of the ATS v6
Trading Intel sprint** (Task Manager sprint_id=5, and ONLY that sprint): a daily cron pass grooms
the sprint board, files at most one issue/day, dispatches ≤2 coding-lane runs/day (always
`--detach`), and Telegrams Aaron a summary. Coding-lane completion is deterministic: the launcher
(`scripts/dwight-launch-from-issue.py`) pushes the task branch, opens the PR via `gh`, and flips
the issue to `in_review` — merge stays human-gated. The `developer` agent does the actual coding
in per-issue Codex subagent sessions. Every agent (including `jerry`) runs through the
Gateway inside `openclaw-gateway`. `jerry` works on the real filesystem via the container's bind
mounts — `/home/aaron/repos` is mounted read-write, so it can create/edit real repos from
Telegram — but host docker/systemd operations remain operator-owned (no `docker.sock` in the
container). Do not re-attempt `tools.exec.host=node` routing for host access: the Codex
app-server harness executes shell in-process and bypasses OpenClaw's node exec dispatch
(verified 2026-07-11).

Each agent has a `workspaces/<agent>/` directory following a consistent file convention — read these
to understand an agent's contract before changing its behavior:

- **`IDENTITY.md`** — id, persona, role, write-scope boundaries, telegram binding, authority caps.
- **`AGENTS.md`** — the operating instructions / system prompt body (authority, role, safety, subagent rules).
- **`SOUL.md`**, **`USER.md`**, **`HEARTBEAT.md`** — persona/voice, operator profile, heartbeat behavior.
- **`TOOLS.md`** — tool allowlist notes (incl. the scripts-policy pointer).
- **`scripts/`** — the agent's deterministic Python (trader's is governed; others are plain).

## AutoTrade pipeline (the core domain logic)

**Determinism-first invariant:** every *number* (regime, scoring, prediction probability, sizing,
risk caps, gate verdicts, calibration) is produced by a **deterministic Python script** reading one
SQLite store. **LLM agents author only judgement/prose and orchestrate — they never invent numbers.**

- **Single store:** `state/trading-intel.sqlite` (WAL, schema v11/v12, ignored by git). DDL:
  `workspaces/trading-intel/sql/schema.sql`; forward migrations: `sql/migrations/NNNN_<slug>.sql`.
  Everything is threaded by `experiment_id`; **every** state transition writes an `audits` row.
- **One pass = `scripts/trader-pass-deterministic.sh`** runs the deterministic prefix and emits one
  JSON. Stage order: `classify_regime → value_universe → score_hypotheses → critic_baseline →
  predict → author_intents → gate_evaluator → risk_gate → execute_intent → reconcile → scoreboard →
  macro → snapshot`. Each stage is a script under the corresponding agent workspace (see
  `SYSTEM_ARCHITECTURE.md` §5 for the script-per-stage map).
- **Intent state machine (non-bypassable):**
  `proposed → (critic 10-gate stack) → risk_review → (Risk agent) → approved → (executor) → submitted
  → filled`; any stage can route to `blocked`. The **Risk gate**
  (`workspaces/risk/scripts/gate_risk_intents.py`) is the single source of truth for limits and is
  **fail-closed** — if equity can't be read, intents stay in `risk_review` and it exits non-zero.
- **World Model & Calibration** (`workspaces/trading-intel/scripts/worldmodel.py` + `mechanisms`,
  `predictions`, `episodes`, `macro_releases`, `valuations`, `portfolio_risk` tables) is the learning
  engine: Beta-posterior beliefs over causal mechanisms updated from realised outcomes. Two learning
  rates — *fast/autonomous* (mechanism Beta updates) and *slow/human-gated* (`rule_proposals`).

## Orchestration (cron)

`cron/jobs.json` is the live, hot-reloaded job source (no cron→DB migration in this build). The
**overseer** drives five market-time weekday passes (`OVERSEER-DRIVE-V2`: pre-market, open,
confirmation, rotation, close-risk ET) plus a daily post-close learning pass and a weekly Sunday
audit. Each pass runs the deterministic core, then spawns agents in strict order
`researcher → quant → critic → trader → risk → executor → archivist` via Codex `spawn_agent`/`wait`,
explicitly `close_agent`-ing each child before the next. Telegram narration goes out on the `druck`
bot.

Inspect/enable jobs:
```bash
python3 -c "import json; [print(j['enabled'], j['name']) for j in json.load(open('cron/jobs.json'))['jobs']]"
```

## Common operator commands

```bash
openclaw health                              # gateway health
openclaw gateway status                      # service status
bash ~/.openclaw/scripts/safe-restart.sh     # ONLY token-safe restart path
openclaw logs                                # gateway logs

# Coding lane regression / boundary tests:
bash ~/.openclaw/scripts/test-coding-lane-regression.sh
bash ~/.openclaw/scripts/test-repo-boundary-hardening.sh

# Telegram routing regression guardrail (run after touching agent/channel config):
python3 ~/.openclaw/scripts/audit_telegram_routing.py

# Token safety around any operation that might restart the gateway:
bash ~/.openclaw/scripts/token-backup.sh      # backups land in credentials/token-backups/
bash ~/.openclaw/scripts/token-restore.sh
```

There is no global build/lint/test runner — verification is per-script (run the relevant
deterministic stage or its workspace test) and via the regression scripts above. SQL schema changes:
edit `sql/schema.sql` (idempotent style) **and** add a paired `sql/migrations/NNNN_<slug>.sql`, then
apply to the live DB after operator confirmation and append a `DECISION_LOG.md` entry.
