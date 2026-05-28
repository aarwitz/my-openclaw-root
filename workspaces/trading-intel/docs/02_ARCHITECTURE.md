# 02 — Architecture

Status: active. Defines agents, workspaces, routing, shared state, and hot vs cold data paths. Implements `01_OPERATING_AUTHORITY.md`.

## 1. Agents

Five OpenClaw agents share one canonical SQLite state store.

| Agent | Cadence | Primary role |
|---|---|---|
| `researcher` | Continuous background | Primary-source ingestion, hypothesis creation and update |
| `quant` | Background + on-demand | Scoring, regime, expression candidate ranking, sizing recs |
| `critic` | Background + pre-trade gate | Prospective challenge of hypotheses and trade intents |
| `archivist` | Background post-resolution | Post-mortems, pattern extraction, calibration feedback |
| `trader` | On-demand + scheduled checkpoints | Telegram front door, Alpaca paper execution, position mgmt |

Only `trader` is externally bound to Telegram.

## 2. Workspaces

- Each agent has its own OpenClaw workspace under `/home/aaron/.openclaw/workspaces/<agent>/`.
- Each workspace contains `AGENTS.md`, `IDENTITY.md`, `SOUL.md`. `AGENTS.md` references this canonical root for all authoritative docs.
- No agent stores trading authority docs in its own workspace. Authority lives only in `/workspaces/trading-intel/`.

## 3. Routing

- Telegram account `druck` is bound to agent `trader`. No other Telegram bindings for trading agents.
- Internal agent-to-agent delegation is enabled for all five trading agents: `researcher`, `quant`, `critic`, `archivist`, `trader`.
- Default internal coordination is through shared state, not chat. Agent-to-agent calls are for explicit, narrow handoffs (e.g., trader asks quant for a fresh size recommendation before executing).

## 4. Shared state

Canonical store: SQLite at `~/.openclaw/state/trading-intel.sqlite`. Schema authority: `sql/schema.sql`. Entity model: `04_SHARED_STATE_SCHEMA.md`.

Write permissions, enforced at the application layer:

| Agent | Writes |
|---|---|
| `researcher` | `hypotheses` (new + evidence), `hypothesis_evidence`, `falsifier_signals` |
| `quant` | `hypotheses` (scores, expression candidates, sizing), `regime`, `trade_intents` (creation) |
| `critic` | `critic_reviews` (on hypotheses and trade intents) |
| `trader` | `trade_intents` (execution fields), `orders`, `positions`, `tranches` |
| `archivist` | `hypotheses` (resolution + grade), `postmortems`, `patterns` |
| all | `audits` (append-only) |

All five agents can read everything.

## 5. Hypothesis lifecycle

States: `raw` → `scored` → `challenged` → `ready` → `active` → `dormant` → `resolved` → `retired`.

Transitions (responsible agent in parens):

- `raw` → `scored` (quant): score, edge decay, regime fit.
- `scored` → `challenged` (critic): challenges issued, requires responses.
- `challenged` → `ready` (quant): all challenges addressed; expression candidates ranked.
- `ready` → `active` (trader): first tranche opened.
- `active` → `dormant` (quant or researcher): a falsifier fired or evidence weakened.
- `active`/`dormant` → `resolved` (archivist): position fully closed or thesis time horizon expired.
- `resolved` → `retired` (archivist): pattern extraction complete.

Detail in `03_EXECUTION_STATE_MACHINE.md`.

## 6. Hot path vs cold path

Hot path (always on, every loop):

- Alpaca account, open orders, positions.
- Active hypotheses with `state in (ready, active)` and their top-ranked expressions.
- Live regime snapshot.
- Core feeds: SEC EDGAR, FRED, EIA, ClinicalTrials.gov, openFDA, USAspending, BLS, market price/volume.

Cold path (on-demand by researcher when a hypothesis needs deeper underwriting):

- Patents (USPTO), trade flows (UN Comtrade), procurement, FERC, NOAA climate, OpenAlex/PubMed, USDA WASDE, USGS, satellite/alt-data (if budgeted).

Operating rule: broad access at launch, narrow default consumption. Researcher promotes a cold source into a hypothesis's evidence record only when that source materially changes confidence or falsifier status.

## 7. OpenClaw capability surfaces (how each is used)

- Tools: live broker and data actions (Alpaca, primary-source APIs).
- Skills: repeatable agent playbooks (researcher reasoning chain, critic checklist, trader execution loop).
- Plugins: only for genuine capability gaps not covered by tools/skills.
- Automation: scheduled background jobs per `05_IMPLEMENTATION_POLICY.md`.
- Subagents / specialist lanes: only when they reduce a real bottleneck (e.g., long-running deep-research pulls under researcher).

## 8. Telegram interface (trader)

User-facing commands handled by `trader`:

- `/summary` — active hypotheses, positions, regime, P&L vs SPY.
- `/hypothesis <id>` — full record.
- `/intent <id>` — pending trade intent and Critic state.
- `/approve <id>` / `/reject <id>` — manual override path.
- `/exit <position_id>`, `/trim <position_id> <pct>`.
- `/regime` — current regime and gates.
- `/critic <hypothesis_id>`, `/archivist <hypothesis_id>`.
- `/audit <period>`.

No other agent has Telegram presence. Researcher, quant, critic, and archivist escalate to the human only by writing to shared state, which trader surfaces on the next checkpoint or via `/summary`.

## 9. Authority chain

This doc implements `01_OPERATING_AUTHORITY.md`. It is implemented by `03_EXECUTION_STATE_MACHINE.md` and `04_SHARED_STATE_SCHEMA.md`. Runtime configuration in `/home/aaron/.openclaw/openclaw.json` must reflect the agent set and bindings defined here.
