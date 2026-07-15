# 08 — The Learning Systems Map

Status: active. Written 2026-07-15 because the operator said "I am starting to not know how
everything works" — this is the one-page answer. Live counts as of that date. When a new
learning mechanism ships, ADD IT HERE (doc-lint's Sunday pass + this header are the reminder).

The design has **two speeds** (SYSTEM_ARCHITECTURE §learning): *fast/autonomous* loops move
numbers on their own; *slow/human-gated* loops change rules and code only through Aaron.
Every loop below is labeled. The unifying invariant: **a loop only learns what its grading
can see** — that's why the observability organs (#8) exist alongside the learners.

---

## The one-diagram version

```
                      MARKET / BROKER REALITY
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

- **Point-in-time feature store** (`state/features.sqlite`): 46.8M rows, 34 feature names,
  each stamped `knowable_at` so backtests can't peek. Factories run nightly in
  `learning-chain.sh`: prices/fundamentals (FMP/Alpaca), LLM news typing (`llm_features`),
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
  retrain, nightly scoring of the top-600 (`ml_scores`, 9 live scoring days so far).
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

## #3 The world model — *causal belief learning (the core)*

- **82 mechanisms** (`mechanisms` table): falsifiable causal statements ("positive EPS
  surprise → post-earnings drift") with **Beta posteriors** updated from realized trade
  outcomes (214 observations, 40 mechanisms with real evidence), half-life decay, and (new
  2026-07-15) **expectancy** — mean realized excess beside hit-rate, because a 45%-hit
  mechanism that wins +8%/loses −2% is good and hit-rate alone can't see it.
- **Linker** (`link_mechanisms.py`, D57): deterministically attaches mechanisms to
  predictions (name/feature/class tiers) so grades flow to the right beliefs. Noisy links
  self-correct toward base rate.
- Gate: posterior updates fully autonomous (the *fast* learning rate).

## #4 Predictions & calibration — *does the desk know what it knows?*

- **`predict.py`**: every hypothesis gets p_correct built from the **empirical base rate**
  (long 46.6%/short 53.4%, measured, re-measured quarterly), mechanism posteriors combined
  in log-odds (family-deduped, shift capped ±0.15 until a family has n≥30 — D63), and a
  valuation-aware return band. 233 predictions, 82 resolved.
- **`grade_outcomes.py`** (nightly): matured predictions graded on 15td SPY-relative excess;
  ±50bps dead-band = 'inconclusive' (no Brier, no observations). **Brier score** tracks
  forecast skill; first cohort ≈ coin-flip because p was honestly pinned at 0.5 pre-evidence.
- **Exam report** (`exam_report.py`, nightly): the report card telegram.

## #5 Trade-experience learning — *lessons from every position*

- **Hypothesis resolution** (76 resolved) with archivist grades
  (correct_right_reasons / correct_wrong_reasons / wrong — being right for the wrong
  reasons is tracked as its own thing).
- **Postmortems** (`write_postmortems.py`): exactly one per resolved hypothesis (10).
- **Patterns** (`extract_patterns.py`): recurring failure themes distilled from postmortems
  (2 so far: stop-whipsaw, horizon-expiry-no-confirmation).
- **Exit quality** (`exit_quality_audit.py`): every exit measured 1/3/5d later —
  "sold before a +5% move" is a number (`regret_usd_5d`), per exit lane (14 rows).
- **Episode library** (13 cases, 1 negative control): curated historical analogs;
  `retrieve_episodes` finds the closest analog for new catalysts, and the
  **episode-negative-control veto** blocks intents matching known traps (it vetoed MRVL/UAL
  on 2026-07-06).
- **Fundamental forecaster** (D61): FCF/EPS forecasts graded against reported actuals;
  first calibration crank cut FCF error 49.5%→30.8% (TTM smoothing).

## #6 Rule proposals — *the slow, human-gated learning rate*

- `rule_proposals` (7 filed, 7 applied — all operator-approved): the ONLY path by which
  parameters of the trading logic change. Drafted by `calibrate.py` from calibration
  evidence or by operator sessions; **agents never self-approve**. Examples: horizon exits,
  cash-yield attribution, deployment governor, empirical base rates, mechanism dedup,
  payoff-aware grading.
- **Adaptive sizing** (D55 governor): baseline size scales with thesis quality, throughput,
  liquidity, regime, and **calibration depth** — sizing trust is literally damped (0.4×)
  until resolved outcomes exist. The system earns its own risk budget.

## #7 The improvement kernel — *the system that improves the system*

- `AGENTIC_SYSTEM.md` + Task Manager (tm.lidisolutions.ai) sprint 5 + **Dwight (PM)** +
  ephemeral coder sessions: telemetry → ranked deficiency (`drag:<signal>`) → ONE
  well-formed TM issue/day → detached coding-lane run → branch + PR → **Aaron merges** →
  next PM pass verifies the deficiency shrank. 71 issues, 57 done.
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
  fill-lineage regression), **sim parity**, **doc-lint** (weekly docs-rot check).

## #9 Institutional memory — *what the humans + LLMs remember*

- **DECISION_LOG.md** (D1–D66): every architectural decision, why, approver. **FINDINGS.md**:
  the lab notebook — dated claims with `revalidate-by` expiry so quantitative beliefs can't
  silently rot into canon. **Market debriefs** (22): the daily what-moved-and-why narrative.
  **Knowledge graph** (14.7k nodes / 73k edges + 50k causal edges, rebuilt nightly): entity
  and causal-link substrate for research retrieval.
- Weekly: archivist retrospective (hit-rate/slippage/lessons) + Sunday audit synthesis.

---

## How to think about it (the 30-second version)

1. **#1 decides what reality the system can see.** 2. **#2–#5 learn from that reality at
machine speed** — beliefs, forecasts, lessons — but only move *numbers*. 3. **#6–#7 change
the rules and the code**, always through Aaron. 4. **#8 exists because #2–#5 can only learn
what's measured** — it grows the measurement frame from blind spots and unexplained P&L.
5. **#9 is the memory that survives context loss** — for humans, agents, and the next
Claude session alike.

Known gaps (kept honest): rotation theses grade by single leg, not spread; mechanism grades
don't control for sector beta; engagement checks are keyword-level; one regime's worth of
calibration data; episode library is small (13) and hand-curated.
