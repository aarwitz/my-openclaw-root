# 02 - Architecture

Status: active. Canonical description of the OpenClaw trading system topology, source map, decision flow, execution loop, and feedback loops. Implements [01_OPERATING_AUTHORITY.md](01_OPERATING_AUTHORITY.md).

## 1. System shape

The trading stack is thesis-first, paper-first, and single-store.

- One canonical SQLite store holds hypotheses, evidence, regime, intents, orders, positions, pauses, postmortems, and validation cases.
- Five agents share that store and do not maintain competing copies of trading authority.
- `trader` is the only Telegram-facing lane. The displayed persona name is Druck.
- Telegram is the operator surface, not the execution engine.
- Alpaca paper is the only broker surface in launch scope.
- External APIs are ingested into evidence first; they do not directly place trades.

## 2. Topology at a glance

```text
                ┌─────────────────────────────────────────────────────────────┐
                │                 External Information Surfaces               │
                │  SEC | FRED | EIA | ClinicalTrials.gov | openFDA | ...      │
                │  USAspending | BLS | USGS | USPTO | arXiv | UN Comtrade      │
                │  NOAA | OpenAlex | PubMed | USDA WASDE | FERC | Alpaca       │
                └───────────────┬─────────────────────────────────────────────┘
                                │
                                v
                      ┌───────────────────────┐
                      │ researcher            │
                      │ source ingestion      │
                      │ hypothesis creation   │
                      │ falsifier tracking    │
                      └──────────┬────────────┘
                                 │ evidence + falsifiers
                                 v
                      ┌───────────────────────┐
                      │ quant                 │
                      │ scoring               │
                      │ regime classification │
                      │ expression ranking    │
                      │ sizing recommendations│
                      └──────────┬────────────┘
                                 │ candidate intents
                                 v
                      ┌───────────────────────┐
                      │ critic                │
                      │ prospective review    │
                      │ challenge closure     │
                      └──────────┬────────────┘
                                 │ reviewed intents
                                 v
                      ┌───────────────────────┐
                      │ trader (Druck)        │
                      │ Telegram front door   │
                      │ Alpaca paper submit   │
                      │ order/position sync   │
                      └──────────┬────────────┘
                                 │ broker events + fills
                                 v
                      ┌───────────────────────┐
                      │ reconciliation        │
                      │ audit rows            │
                      │ pauses / divergence    │
                      └──────────┬────────────┘
                                 │ resolved cases
                                 v
                      ┌───────────────────────┐
                      │ archivist             │
                      │ postmortems           │
                      │ patterns              │
                      │ calibration feedback  │
                      └──────────┬────────────┘
                                 │ feedback to research + quant
                                 v
                      ┌───────────────────────┐
                      │ shared SQLite store   │
                      │ trading-intel.sqlite  │
                      └───────────────────────┘
```

## 3. Agent contracts

| Agent | Role | Writes | Primary loop |
|---|---|---|---|
| `researcher` | Ingests source deltas and builds new theses | `hypotheses`, `hypothesis_evidence`, `falsifier_signals` | Source -> evidence -> thesis |
| `quant` | Scores, ranks, sizes, and classifies regime | `hypotheses`, `regime`, `trade_intents` | Evidence -> score -> candidate intent |
| `critic` | Challenges hypothesis and intent quality before capital risk | `critic_reviews` | Intent gating loop |
| `trader` | Telegram-facing Druck persona and only execution lane | `trade_intents`, `orders`, `positions`, `tranches` | Human command -> broker -> reconciliation |
| `archivist` | Grades outcomes and extracts reusable patterns | `hypotheses`, `postmortems`, `patterns` | Resolution -> attribution -> calibration |

All five agents can read all trading state. Write permissions are application-enforced, not implicit.

## 4. Canonical external sources

This is the approved source stack. Sources are read into evidence records; they never bypass the shared store.

| Source | Official name / API surface | Main payloads used | Primary owner | Path |
|---|---|---|---|---|
| Broker + market data | Alpaca Trading API (paper) + Alpaca Market Data API | account, orders, positions, fills, bars, trades, quotes | trader | Hot path |
| Corporate disclosures | SEC EDGAR | 8-K, 10-Q, 10-K, 13D/G, S-1, exhibits, XBRL | researcher | Hot + event-driven |
| Macro series | FRED + ALFRED | level, vintage, revisions, release timestamps | researcher, quant | Hot + cold |
| Energy | EIA API | inventories, production, storage, prices, weekly deltas | researcher | Hot + cold |
| Trials | ClinicalTrials.gov API | trial status, phase, enrollment, endpoints, sponsor changes | researcher | Hot + cold |
| Drug/regulatory | openFDA APIs | adverse events, labels, recalls, enforcement signals | researcher | Hot + cold |
| Fiscal demand | USAspending.gov API | awards, obligations, agency flows, timing, counterparties | researcher | Cold |
| Labor / inflation | BLS Public Data API | CPI, PPI, payrolls, unemployment, JOLTS | researcher, quant | Hot + cold |
| Geo / physical shocks | USGS APIs | earthquakes, seismic events, water/climate alerts where relevant | researcher | Cold |
| Innovation | USPTO patent data / patent APIs | publications, claims, assignees, citations, families | researcher | Cold |
| Preprints | arXiv API / OAI-PMH | preprints, categories, authors, abstract metadata | researcher | Cold |
| Trade flows | UN Comtrade API | import/export volumes, country flows, product shifts | researcher | Cold |
| Climate | NOAA climate data services | weather, anomalies, event context | researcher | Cold |
| Scientific literature | OpenAlex API + PubMed E-utilities | papers, citations, abstracts, author networks | researcher | Cold |
| Agriculture | USDA WASDE / NASS datasets | supply, demand, acreage, yield, crop balance sheets | researcher | Cold |
| Power / grid | FERC eLibrary / open data surfaces | generation, transmission, rate filings, capacity changes | researcher | Cold |

Operating rule: broad access at launch, narrow default consumption. Researcher promotes a cold source into evidence only when it materially changes confidence, falsifier status, or regime interpretation.

## 5. Signal lifecycle

The canonical progression is:

```text
source delta
  -> evidence row
  -> hypothesis update
  -> quant score / regime / expression ranking
  -> critic challenge set
  -> trade intent
  -> trader execution or block
  -> broker fill / position update
  -> reconciliation
  -> archivist postmortem
  -> pattern extraction
  -> future hypothesis calibration
```

What moves between stages:

- Evidence rows carry provenance, release timing, vintage where relevant, and signal type.
- Hypotheses carry thesis summary, confidence, horizon, score, and falsifier state.
- Trade intents carry edge scorecards, explainability fields, experiment tags, and fill realism fields.
- Orders and positions mirror broker reality and are reconciled back to shared state.
- Postmortems and patterns feed the next generation of hypotheses and quant thresholds.

## 6. Decision flow

The system is not a chat bot with trading bolted on. It is a gated state machine.

1. Researcher observes source movement and writes evidence.
2. Quant converts evidence into score, regime, and candidate expressions.
3. Critic validates that the thesis is still coherent and that objections are addressed.
4. Trader checks gates, sizing, pauses, and broker state before submission.
5. Broker events are reconciled against local state.
6. Archivist grades the outcome and updates the calibration corpus.

No stage is allowed to skip ahead by implying a later stage already happened.

## 7. Gate stack before capital risk

`trade_intent` can move to `submitted` only when all of the following are satisfied:

- hypothesis is `ready` or `active`
- critic challenges are resolved or an explicit human override exists in `audits`
- current regime permits the action
- portfolio and per-trade risk limits are still respected after the hypothetical fill
- no active pause blocks the atomic action
- options trades have an explicit catalyst window and only high-confidence theses may use shorter-dated options
- evidence freshness is within threshold for decision-critical sources
- factor overlap is acceptable after the hypothetical fill
- explainability fields are present and above threshold
- modeled fill is realistically achievable under ADV and slippage assumptions

If any gate fails, the intent is blocked with a structured reason. There is no silent fallback to discretionary behavior.

## 8. Execution and reconciliation loops

Trader is the only lane that converts intent into broker activity.

```text
Telegram command or scheduled checkpoint
  -> trader checks shared state
  -> trader checks pauses, freshness, overlap, explainability, fill realism
  -> trader submits Alpaca order if allowed
  -> Alpaca emits fill/order events
  -> trader mirrors events to orders / positions / tranches
  -> trader reconciles actual broker state vs shared state
  -> divergence creates reconciliation_run
  -> affected hypothesis can be paused for new opens until resolved
```

Important broker-side invariants:

- every Alpaca order becomes an `orders` row
- every filled action becomes position/tranche history
- early assignment or unexpected corporate action must generate a corrective intent if exposure changed
- a reconciliation mismatch blocks new opens for the affected hypothesis until cleared
- broker-wide anomalies can force `exits_trims_only`

## 9. Time loops

The system has multiple loops running at different horizons.

- Researcher: morning delta ingest, nightly refresh, event-driven ingress on filing/trial/macro shocks.
- Quant: pre-market scoring, intraday rerank, after-close recalculation, on-demand rescoring when evidence changes.
- Critic: high-priority review before intent release, plus postmortem calibration review.
- Trader: pre-market decision pass, midday confirmation/invalidation, rotation pass, close-risk pass, event-driven reconciliation.
- Archivist: daily resolution sweep and weekly pattern extraction.

These loops are coupled through shared state, not through free-form chat.

## 10. Telegram interface

Telegram is the operator control surface for Druck, not a trading logic layer.

- `/summary` shows active hypotheses, positions, regime, and P&L vs SPY.
- `/hypothesis <id>` shows the full record.
- `/intent <id>` shows the current trade-intent status and critic posture.
- `/approve <id>` and `/reject <id>` provide explicit human override paths.
- `/exit <position_id>` and `/trim <position_id> <pct>` are direct risk-management commands.
- `/regime`, `/critic <hypothesis_id>`, `/archivist <hypothesis_id>`, `/audit <period>` expose state and history.

No other trading agent is externally bound to Telegram.

## 11. Shared state and schema coupling

The runtime schema is authoritative in [sql/schema.sql](../sql/schema.sql).

Key storage relationships:

- `hypotheses` anchors the thesis.
- `hypothesis_evidence` holds every source-derived observation.
- `falsifier_signals` marks break conditions and monitors them over time.
- `expression_candidates` represents possible instruments and vehicle choices.
- `regime` stores the latest deterministic macro/risk classification snapshot.
- `trade_intents` records proposed risk actions and all gate results.
- `critic_reviews` captures unresolved vs resolved challenges.
- `orders`, `positions`, and `tranches` mirror broker reality.
- `system_pauses` enforces operational brakes.
- `reconciliation_runs` records mismatches.
- `postmortems` and `patterns` close the learning loop.
- `validation_cases` keeps the leakage-resistant calibration corpus.
- `audits` is the append-only authority ledger.

## 12. Feedback loops that matter most

The critical loops are:

1. Source -> evidence -> quant -> critic -> trader -> broker -> reconciliation. This is the live execution loop.
2. Broker fill -> position outcome -> archivist -> pattern -> quant/researcher. This is the calibration loop.
3. Falsifier change -> dormant state -> exit/trim intent -> reconciliation. This is the protection loop.
4. Regime update -> gate shift -> candidate reprioritization -> sizing change. This is the context loop.
5. Validation case -> model decision -> resolved outcome -> threshold update. This is the anti-leakage loop.

Archivist writeback contract:

- Archivist does not write free-form advice back into researcher or quant state.
- Archivist writes `postmortems` and `patterns` only, plus `audits` for the transition trail.
- `postmortems` must name the hypothesis, resolution state, what was learned, what was false, and which mechanism remained plausible.
- `patterns` must be reusable rules or anti-patterns, not narrative summaries.
- Researcher consumes `patterns` as a structured prior: it may raise or lower evidence weight, add a new falsifier, or shorten the time horizon for a similar future thesis.
- Quant consumes `patterns` as a scoring prior: it may adjust regime thresholds, confidence priors, or expression ranking penalties, but only through a traceable experiment tag.
- Neither researcher nor quant may treat a pattern as a hard rule unless it is promoted into a formal rule or threshold change and logged in `DECISION_LOG.md`.

## 13. Temporary state policy

Temporary data under `~/.openclaw/tmp` is never authoritative. It may contain:

- coding-lane run metadata
- preflight cache files
- old housekeeping artifacts

Only the subtrees wired into current scripts are operationally useful. Stale archive content in that directory can be removed when it is no longer referenced by the runtime or a current preflight path.

## 14. Non-goals

- No live-capital trading at launch.
- No hidden bypass around critic or reconciliation.
- No reliance on stale cached source snapshots for decision-critical signals.
- No secondary trading persona outside Druck / `trader`.

## 15. Authority chain

This document is implemented by:

- [01_OPERATING_AUTHORITY.md](01_OPERATING_AUTHORITY.md)
- [03_EXECUTION_STATE_MACHINE.md](03_EXECUTION_STATE_MACHINE.md)
- [04_SHARED_STATE_SCHEMA.md](04_SHARED_STATE_SCHEMA.md)
- [05_IMPLEMENTATION_POLICY.md](05_IMPLEMENTATION_POLICY.md)

If this file and the DDL disagree, the DDL is runtime truth and this document must be updated to match.