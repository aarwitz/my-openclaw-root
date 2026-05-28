# Trading Intelligence — Decision Log

## 2026-05-28: Canonical reset

- D1: New canonical root established at `/home/aaron/.openclaw/workspaces/trading-intel`. All prior trading roots are migration sources only.
- D2: Topology locked at 5 agents: `researcher`, `quant`, `critic`, `archivist`, `trader`. `trader` is the sole Telegram-facing agent.
- D3: Options are first-class expression vehicles from launch: direct equity, ETF, LEAPS, shorter-dated catalyst options, pair trades, competitor shorts.
- D4: Canonical state store is SQLite at `~/.openclaw/state/trading-intel.sqlite`. Schema authority is `sql/schema.sql`.
- D5: Doc stack capped at 5 active docs in `docs/`. All `/workspaces/druck/*.md` trading docs are superseded.
- D6: Hybrid retirement policy. High-value historical docs are archived under `archives/` with date-stamped notes; low-value duplicates are hard-deleted.
- D7: Old `/home/aaron/.openclaw/workspace/trader/` prototype is retired. Useful Pydantic typed-state ideas are absorbed into `sql/schema.sql` and `docs/04_SHARED_STATE_SCHEMA.md`; prototype code archived.
- D8: `openclaw.json` gains an `archivist` agent. Existing four agents keep their workspace paths. `druck` agent stays defined for legacy migration safety but is not used by the trading system.
- D9: Telegram account `druck` continues to route to agent `trader`. No other Telegram bindings for trading agents.
- D10: Internal delegation allowlist includes all five trading agents.

## Format

For future decisions, append entries with: id, summary, rationale, files touched, approver, date.
