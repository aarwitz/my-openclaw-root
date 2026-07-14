# AUTONOMOUS_ORG — the dev-org operating model (2026-07-03)

Goal: one substrate that autonomously develops every product — AutoTrade,
ResiLife (EWAG iOS), MONTRA, AutoTap/OpenClawQA, CleaningRobot, Task Manager —
and eventually runs deployed product agents (robot). POC-performance first;
hard security later, but the anti-self-destruction invariants stay (they ARE
performance: an org that corrupts its own auth ships nothing).

## The model: one queue, one coding lane, product registry, owner loops

```
   projects.json ──────────────┐  (single source of truth: repo paths, slugs,
                               │   verify/deploy commands, tm_assignee, owner)
                               ▼
  org-owner-daily (dwight) ── files/curates READY issues per product ──► TM
                               │                                     (the queue)
                               ▼
  dwight-launch-from-issue.py ── claim → launch-coding-task.sh (codex-subagent)
                               │   strict JSON contract {status,branch,pr,evidence}
                               ▼
                     branch + PR + TM comment  ──►  HUMAN merge/deploy gate
```

- **Queue**: Task Manager (tm.lidisolutions.ai, CF Worker + D1). Fixed 2026-07-03
  (init stalls, D1 param crash, scoping); one repo, one branch, one impl.
- **Registry**: `~/.openclaw/projects.json`. Add a product here → the repo
  inference, TM allowlist, and org-owner loop all know it. This file is the
  org's hiring paperwork.
- **Coding lane**: ONE lane writes code — the gateway codex-subagent lane
  (`launch-coding-task.sh`), dispatched by `dwight-launch-from-issue.py`.
  Repaired 2026-07-03: the default launcher `dwight-assign-coding-task.sh` was
  referenced but missing (auto-dispatch silently broken); TM auth fallback
  added (shared credential file). Registry-driven repo resolution.
- **Runner containers** (`lidi-task-manager-ewag-agent-*`, 6): branch/PR
  scaffolding + claim-protocol workers only — they contain NO model runtime and
  never write code. Keep as scheduling/scaffold layer; codegen stays in-gateway.
- **Host lane**: jerry-host-poll (host-ops issues) — the "agent that repairs
  agents". Unchanged.
- **Owner loops**: `org-owner-daily` cron (dwight, weekdays 11:00 ET) walks the
  registry: backlog health per product, files ONE well-formed ready issue where
  the funnel is empty, dispatches the coding lane (cap 2/day POC), nudges stale
  work, one Telegram summary. This is the generalization of the trading desk's
  overseer — the pattern that made AutoTrade self-driving, applied to the org.

## Roles

| Agent | Org role |
|---|---|
| dwight | Engineering/product owner: backlog curation, dispatch, TM maintenance |
| developer | The coding hands (codex-subagent lanes run as this identity) |
| jerry (host) | Host/infra repair — the agent that repairs agents |
| overseer + desk | AutoTrade's self-contained sub-org (the template) |
| main (Jerry) | Aaron's assistant; not in the dev loop |
| (future) robot agent | Deployed-product agent — see below |

## Coordination doctrine (settled 2026-07-03 after operator challenge)

- **TM is a work LEDGER, not a communication medium.** An issue = a unit of work
  with acceptance criteria and a lifecycle (durable across crashes, idempotent
  via claim tokens, human-visible, runtime-agnostic). Collaboration WITHIN a
  work item happens in one rich context — subagent spawns, shared files, direct
  tool calls — never through TM comment ping-pong. One structured result per
  issue. Agents filing issues nothing consumes = complaint box = anti-pattern
  (the ~200 undrained desk-filed issues are the standing example to triage).
- **Front agents orchestrate; lanes code — with an inline exception.** Agents
  the operator talks to (dwight, overseer, main) keep their context for product
  knowledge and priorities and dispatch coding to fresh subagent lanes
  (parallelism + failure isolation). Exception: trivial changes (~1-2 files,
  <15 min) are done inline — spawning a lane for a two-line fix loses more in
  handoff than it protects. State which mode you chose.
- **Topology is second-order.** The org's real throughput caps are verification
  (QA gate on PRs — wire openclaw-qa) and issue quality (one well-formed issue
  beats five vague ones). Optimize those before re-architecting coordination.

## Invariants that stay (even in POC mode)

1. **PRs only** — no agent merges to main/master or deploys without a human.
2. **Never assign work to Aaron; never auto-launch non-code work.**
3. **Gateway token safety** — no restarts outside safe-restart.sh.
4. **AutoTrade trading logic changes flow as rule_proposals**, never PRs-to-merge.
5. **Every lane reports back to TM** (structured comment) — silent work is lost work.

## What each product needs to be fully autonomous (state 2026-07-03)

- **AutoTrade** — already self-driving (crons + desk). Dev work flows via TM.
- **ResiLife** — repo active, runners exist. Needs: verify command validated on
  this host (xcodebuild needs a Mac runner — likely remote via openclaw-qa
  SSH runners), backlog seeded.
- **MONTRA** — iOS app: EWAG-dev/MONTRA on mac-dev; web console in
  lidi-solutions/public/solutions/montra_dev.
- **AutoTap/OpenClawQA** — the org's QA arm. Highest-leverage next build: wire
  its engine as the VERIFY step for iOS products' PRs (QA evidence attached to
  TM before human merge).
- **CleaningRobot** — repo at ~/CleaningRobot (firmware + vision). The deployed
  robot agent is a NEW runtime class: an OpenClaw agent off-gateway with
  hardware tools. Design doc needed before code (sensor/actuator tool surface,
  update channel, kill switch).
- **Task Manager** — healthy; dwight sole maintainer.

## Execution nodes (2026-07-03)

- **mac-dev** — taylorolsen-vogt@100.125.133.123 (Taylor's Air, macOS 26.4.1,
  Xcode 26.6). ALL iOS dev/build/test/QA. SSH keyed from this host (verified).
  Repos on it: AutoTap (aarwitz/AutoTap — the real AutoTap source), MONTRA
  (EWAG-dev/MONTRA), plus RN test apps. ResiLife clone pending (no
  non-interactive GitHub auth on the mac — needs a deploy key or manual clone).
- **robot-orin** — taylor@ubuntu, NVIDIA Orin Nano = the actual robot; live
  tests run there; repo at ~/workspaces/CleaningRobot. SSH pending (add this
  host's pubkey).
- **gateway host** (this machine) — everything else: web products, TM, the
  desk, coding lanes. Remote work = the lane SSHes to the node (mac-dev
  pattern; openclaw-qa engine natively supports SSH runners).

## Operator inputs wanted

1. Orin SSH: add this host's pubkey (~/.ssh/id_rsa.pub) to
   taylor@ubuntu:~/.ssh/authorized_keys.
2. Mac GitHub auth for cloning EWAG-dev/iosApp non-interactively (deploy key,
   or clone it once by hand).
3. Grant aaronclawrsl-bot access to aarwitz/CleaningRobot + aarwitz/AutoTap +
   EWAG-dev/MONTRA (so lanes can push branches/PRs).
4. Throughput: the 2-dispatch/day POC cap — raise when the loop proves itself.

## Known deferred-security debt (revisit before anything faces the world)

- myAgentRepo holds plaintext account credentials in a git repo.
- Bot GitHub token in env; TM session token shared across host clients.
- Runner containers mount host repos read-write.
