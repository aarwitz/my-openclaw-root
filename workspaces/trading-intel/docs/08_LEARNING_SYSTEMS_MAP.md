# 08 — The Learning Systems Map

Status: active. Reconciled 2026-08-02. This is the one-page answer. When a new
learning mechanism ships, ADD IT HERE (doc-lint's Sunday pass + this header are the reminder).

The design has **two speeds** (SYSTEM_ARCHITECTURE §learning): *fast/autonomous* loops move
numbers on their own; *slow/human-gated* loops change rules and code only through Aaron.
Every loop below is labeled. The unifying invariant: **a loop only learns what its grading
can see** — that's why the observability organs (#8) exist alongside the learners.

---

## The one-diagram version

```
                      MARKET / PAPER-LEDGER REALITY
                              │
   ┌──────── data intake (#1) ─────────┐        prices, filings, news, X,
   │  feature store (point-in-time)    │        Reddit, macro, valuations
   └──────┬──────────────┬─────────────┘
          │              │
   GBM ranker (#2)   world model (#3..#5)          [fast, autonomous]
   ml_scores         hypotheses → predictions →
   model book        grades → mechanism posteriors,
   earn-trust        episodes, postmortems, patterns,
   ledger            exit-quality, fundamental forecasts
          │              │
          └──────┬───────┘
                 │ evidence
        rule_proposals (#6)  ──────►  Aaron approves  ──►  parameters change
        TM / coding lane (#7) ─────►  Aaron merges PR ──►  code changes
                 ▲                                        [slow, human-gated]
                 │ deficiency signals
        observability organs (#8): regime, rotation axes, market x-ray,
        blind spots, unexplained variance, health sweep, money-path CI
                 ▲
        institutional memory (#9): DECISION_LOG, FINDINGS, debriefs, doc-lint
```

---

## #1 Data-sourcing adaptivity — *what the system is allowed to see*

- **Point-in-time feature store** (`state/features.sqlite`): each row is stamped
  `knowable_at` so backtests cannot peek. Factories run nightly in
  `learning-chain.sh`: prices (Massive), fundamentals (FMP/EDGAR), LLM news typing (`llm_features`),
  EDGAR filing deltas (Lazy-Prices MinHash), peer/economic-link momentum, X cashtag
  attention (`x_features`), Reddit retail attention (ApeWisdom `social_collect`, 25k rows),
  fundamental forecasts.
- **Audition protocol** (`DATA_SOURCES.md`): new source → free/cheap audition → pre-registered
  IC bar → paid confirm → full FDR backtest → GEN_FEATURES or kill. Lifecycle proof: options
  flow (audition passed at 1yr/63 names, KILLED at 4yr/592 — $79 total spend).
- **`data-scout-monthly` cron**: proposes new sources/prices monthly, appends to the catalog.
- Gate: auditions autonomous; *spending money* and *adding a feature family to the live
  ranker* are operator decisions.

## #2 The ML ranker + its live trial — *cross-sectional pattern learning*

- **Walk-forward GBM** (`ml_ranker.py`): 43 features, per-date rank normalization, quarterly
  retrain, nightly scoring of the current top-600 (`ml_scores`). Its historical
  metrics use today's active top-cap universe and are development diagnostics,
  not survivorship-safe edge proof. Live ranks are research-advisory and feed a
  separate monthly internal shadow model book; they never authorize desk risk.
  Promotion bar to influence sizing: **t > 3** on live track record — not yet met, so it is
  QUARANTINED: advisory only.
- **Model book** (`sim_broker.py`, D51.2): pre-registered live portfolio (long-only top
  decile, equal weight, monthly rebalance, spread costs) — the overfit-proof line. Already
  taught us the top decile is substantially a semiconductor sector bet.
- **Earn-trust ledger** (`track_ml_evidence.py`): every hypothesis records whether the model
  agreed; resolved outcomes accumulate the model's real-world hit evidence.
- **Discovery channel**: catalyst briefs flag MODEL_TOP/BOTTOM_DECILE names; `signals_to_
  hypotheses` mints hypotheses from high-p model candidates — model informs research, never
  trades directly.

## #3 The world model — *predictive-belief learning (the core)*

- **Mechanisms** (`mechanisms` table): falsifiable predictive statements
  ("positive EPS surprise → post-earnings drift") with Beta posteriors and
  expectancy. All 100 retained mechanism rows are currently deprecated after
  the robust replay found zero FDR survivors; their observations remain as
  audit/learning history, not trade permission.
- **Linker** (`link_mechanisms.py`): deterministically attaches mechanisms to
  predictions (name/feature/class tiers). A prediction owns exactly one total
  unit of learning credit, split across its unique linked mechanisms, so a
  forecast cannot multiply itself into six independent observations.
- Gate: posterior updates fully autonomous (the *fast* learning rate).
- **Discovery inference is cluster-aware** (`mechanism_backtest.py`): same-date
  ticker returns become one equal-weight portfolio observation; Newey-West/HAC
  inference operates across entry dates; promotion eligibility requires at
  least 30 entry-date clusters and 20 distinct names under one FDR umbrella.
  Training outcomes that cross a test boundary are purged and bounded test
  outcomes must mature before the exclusive fold end. The 2020-06-18 holdout
  is labelled reused/development-only.
- **Historical time-machine lane** (`historical_walkforward.py`): four
  preregistered, non-overlapping 2018–2025 folds recompute thresholds using
  training data only and apply a second cross-fold Bonferroni stability gate.
  The report is offline, resumable, and has `promotion_authority=none`; it can
  reject fragile ideas quickly but cannot certify production edge because the
  system's feature families have already been informed by those years. The locked
  2026-08-03 through 2026-10-30 forward shadow window may not be tuned against
  and must complete before production-edge claims.

## #4 Predictions & calibration — *does the desk know what it knows?*

- **`predict.py`**: every hypothesis gets p_correct built from an empirical base rate,
  mechanism posteriors combined
  in log-odds (family-deduped, shift capped ±0.15 until a family has n≥30 — D63), and a
  valuation-aware return band. Direction and the exact predictor/world-model
  source fingerprint are frozen on the prediction row; later thesis edits
  cannot change old grading semantics.
- **`resolve_prediction_backlog.py` is the resolution owner**:
  each prediction is graded from its own `predicted_at` and horizon against
  SPY-relative excess. Missing data stays unresolved/data-blocked and pages
  health; it is never silently called inconclusive. `grade_outcomes.py` is only
  a compatibility wrapper. ±50bps dead-band = inconclusive (no Brier/learning
  observation). Forecast resolution does not mutate hypothesis lifecycle.
- **`calibrate.py` is calibration-only**: it consumes resolved forecasts and
  recomputes mechanism posteriors; it does not resolve predictions.
- **Exam report** (`exam_report.py`, nightly): the report card telegram.
- **Offline probability replay** (`prediction_replay.py`, every preflight):
  read-only fixed-variant Brier/log-loss and direction-adjusted-return checks on
  frozen rows, also exposed as `predictionReplay` in the runtime GUI snapshot.
  It detects regressions before Telegram but has no promotion authority; only
  the preregistered forward challenger can earn trust.

## #5 Trade-experience learning — *lessons from every position*

- **Hypothesis resolution** with archivist grades
  (correct_right_reasons / correct_wrong_reasons / wrong — being right for the wrong
  reasons is tracked as its own thing).
- **Postmortems** (`write_postmortems.py`): exactly one per resolved hypothesis.
- **Patterns** (`extract_patterns.py`): recurring failure themes distilled from postmortems.
- **Exit quality** (`exit_quality_audit.py`): every exit measured 1/3/5d later —
  "sold before a +5% move" is a number (`regret_usd_5d`), per exit lane.
- **Episode library:** curated historical analogs;
  `retrieve_episodes` finds the closest analog for new catalysts, and the
  **episode-negative-control veto** blocks intents matching known traps (it vetoed MRVL/UAL
  on 2026-07-06).
- **Fundamental forecaster** (D61): FCF/EPS forecasts graded against reported actuals;
  first calibration crank cut FCF error 49.5%→30.8% (TTM smoothing).

## #6 Rule proposals — *the slow, human-gated learning rate*

- `rule_proposals`: the ONLY path by which
  parameters of the trading logic change. Drafted by `calibrate.py` from calibration
  evidence or by operator sessions; **agents never self-approve**. Examples: horizon exits,
  cash-yield attribution, deployment governor, empirical base rates, mechanism dedup,
  payoff-aware grading.
- **Sizing has no baseline escape hatch**: an open/add intent requires a
  pre-intent forecast with `p_correct >= .52`, positive P50, and positive
  fractional-Kelly sizing. Missing/weak forecasts produce no intent.

## #7 The improvement kernel — *the system that improves the system*

- `AGENTIC_SYSTEM.md` + Task Manager (tm.lidisolutions.ai) sprint 5 + **Dwight (PM)** +
  ephemeral coder sessions: telemetry → ranked deficiency (`drag:<signal>`) → ONE
  well-formed TM issue/day → detached coding-lane run → branch + PR → **Aaron merges** →
  next PM pass verifies the deficiency shrank.
- Deficiency sources now include the blind-spot table (#8) via the Sunday audit.

## #8 Observability organs — *widening what the loops can learn (the "second loop")*

- **Regime classifier** (deterministic thresholds, per pass): risk_on…crisis.
- **Rotation monitor** (D64, per pass): basket-axis correlation/spread/seesaw flags
  (hw↔sw, cyclicals↔defensives); morning research must state which side of an active
  seesaw a thesis sits on.
- **Market x-ray** (D65, nightly): six-dimension tape decomposition (breadth, dispersion,
  pairwise corr, momentum/sentiment factor spreads, vol); |z|≥2 phenomena nobody engaged =
  **blind spots** (4 found retroactively), reviewed every Sunday → TM issues.
- **Unexplained-variance gauge** (D66, nightly): rolling share of the desk's own P&L its
  ontology can't explain (first read 92% — ceiling, mostly idiosyncratic by construction;
  the signal is the trend and spike days).
- **Health sweep** (3×/day + pages), **money-path CI** (22 nightly checks incl. the
  fill-lineage regression), **internal-ledger integrity**, **doc-lint** (weekly docs-rot check).

## #9 Institutional memory — *what the humans + LLMs remember*

- **DECISION_LOG.md** (through D115): every architectural decision, why, approver. **FINDINGS.md**:
  the lab notebook — dated claims with `revalidate-by` expiry so quantitative beliefs can't
  silently rot into canon. **Market debriefs:** the daily what-moved-and-why narrative.
  **Evidence graph** (historical table name `causal_edges`, rebuilt nightly):
  predictive/correlational links are `association_validated`; event,
  attribution, and co-mention links are `hypothesis`. Correlation and backtests
  are never narrated as causal identification, and corroboration counts unique
  evidence rather than rebuilds.
- The graphs are not the thesis/prediction memory. Thesis, forecast,
  selection-outcome, and fill lineage remains relational in the live database;
  episodes remain a separate FTS library. No current graph node type represents
  those records directly.
- Weekly: archivist retrospective (hit-rate/slippage/lessons) + Sunday audit synthesis.

---

## How to think about it (the 30-second version)

1. **#1 decides what reality the system can see.** 2. **#2–#5 learn from that reality at
machine speed** — beliefs, forecasts, lessons — but only move *numbers*. 3. **#6–#7 change
the rules and the code**, always through Aaron. 4. **#8 exists because #2–#5 can only learn
what's measured** — it grows the measurement frame from blind spots and unexplained P&L.
5. **#9 is the memory that survives context loss** — for humans, agents, and the next
Claude session alike.

Known gaps (kept honest): rotation theses grade by single leg, not spread;
mechanism grades do not yet neutralize sector/factor beta; the reused historical
holdout is contaminated by development decisions; the locked forward shadow
window has not completed; simulator realism omits queues/halts/name-specific
borrow/dividends/options; engagement checks are keyword-level; the episode
library is small and hand-curated.
