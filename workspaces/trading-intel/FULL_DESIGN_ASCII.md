# FULL DESIGN ASCII

> ⚠️ **SUPERSEDED (2026-06-06).** The authoritative system description is now
> [`SYSTEM_ARCHITECTURE.md`](../../SYSTEM_ARCHITECTURE.md). This diagram is
> stale: it shows five agents and conflates overseer+executor+trader, omits the
> Risk agent and the World-Model layer, and lists Alpaca under "trader" (now
> executor). Retained for history only.

Canonical top-level diagram for the OpenClaw trading system.

Source of truth: the live architecture, execution, schema, and implementation policy docs under this root.

```text
========================================================================================================================
OPENCLAW TRADING INTELLIGENCE SYSTEM
========================================================================================================================

MISSION
  Exploit slow diffusion of primary-source world changes into liquid public-market expressions
  in Alpaca paper, with thesis-first gating, broker reconciliation, and postmortem calibration.

HUMAN SURFACE
  Telegram account: druck
  Human-facing persona: Druck
  Only externally visible trading lane: trader

CANONICAL ROOT
  /home/aaron/.openclaw/workspaces/trading-intel/
  - DOC_INDEX.md
  - README.md
  - DECISION_LOG.md
  - docs/01_OPERATING_AUTHORITY.md
  - docs/02_ARCHITECTURE.md
  - docs/03_EXECUTION_STATE_MACHINE.md
  - docs/04_SHARED_STATE_SCHEMA.md
  - docs/05_IMPLEMENTATION_POLICY.md
  - sql/schema.sql

CANONICAL STATE
  ~/.openclaw/state/trading-intel.sqlite
  WAL-mode SQLite; application-layer write enforcement; one shared store for all five agents.

CANONICAL REFERENCE ARTIFACTS
  reference/regime_rules.md               -> live regime classifier thresholds (narrative)
  sql/seeds/regime_rules.json             -> regime_rules seed row (rule_version live)
  sql/seed_bootstrap.py                   -> idempotent seed loader
  reference/validation_corpus/README.md   -> validation case JSON contract + protocol
  reference/validation_corpus/seeds/      -> exemplar cases (winner / negative_control / post_cutoff + fake_date_variant)
  ../researcher/skills/reasoning_chain.md -> active 8-question researcher reasoning chain

========================================================================================================================
1) AGENT TOPOLOGY
========================================================================================================================

                                      ┌──────────────────────────────────────────────┐
                                      │              SHARED SQLITE STORE            │
                                      │   trading-intel.sqlite (authoritative)      │
                                      └───────────────┬──────────────────────────────┘
                                                      ▲
                 writes evidence / scores / reviews / orders / patterns / audits    │
                                                      │
┌──────────────────────────┐        ┌──────────────────┴──────────────────┐       ┌──────────────────────────┐
│ researcher               │        │ quant                               │       │ critic                   │
│--------------------------│        │--------------------------------------│       │--------------------------│
│ - source ingestion       │        │ - scoring                           │       │ - prospective challenge  │
│ - thesis creation        │        │ - regime classification             │       │ - hypothesis review      │
│ - falsifier tracking     │        │ - expression ranking                │       │ - intent review          │
│ - evidence updates       │        │ - sizing recommendations            │       │ - unresolved challenges  │
└───────────────┬──────────┘        └───────────────┬──────────────────────┘       └──────────────┬───────────┘
                │                                   │                                            │
                │                                   │ reviewed / scored intents                   │
                │ evidence + falsifiers             │                                            │
                │                                   ▼                                            │
                │                          ┌──────────────────────────┐                          │
                └─────────────────────────▶│ trader (Druck)           │◀──────────────────────────┘
                                           │--------------------------│
                                           │ - Telegram front door    │
                                           │ - Alpaca paper submit    │
                                           │ - order sync             │
                                           │ - position sync          │
                                           │ - reconciliation         │
                                           └──────────────┬───────────┘
                                                          │ broker events / fills / snapshots
                                                          ▼
                                           ┌──────────────────────────┐
                                           │ reconciliation + audits  │
                                           │--------------------------│
                                           │ - divergence detection   │
                                           │ - pauses                 │
                                           │ - mismatch records       │
                                           └──────────────┬───────────┘
                                                          │ resolved cases / outcomes
                                                          ▼
                                           ┌──────────────────────────┐
                                           │ archivist                │
                                           │--------------------------│
                                           │ - postmortems            │
                                           │ - pattern extraction     │
                                           │ - calibration feedback   │
                                           └──────────────┬───────────┘
                                                          │ backpropagates to research + quant
                                                          └───────────────────────────────────────▶

Internal loop summary:
  research -> quant -> critic -> trader -> reconciliation -> archivist -> research/quant

External loop summary:
  primary-source APIs -> evidence -> thesis -> intent -> Alpaca -> reconciliation -> postmortem

========================================================================================================================
2) SOURCE MAP (OFFICIAL API / SURFACE NAMES)
========================================================================================================================

  [BROKER / MARKET]
    - Alpaca Trading API (paper)
    - Alpaca Market Data API
    - Inputs: account, orders, fills, positions, bars, trades, quotes
    - Used by: trader
    - Purpose: execution, reconciliation, real fill observation, position state

  [CORPORATE DISCLOSURE]
    - SEC EDGAR
    - Inputs: 8-K, 10-Q, 10-K, 13D/G, S-1, exhibits, XBRL
    - Used by: researcher
    - Purpose: primary-source company events, filing deltas, capital actions, risk changes

  [MACRO / REVISION AWARE]
    - FRED (Federal Reserve Economic Data)
    - ALFRED (Archival FRED vintages)
    - Inputs: macro series levels, vintages, revisions, release timestamps
    - Used by: researcher, quant
    - Purpose: economic regime context, vintage-safe comparisons, revision awareness

  [ENERGY]
    - EIA API
    - Inputs: inventories, production, storage, prices, weekly deltas
    - Used by: researcher
    - Purpose: energy surprises, supply-demand inflections, regime inputs

  [TRIALS / BIOTECH]
    - ClinicalTrials.gov API
    - Inputs: trial status, phase, enrollment, endpoints, sponsor changes
    - Used by: researcher
    - Purpose: clinical catalysts, trial inflection, sponsor signal change

  [REGULATORY / SAFETY]
    - openFDA APIs
    - Inputs: adverse events, labels, recalls, enforcement signals
    - Used by: researcher
    - Purpose: drug safety shocks, product risk, label / recall catalysts

  [FISCAL / PROCUREMENT]
    - USAspending.gov API
    - Inputs: awards, obligations, agencies, counterparties, timing
    - Used by: researcher
    - Purpose: federal spend flow, demand shifts, vendor concentration

  [LABOR / INFLATION]
    - BLS Public Data API
    - Inputs: CPI, PPI, payrolls, unemployment, JOLTS
    - Used by: researcher, quant
    - Purpose: labor/inflation regime, macro nowcast, sector rotation context

  [GEOPHYSICAL]
    - USGS APIs
    - Inputs: earthquakes, seismic events, water/climate alerts where relevant
    - Used by: researcher
    - Purpose: physical shock context, commodity / industrial impact

  [IP / INNOVATION]
    - USPTO patent data / patent APIs
    - Inputs: publications, claims, assignees, citations, families
    - Used by: researcher
    - Purpose: innovation pipeline, defensibility, competitive moat signals

  [PREPRINTS]
    - arXiv API / OAI-PMH
    - Inputs: preprints, categories, authors, abstract metadata
    - Used by: researcher
    - Purpose: early scientific signal, frontier research inflection

  [GLOBAL TRADE]
    - UN Comtrade API
    - Inputs: import/export volumes, country flows, product shifts
    - Used by: researcher
    - Purpose: supply chain shifts, country / product demand structure

  [CLIMATE]
    - NOAA climate data services
    - Inputs: weather, anomalies, event context
    - Used by: researcher
    - Purpose: physical / seasonal shocks, agriculture, logistics

  [LITERATURE / KNOWLEDGE GRAPH]
    - OpenAlex API
    - PubMed E-utilities
    - Inputs: papers, citations, abstracts, author networks
    - Used by: researcher
    - Purpose: literature momentum, validation density, scientific adjacency

  [AGRICULTURE]
    - USDA WASDE
    - USDA NASS datasets
    - Inputs: supply, demand, acreage, yield, crop balance sheets
    - Used by: researcher
    - Purpose: commodity fundamentals, crop surprise, input inflation

  [POWER / GRID]
    - FERC eLibrary / open data surfaces
    - Inputs: generation, transmission, rate filings, capacity changes
    - Used by: researcher
    - Purpose: utility / grid inflection, infrastructure demand, rate pressure

  [DATA-ONLY HOT PATH]
    - Market price / volume from Alpaca Market Data API
    - Open orders and positions from Alpaca Trading API
    - Live broker state from Alpaca account endpoints / webhooks

========================================================================================================================
3) INFORMATION FLOW
========================================================================================================================

┌────────────────────────────┐
│  PRIMARY-SOURCE DELTA       │  (filing, release, trial update, macro print, broker event)
└──────────────┬─────────────┘
               │
               ▼
┌────────────────────────────┐
│  researcher                 │
│  - fetch + normalize        │
│  - write hypothesis_evidence│
│  - update falsifier_signals │
│  - create / revise thesis   │
└──────────────┬─────────────┘
               │ evidence rows + falsifier state
               ▼
┌────────────────────────────┐
│  quant                      │
│  - score thesis             │
│  - classify regime          │
│  - rank expression candidates│
│  - size recommendation      │
└──────────────┬─────────────┘
               │ scored thesis + candidate intent
               ▼
┌────────────────────────────┐
│  critic                     │
│  - challenge assumptions    │
│  - validate evidence quality│
│  - require responses        │
└──────────────┬─────────────┘
               │ reviewed intent or blocked intent
               ▼
┌────────────────────────────┐
│  trader (Druck)             │
│  - Telegram command intake  │
│  - gate evaluation          │
│  - Alpaca paper order submit│
│  - order/position/tranche sync
└──────────────┬─────────────┘
               │ order events / fills / position updates
               ▼
┌────────────────────────────┐
│  reconciliation             │
│  - compare Alpaca vs SQLite │
│  - write reconciliation_run │
│  - pause on divergence      │
└──────────────┬─────────────┘
               │ resolved outcomes + anomaly history
               ▼
┌────────────────────────────┐
│  archivist                  │
│  - postmortem               │
│  - grade outcome            │
│  - extract patterns         │
│  - feed calibration corpus  │
└──────────────┬─────────────┘
               │ calibration + patterns + thresholds
               └───────────────────────────▶ back into researcher / quant

Key invariant:
  Evidence is always written before capital risk. Capital risk is always followed by reconciliation.

========================================================================================================================
4) DECISION STACK
========================================================================================================================

  Stage 0: external signal observed
    - source delta arrives
    - provenance captured
    - vintage / release time stored if applicable

  Stage 1: evidence and thesis
    - researcher writes evidence rows
    - researcher updates hypothesis state and falsifiers
    - thesis summary remains concise and falsifiable

  Stage 2: quantification
    - quant updates hypothesis score
    - quant computes regime.current from deterministic live rules
    - quant ranks expression candidates
    - quant proposes sizes and tranche logic

  Stage 3: adversarial review
    - critic reviews hypothesis and trade_intent
    - unresolved challenges keep the intent from advancing
    - overrides, if any, must be recorded in audits

  Stage 4: trader gate evaluation
    - check hypothesis readiness
    - check critic status
    - check regime gate
    - check evidence freshness
    - check factor overlap
    - check explainability threshold
    - check fill realism / ADV cap / modeled slippage
    - check system pauses

  Stage 5: execution
    - trader submits Alpaca paper order only if all gates pass
    - trade_intent transitions to submitted
    - order row created
    - fill events mirrored

  Stage 6: reconciliation
    - compare broker state to local state on every checkpoint and every broker event
    - divergence creates reconciliation_run
    - affected hypothesis can be paused for new opens until resolved

  Stage 7: archival learning
    - archivist grades the outcome
    - write postmortem
    - extract reusable patterns
    - update calibration corpus and thresholds

========================================================================================================================
5) KEY LOOPS
========================================================================================================================

  LOOP A: SOURCE -> EVIDENCE -> THESIS
    - new filing / release / print / event
    - researcher ingests and normalizes
    - hypothesis_evidence appended
    - thesis state updated

  LOOP B: THESIS -> SCORE -> RANK
    - quant re-scores based on new evidence
    - regime snapshot refreshed
    - candidate expressions ranked
    - sizing recommendations updated

  LOOP C: THESIS -> CRITIC -> REVISION
    - critic issues challenges
    - researcher or quant responds
    - unresolved items keep intent blocked

  LOOP D: INTENT -> ORDER -> FILL -> RECONCILE
    - trader submits only after gates pass
    - Alpaca acknowledges and fills
    - position/tranche history updated
    - reconciliation_run created if broker and SQLite diverge

  LOOP E: FILL -> POSTMORTEM -> CALIBRATION
    - archivist attributes outcome
    - patterns are extracted
    - thresholds and heuristics get sharpened

  ARCHIVIST WRITEBACK CONTRACT
    - archivist writes structured postmortems and reusable patterns only
    - no free-form advice is written directly back into researcher or quant state
    - researcher consumes patterns as a prior adjustment, added falsifier, or shorter horizon
    - quant consumes patterns as a scoring prior, threshold shift, or ranking penalty
    - any promotion from pattern to hard rule requires a formal rule change and DECISION_LOG entry

  LOOP F: FALSIFIER -> DORMANT -> EXIT/TRIM
    - falsifier flips broken
    - hypothesis becomes dormant
    - trader emits exit/trim intent
    - new adds are blocked

  LOOP G: REGIME -> GATE SHIFT
    - regime.current changes
    - quant reprioritizes ideas
    - trader changes allowed actions and sizing

========================================================================================================================
6) BROKER / POSITION STATE
========================================================================================================================

  Alpaca order / position truth:
    - orders mirror Alpaca order status
    - positions mirror open exposure
    - tranches record immutable entry / exit slices
    - assignment / corporate action changes require corrective intent if exposure changed

  Reconciliation rules:
    - every order event is mirrored into local state
    - every fill becomes a tranche and position update
    - mismatch => reconciliation_run
    - mismatch blocks new opens for the affected hypothesis
    - broker-wide anomaly can trigger system_pauses.scope = exits_trims_only

========================================================================================================================
7) SCHEMA / ENTITY MAP
========================================================================================================================

  hypotheses
    - thesis summary
    - confidence
    - quant score
    - lifecycle state
    - resolution / archivist grade

  hypothesis_evidence
    - indicator, value, source, source_url
    - retrieved_at, released_at, as_of, vintage
    - signal_type for independence counting

  falsifier_signals
    - monitored failure condition
    - current_status: no_signal | monitoring | warning | broken

  expression_candidates
    - vehicle: direct_equity | etf | leaps | short_options | competitor_short | pair_trade
    - ticker / option_contract / event_date
    - conviction weighting and scoring payload

  regime
    - deterministic snapshot of market / macro condition
    - current: risk_on | neutral | caution | risk_off | crisis
    - signals_json: spy_trend, credit_spreads, vix_term_structure, yield_curve

  trade_intents
    - action: open | add | trim | exit | rotate
    - edge_scorecard_json
    - evidence_freshness_status
    - factor_overlap_status
    - provenance_completeness_pct
    - counterargument_quality_score
    - explainability_status
    - experiment_id
    - max_fillable_size
    - modeled_slippage_bps
    - modeled_fill_price

  critic_reviews
    - challenges_json
    - all_challenges_addressed

  orders
    - broker_order_id
    - status mirror
    - avg_fill_price

  positions / tranches
    - live aggregate exposure
    - immutable trade slices
    - pnl_ideal and pnl_slippage_adjusted

  system_pauses
    - scope: new_entries_only | adds_only | shorts_only | exits_trims_only | full_system

  reconciliation_runs
    - divergence records
    - resolved flag

  postmortems / patterns
    - outcome attribution
    - reusable extraction
    - calibration feedback

  validation_cases
    - anonymized / negative control / fake-date / post-cutoff corpus

  validation_cases ingestion protocol
    - researcher produces anonymized cases first
    - case classes: winner | negative_control | post_cutoff
    - fake_date_variant metadata must be recorded when date-shift testing is run
    - passed=true requires correct model decision under anonymized representation and no fake-date flip
    - rows are append-only corpus records plus batch audit trail

  regime_rules protocol
    - quant owns regime.current and must use deterministic thresholds from the active live rule set
    - regime_rules stores rule_version, effective_at, thresholds_json, notes, experiment_id
    - a regime update is only valid when the active rule configuration is recorded
    - default fail-closed behavior applies if thresholds are not yet authored

  audits
    - append-only state transition ledger
    - actor, action, before_state, after_state, rationale_concise

========================================================================================================================
8) TELEGRAM / OPERATOR PATH
========================================================================================================================

  Telegram is only for Druck -> trader.

  Commands:
    /summary       active hypotheses, positions, regime, P&L vs SPY
    /hypothesis    full thesis record
    /intent        trade-intent status and critic posture
    /approve       explicit human override path
    /reject        explicit human veto path
    /exit          risk-off command for a position
    /trim          partial risk reduction
    /regime        live regime snapshot
    /critic        critic state for a hypothesis
    /archivist     archival / postmortem status
    /audit         transition history

  Operator model:
    - Telegram is human-readable control surface
    - it is not a secondary decision engine
    - it does not bypass gates

========================================================================================================================
9) TEMPORARY STATE POLICY
========================================================================================================================

  ~/.openclaw/tmp is scratch, not authority.

  Live scratch roots currently wired into scripts:
    - tmp/coding-lane-runs         (launch metadata / task run records)
    - tmp/preflight-cache          (cached probe status)

  Removed / deprecated noise:
    - tmp/housekeeping-archive     (stale archive tree; no longer needed)

  Rule:
    - if a tmp subtree is not directly referenced by a current script or check, treat it as disposable.
    - no decision-critical trading state belongs in tmp.

========================================================================================================================
10) SYSTEM INVARIANTS
========================================================================================================================

  - One canonical root: trading-intel.
  - One canonical store: trading-intel.sqlite.
  - One human-facing lane: trader / Druck.
  - One broker scope: Alpaca paper.
  - One execution rule: no submission without critic + gates.
  - One learning loop: postmortems feed future quant / research.
  - One safety model: reconcile every event, pause on divergence.

========================================================================================================================
11) FLOW OF A SINGLE TRADE
========================================================================================================================

  1. researcher ingests a source delta and writes evidence.
  2. quant scores the hypothesis and updates regime + expression candidates.
  3. critic challenges the thesis and the proposed intent.
  4. trader evaluates gates and either blocks or submits.
  5. Alpaca fills or rejects; broker events arrive.
  6. trader mirrors the broker truth into orders, positions, tranches.
  7. reconciliation detects any mismatch and pauses if needed.
  8. archivist grades the closed outcome and extracts a pattern.
  9. the next thesis is slightly better because the calibration corpus is richer.

========================================================================================================================
END
========================================================================================================================
```
