# AGENTIC_SYSTEM.md — the improvement kernel

**What this is:** the design contract for LIDI's recursively self-improving agentic system.
AutoTrade is the first project on it; CleaningRobot and others follow. This doc is
project-agnostic on purpose — project-specific knowledge lives in per-project plugins, never in
the kernel. When another doc disagrees about how the *improvement loop* works, this one wins
(SYSTEM_ARCHITECTURE.md remains the authority for AutoTrade's trading pipeline itself).

## The one-paragraph theory

A system improves recursively when it (1) **measures its own deficiencies**, (2) turns the top
deficiency into a **well-formed unit of work**, (3) executes that work as a **reviewable code
change**, (4) passes a **human quality/safety gate**, and (5) **verifies against the same
measurement** that the deficiency actually shrank — then repeats. Projects differ only in step 1
(what they measure). Steps 2–5 are identical machinery for a trading desk, a robot, or this
platform itself. So: one kernel, many telemetry plugins.

## Design principles (each one was paid for)

1. **Determinism-first.** LLMs author judgment and prose; scripts own every mechanical step
   (claiming, dispatching, pushing, PR-opening, status flips) and every number. Any step that
   depends on an LLM "remembering to do it" will silently stop happening. (Proof: PR-opening
   worked 0 times as an agent instruction, 100% once the launcher owned it.)
2. **Silence is a failure mode, not a state.** Every loop must be observable somewhere a human
   actually looks (TM Progress page, Telegram summaries, health sweep). Workers that can hang
   without anyone noticing are bugs by construction. (Proof: EWAG workers hung 18 days; nobody knew.)
3. **Telemetry outranks intuition.** Work items come from measured deficiencies, not from an
   agent's idea of what sounds useful. Filing from "inspect the repo for TODOs" produces
   plausible busywork; filing from "11 trades blocked in 14 days by the executor short-mapping
   gap" produces improvement. Every filed issue carries its motivating signal (`drag:<signal-id>`).
4. **Few standing agents, unlimited ephemeral workers.** Standing identities (Dwight, jerry, the
   desk) are doc-maintenance liabilities that drift (proof: Dwight's docs described a dead
   architecture within 3 weeks). Capacity scales by spawning *per-task sessions* (the coding
   lane), not by adding named agents. "Hundreds of agents" = hundreds of concurrent ephemeral
   tasks; the standing roster stays small enough to keep true. Adding/removing a standing agent
   is itself a code change (openclaw.json) that flows through the lane as a PR.
5. **Human attention is the budgeted, load-bearing resource.** Aaron's interface is: merge PRs,
   approve rule_proposals, answer ≤3 bullets per project per day, and the TM pause button.
   Everything mechanically verifiable is verified by machines so that what reaches him is small
   and high-signal. Merge stays human forever — that gate IS the safety architecture for a
   self-modifying system.
6. **Standing state drifts; registries and drift-checks fight it.** Env files, compose env,
   agent docs, and watchers all rotted silently after migrations. Anything duplicated will
   diverge: keep one source of truth (projects.json, openclaw.json, this doc) and make the
   health sweep check the copies.

## The loop (kernel view)

```
        ┌─────────────────────────────────────────────────────────────┐
        ▼                                                             │
  [1 MEASURE]  project telemetry plugin → ranked deficiency signals   │
        │           (per-project script, JSON contract below)         │
  [2 BACKLOG]  PM pass: top unaddressed signal → ONE well-formed      │
        │      TM issue (sprint board = project), tagged drag:<id>    │
  [3 EXECUTE]  launcher dispatches (--detach) → ephemeral coder       │
        │      session → commits on task branch → contract JSON       │
  [4 GATE]     launcher pushes branch, opens PR, issue → in_review;   │
        │      HUMAN reviews & merges (never automated)               │
  [5 VERIFY]   next PM pass re-reads telemetry: did the motivating    │
               signal shrink? verdict commented on the issue ─────────┘
               (regressed → follow-up issue; that's the recursion)
```

Two tiers, every project: a **fast loop** inside the project (AutoTrade: Beta-posterior mechanism
updates; a robot: parameter tuning within a safe envelope) that adjusts *weights* autonomously,
and this **slow loop** that changes *code* through the human gate. The kernel is the slow loop.

## Components

- **Project registry — `~/.openclaw/projects.json`.** One entry per project:
  `name`, `status` (active|paused), `repos[]`, `sprint_id` (its TM board), `tm_assignee` default,
  and `telemetry_cmd` (the plugin). The PM pass loops over `status=active` — activating a project
  is a one-field flip, not a prompt rewrite.
- **Telemetry plugin contract.** `telemetry_cmd` is any deterministic command printing JSON:
  ```json
  { "project": "<name>", "generated_at": "<iso>", "signals": [
      { "id": "stable-kebab-slug", "severity": 0-100,
        "summary": "one line with the number in it",
        "evidence": ["concrete measurements"],
        "suggested_issue": { "title": "...", "acceptance_criteria": "...", "assignee": "Developer" } } ] }
  ```
  Signals ranked by severity; `id` is stable across runs so the PM can dedup against open issues
  (`drag:<id>` tag in the issue description) and verify post-merge. A project with no plugin yet
  degrades to roadmap-inspection filing — allowed, but the PM must say telemetry was unavailable.
  AutoTrade's plugin: `workspaces/trading-intel/scripts/drag_report.py` (reads the desk SQLite
  read-only). CleaningRobot's will read test/crash/bump logs. The platform's own plugin is the
  health sweep + lane-failure stats (the system files issues about itself through the same lane).
- **PM lane.** One PM (Dwight) runs the same daily pass over every active project. The pass is
  generic; all project-ness comes from the registry + plugin. When project count outgrows one
  PM's daily pass, the registry gains a `pm` field and a second PM agent — a PR, not a rewrite.
- **Execution lane.** `scripts/dwight-launch-from-issue.py --execute --detach` (see
  TELEGRAM_EXECUTION_GUIDE.md §7 for the full contract): claim → ephemeral coder session →
  commit on task branch → push → PR → `in_review` → outcome-first TM comment → original branch
  restored. Dispatch caps live in the PM instructions (currently 2/day) and are a throughput
  knob, not architecture.
- **Gates.** Merge = Aaron. rule_proposals (AutoTrade) = Aaron. TM pause button = instant global
  agent lockout (HTTP 423; agents stop and report, never retry around it). Robot-class projects
  add a hardware-safety tier: nothing that moves physical actuators ships without explicit
  per-change human sign-off, regardless of test results.
- **Observability.** TM Progress page (burndown + activity feed) is the human window; Telegram
  daily summaries are the push channel; `scripts/system-health-sweep.py` is the watchdog. New
  automation must register a heartbeat/log the sweep can check — a worker the sweep can't see
  hung is not allowed to exist.

## Onboarding a new project (the whole checklist)

1. Create its TM sprint (one board per project; boards are independent — multiple sprints active
   at once is normal).
2. Add the registry entry (repos, sprint_id, assignee, status=paused).
3. Write its telemetry plugin (start tiny: two or three signals it can measure honestly).
4. Define its fast loop (what may self-tune autonomously) and its safety tier (what is
   human-gated beyond merge).
5. Flip `status=active`. The next PM pass picks it up with zero prompt changes.

## What "working" means (and the kill criterion)

The meta-metric is **signal retirement**: merged PRs whose motivating telemetry signal measurably
shrank and stayed shrunk. Secondary: Aaron's manual interventions trending down; no silent-stall
recurrences. Reviewed monthly against the Progress page. **Kill criterion, stated honestly:** if
a month of merged PRs retires no signals, the kernel is overhead cosplaying as improvement — cut
the PM cadence back or simplify the machinery. The system must be allowed to conclude that about
itself; that conclusion would itself arrive as a telemetry signal.
