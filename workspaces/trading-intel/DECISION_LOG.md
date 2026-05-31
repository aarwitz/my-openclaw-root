# Trading Intelligence — Decision Log

## 2026-05-28: Canonical reset

- D1: New canonical root established at `/home/aaron/.openclaw/workspaces/trading-intel`. All prior trading roots are migration sources only.
- D2: Topology locked at 5 agents: `researcher`, `quant`, `critic`, `archivist`, `trader`. `trader` is the sole Telegram-facing agent.
- D3: Options are first-class expression vehicles from launch: direct equity, ETF, LEAPS, shorter-dated catalyst options, pair trades, competitor shorts.
- D4: Canonical state store is SQLite at `~/.openclaw/state/trading-intel.sqlite`. Schema authority is `sql/schema.sql`.
- D5: Doc stack capped at 5 active docs in `docs/`. All `/workspaces/druck/*.md` trading docs are superseded.
- D6: Hybrid retirement policy. High-value historical docs are archived under `archives/` with date-stamped notes; low-value duplicates are hard-deleted.
- D7: Old `/home/aaron/.openclaw/workspace/trader/` prototype is retired. Useful Pydantic typed-state ideas are absorbed into `sql/schema.sql` and `docs/04_SHARED_STATE_SCHEMA.md`; prototype code archived.
- D8: `openclaw.json` gains an `archivist` agent. Existing four agent workspace paths are retained.
- D9: Telegram account `druck` continues to route to agent `trader`. No other Telegram bindings for trading agents.
- D10: Internal delegation allowlist includes all five trading agents.
- D11: Naming clarification: "Druck" is the human-facing Telegram persona presented by agent id `trader`; `trader` remains the execution agent id in architecture and schema.
- D12: Validation protocol replaced with leakage-resistant design: anonymized cases, negative controls, fake-date tests, and post-cutoff holdout metrics as primary evidence.
- D13: 90-day run is explicitly operational validation (plumbing/gates/reconciliation), not edge validation.
- D14: Fill realism is mandatory for performance reporting (slippage-adjusted P&L is benchmark series).
- D15: Execution gates now include data freshness, factor-overlap, explainability thresholds, and ADV-aware fill realism checks.
- D16: Automatic degraded mode added: broker anomalies or unresolved reconciliation force `exits_trims_only` pause.
- D17: Experiment tagging required (`experiment_id`) across intents, audits, patterns, postmortems, and validation cases.
- D18: Naming-collision cleanup required before live-capital consideration: remove or rename legacy `druck` agent id to avoid account-id confusion.
- D19: Aggressive cutover executed: legacy `druck` agent id removed from runtime config; trading topic routes now target `trader` (Druck persona).
- D20: Legacy workspace state purged: `/home/aaron/.openclaw/workspaces/druck` and obsolete archived prototypes removed.

## 2026-05-29: Production-readiness artifacts

- D21: Regime classifier v1 thresholds authored at `reference/regime_rules_v1.md` and seeded via `sql/seeds/regime_rules_v1.json` (`rule_version = "v1"`, `experiment_id = "regime_rules_v1_init"`). Quant is unblocked to implement deterministic regime classification. Default fail-closed remains `caution` when signals are stale or missing.
- D22: Researcher reasoning chain promoted to a versioned skill at `workspaces/researcher/skills/reasoning_chain_v1.md` (`experiment_id = "researcher_reasoning_v1"`). All downstream rows must carry this `experiment_id` so reasoning-chain changes are attributable.
- D23: Validation corpus scaffolded at `reference/validation_corpus/` with the JSON contract, one exemplar seed per class, and a paired `fake_date_variant` seed (`experiment_id = "validation_corpus_v1_seed"`). Operator must hand-build the full corpus to clear Phase 1 thresholds (≥30 post-cutoff, ≥60 negative-control, plus fake-date variants).

## 2026-05-30: Bootstrap start and continuous learning loop

- D24: Bootstrap operations mode approved for Alpaca paper with current corpus, provided all execution and reconciliation gates remain enforced. This is an operations start, not edge proof.
- D25: Dual-lane data policy adopted: real specific operations data for live trading decisions, masked evaluation corpus for leakage-resistant validation.
- D26: Continuous learning export flow added: resolved hypotheses can be exported into a review queue and promoted into approved raw cases, then converted into masked validation cases over time.

## Format

For future decisions, append entries with: id, summary, rationale, files touched, approver, date.
