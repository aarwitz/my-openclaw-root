# Trading Intelligence — Canonical Root

This is the single source of truth for the OpenClaw AutoTrade desk.

- Authoritative docs live in `docs/`.
- Canonical SQLite schema lives in `sql/schema.sql`.
- Doc registry: `DOC_INDEX.md`.
- Decision log: `DECISION_LOG.md`.
- Retired material lives under `archives/` with date-stamped retirement notes.

## Topology (locked)

The live desk topology is the canonical `9 agents + Jerry` model described in `/home/aaron/.openclaw/SYSTEM_ARCHITECTURE.md`.

Trading-desk roles:

- `researcher` — background discovery and hypothesis generation
- `quant` — scoring, regime, expression selection, sizing
- `critic` — prospective challenge of every hypothesis and trade intent
- `risk` — intent-to-order veto, sizing limits, exposure and drawdown controls
- `trader` — portfolio-manager lane that authors sized intents within the risk budget
- `executor` — deterministic execution agent; only lane that submits/cancels paper orders and mirrors broker state
- `archivist` — post-resolution grading and pattern extraction
- `overseer` — Telegram/chat front door, cron orchestrator, and pipeline coordinator behind the Druck surface
- `developer` — software-improvement lane for desk code, scripts, connectors, schema, and app contract

Support role outside the desk count:

- `jerry` — default assistant and general OpenClaw platform/orchestration layer

## Authority

If anything outside this root contradicts a doc here, the doc here wins. Legacy druck workspace state has been decommissioned as of 2026-05-28 (see `DECISION_LOG.md`). This root is the single live trading authority.
