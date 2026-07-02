# Alpha Engine Roadmap — architecture assessment & evolution plan

Status: ACTIVE ROADMAP (2026-07-02). Authored in response to the operator question:
"is our dataset aggregation / knowledge graph / mechanism design optimal and
state-of-the-art?" Canonical architecture remains `SYSTEM_ARCHITECTURE.md`; this
doc is the forward plan for the *alpha engine* specifically.

## Verdict: evolve, don't restart

The expensive, unusual, CORRECT parts of this system are the parts most
projects never build:

- **Point-in-time, survivorship-safe feature store** (`features.sqlite`, 1,510
  names ∪ delisted, 20yr, `knowable_at` discipline). This is the moat. Keep.
- **The validation harness** (walk-forward, no-look-ahead proven, non-overlapping
  samples, cost-net grading, BH-FDR across the hypothesis grid). Most funds
  don't hold themselves to this. Keep — everything new must pass through it.
- **Determinism-first split** (scripts produce numbers, LLMs produce judgment)
  and the **non-bypassable risk gate**. Keep.
- **Calibration loop** (Brier-graded predictions → Beta posteriors, two learning
  rates, human-gated structural change). Keep.

What is NOT state-of-the-art is the **model class sitting on top of the data**,
and how little of the LLM's actual intelligence reaches the numbers.

## The three findings

### 1. Mechanisms are the explanation layer, not the alpha ceiling
Calibrated mechanisms are 1–2 feature threshold rules. The empirical
asset-pricing literature (Gu/Kelly/Xiu 2020 onward) is unambiguous: tree
ensembles and shallow NNs over the full feature matrix dominate rule sets,
because cross-sectional signal lives mostly in feature INTERACTIONS (cheap AND
oversold AND upgraded ≠ the sum of its parts). We already own the feature
matrix; the rules use it a slice at a time.

**Change:** add a cross-sectional GBM ranker (`ml_ranker.py`) trained
walk-forward on the same point-in-time panel, predicting H-day market-relative
return rank. Mechanisms stay — as the interpretable explanation layer (the
"why", via SHAP/feature attribution + the mechanism that agrees), as regime
conditioners, and as an ensemble member. The ranker becomes the primary
cross-sectional score consumed by `signal_scan`/`signals_to_hypotheses` once it
clears the same OOS + cost bar (evaluation first, integration is a separate
human-gated step — invariant 4 applies).

### 2. The LLM is underused as a FEATURE FACTORY (the biggest untapped edge)
Today the LLM layer writes hypotheses/prose and debates. Meanwhile the news
featurizer is a *deterministic lexicon* — decades behind what a frontier model
reading the same text produces. The unlock is NOT "let the LLM pick stocks";
it is **LLM-scored structured features, stored point-in-time, validated like
any other feature**:

- **Earnings-call reads** (FMP transcripts): guidance delta vs prior, tone,
  hedging density, analyst-pushback score — scored on a fixed rubric → rows in
  `features` (`source='llm'`, `knowable_at` = call date).
- **Filing deltas** (EDGAR): 10-K/10-Q risk-factor section CHANGES, new-risk
  emergence, language shifts.
- **News event typing**: catalyst class, directionality, credibility, novelty
  (vs the lexicon's bag-of-words sentiment).
- Each is just another column in the panel: the ranker + FDR harness decide if
  it carries alpha. Determinism note: LLM scoring is cached per (document,
  rubric-version) so reads are reproducible; the *numbers* downstream still
  come from the deterministic model.

### 3. KG and episodes: honest but thin — make them load-bearing
The KG (symbolic: sector/correlation/mechanism-cluster/regime edges) already
feeds the novelty discount and why-engine. Good. What it lacks: **economic
linkage edges** (supplier/customer, competitor, thematic exposure) that enable
second-order propagation — "TSMC guidance cut → who re-rates?". Add
supply-chain/peer edges (FMP peers + LLM-extracted relationships from filings,
cached), then a propagation featurizer: event on X → linked-name feature.
Episodes (13, hand-fed) should auto-grow: every graded prediction + debrief
drafts an episode candidate (LLM writes lesson/trap, human curates via the
weekly audit).

## Phased plan (each phase gated by measured OOS lift)

1. **P1 — GBM ranker evaluation** (offline) — **RUN 2026-07-02, verdict: promising,
   below the promotion bar, iterate before P2.** 62,053 samples, 600 names,
   walk-forward 2020–2026, cost-net, embargoed. v1 (raw values): IC 0.012,
   t=0.57 — broken by the 2023 regime shift (IC −0.098). v2 (per-date rank
   normalization, GKX-standard): **IC 0.023, t=1.21, ICIR 0.48, decile L/S
   +4.0%/yr net, top-decile long-only ≈ +8%/yr net, 2023 fixed (−0.009)**.
   Key insight: 2024–2026 ICs are 0.05–0.10 — the panel is feature-POOR before
   2024 (X/short-interest/news columns barely exist), so pooled stats understate
   the model as fielded today.
   **v3 (quarterly retrain + coverage-era split):** full-period decile L/S
   improved to +6.3%/yr net; on the **full-coverage era (2024–2026, 29
   rebalances): IC 0.034, ICIR 0.92, decile L/S +18.3%/yr net, top-decile
   long-only ≈ +22%/yr net alpha** — institutional-grade where the alt-data
   columns exist. t=1.43 is sample-limited (29 monthly obs), so the promotion
   path is BREADTH, not tuning: (a) backfill X counts to all 600 names
   (currently 32–64), (b) add LLM news features (P3) as columns, then
   re-evaluate. Note transcripts (FMP) are a paywalled tier — P3 v1 targets
   news event-typing + EDGAR filings instead. Promotion bar: coverage-era IC
   t-stat > 3 + the ensemble must beat the best single feature on the era
   (currently 0.034 vs vol's 0.072 — not yet; complexity must earn its keep).
   Results: `state/ml_ranker_eval.json`.
2. **P2 — Ranker → live (human-gated)**: nightly score after `refresh-live`;
   `signal_scan` consumes rank as the primary conviction input; mechanisms
   annotate the why. Rule proposal + operator approval before any sizing change.
3. **P3 — LLM feature factory v1**: earnings-call rubric on the top-150 names
   (backfill 2–3yr from FMP transcripts, then per-event). Validate columns in
   the same harness; promote what survives.
4. **P4 — KG economic edges + propagation features**; auto-grown episode
   pipeline.
5. **P5 — Ensemble meta-calibration**: isotonic calibration of ranker score +
   mechanism posterior + LLM catalyst judgment → one calibrated p that feeds
   Kelly (replaces hand-tuned log-odds blending).

## What we are NOT doing (and why)

- **Not restarting.** The store + harness + governance are the moat; a rewrite
  rebuilds three months of correctness for zero alpha.
- **Not deep sequence models / end-to-end RL.** Data volume (~10⁵ samples at
  monthly cadence) rewards trees; deep nets need orders more and add opacity
  the audit chain can't carry. Revisit at daily-cadence panels.
- **Not letting the LLM emit position sizes.** Invariant 1 stands. The LLM
  reads text and proposes; deterministic models score; the risk gate disposes.
