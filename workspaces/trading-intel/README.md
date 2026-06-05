# Trading Intelligence — Canonical Root

This is the single source of truth for the OpenClaw 6-agent trading system.

- Authoritative docs live in `docs/`.
- Canonical SQLite schema lives in `sql/schema.sql`.
- Doc registry: `DOC_INDEX.md`.
- Decision log: `DECISION_LOG.md`.
- Retired material lives under `archives/` with date-stamped retirement notes.

## Topology (locked)

Six OpenClaw agents share one canonical SQLite store and one repo of authoritative docs:

- `researcher` — background discovery and hypothesis generation
- `quant` — scoring, regime, expression selection, sizing
- `critic` — prospective challenge of every hypothesis and trade intent
- `archivist` — post-resolution grading and pattern extraction
- `trader` — only Telegram-facing agent (persona name: Druck); chat front door, orchestrator, and trading-infrastructure developer
- `executor` — deterministic execution agent; only agent that submits/cancels Alpaca paper orders and mirrors broker state

## Authority

If anything outside this root contradicts a doc here, the doc here wins. Legacy druck workspace state has been decommissioned as of 2026-05-28 (see `DECISION_LOG.md`). This root is the single live trading authority.
