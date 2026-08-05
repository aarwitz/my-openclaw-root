# AutoTrade v1 retired

AutoTrade v1 was deliberately retired on **2026-08-05**.

## Retirement state

- All 11 OpenClaw gateway jobs remain defined but are disabled.
- The gateway scheduler has no AutoTrade next wake.
- AutoTrade host-cron jobs are removed by `scripts/retire-autotrade.sh`.
- No AutoTrade systemd service or timer was active at retirement.
- The internal paper ledger, features, raw events, logs, and historical research
  remain under `state/` and `logs/`; they are evidence, not a live authority.
- A forensic manifest and the interrupted final patch are stored under
  `archives/autotrade-retired-20260805/` on this host.
- The architectural postmortem is [`bitter_lessons.md`](bitter_lessons.md).

## Boundary

This repository is an archived implementation and forensic source. Do not add
new AutoTrade strategies, agents, graph machinery, or trading jobs here. The
standalone successor must have no runtime dependency on `.openclaw`, OpenClaw,
Telegram, gateway cron, agent sessions, or LLM availability.

Reactivation requires an explicit operator decision and a documented recovery
plan. Do not enable individual jobs opportunistically.
