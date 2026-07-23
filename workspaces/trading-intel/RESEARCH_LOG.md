# RESEARCH_LOG — what we tried, what it did, what we learned

Append-only, newest first. Machine twin: `state/decomp-experiments.jsonl` (auto-written by
`scripts/backtest_decomposition.py` after every graded corpus run). One entry per experiment;
no version theater — the entry IS the record.

The standing bar every research-layer change must clear: **beat BOTH naive baselines
(follow-the-move, fade-the-move) on the walk-forward corpus, out-of-sample, before it may
author anything live.** Live authoring is held at `DECOMP_MAX=0` in `learning-chain.sh` until
a run clears the bar (and then validates on a fresh holdout slice — winning the practice set
isn't winning).

---

## 2026-07-23 — model axis: Opus 4.8 on the identical enriched corpus (RUNNING)

**Change:** same corpus, same enriched prompt — only the model (sonnet-5 → opus-4-8).
Question: does a stronger reasoner find deviations from drift that pay?
**Result:** pending.

## 2026-07-23 — enriched inputs: earnings surprise + analyst actions, 64 events

**Change:** kept the continuation prompt; changed the diet — 15×400-char headlines,
point-in-time earnings surprise (`fmp.earnings` + event-day-is-earnings flag), dated analyst
actions (14d). Hypothesis: input starvation.
**Result:** engine 59% / +2.58% fwd10 vs momentum 59% / +2.75%; fwd21 56% / +5.58% vs
59% / +6.95%. Deviations from the rule collapsed to 4 (2 paid, 2 cost — net wash).
**Lessons:** (1) Given the real driver data the engine CONVERGES TO the drift rule — it stops
being wrong by learning to do what the free rule does. It matches, never beats. (2) **Its own
conviction is anti-predictive at the extremes** (3 highest/lowest-conviction calls: all wrong,
≈−10% each) — the same inversion as the desk's p_correct (corr −0.17). This system's confidence
signals keep inverting; treat any LLM/mechanism conviction as unvalidated until graded.
(3) The mid-conviction slice sits +0.5pp over the rule — noise-level, post-hoc, not actionable.
(4) The 64-event baseline (headline-only diet) confirmed the 28-event verdict at scale:
55%/+1.70% — under the rule on both horizons.

## 2026-07-23 — continuation-base-rate prompt, 28 events

**Change:** prompt rebuilt around the measured base rate (big moves continue; fading requires
specific disconfirming evidence; shyness also penalized).
**Result:** hit 36% fwd10 / 46% fwd21, avg excess −2.1% / +3.1% (n=28 actionable — no_trade
collapsed 75%→0%, the pendulum overcorrected). Momentum baseline: 54%/+3.0% and 57%/+5.3%.
**Lessons:** (1) Massive improvement over the fade prompt but still LOSES to the one-line rule.
(2) The engine's *deviations* from drift were anti-predictive (PL/CTMX/HELE fades, LUNR short
into +31%) — and "momentum filtered by LLM agreement" also underperformed raw momentum: with
this diet the model's judgment subtracts value. (3) Prompt wording is hitting diminishing
returns → change inputs, not words.

## 2026-07-23 — first walk-forward run (fade-biased prompt), 28 events

**Change:** first corpus run ever. Prompt was built around the Kimi-overreaction exemplar.
**Result:** 0/7 direction-correct fwd10, avg excess −15.3%; 21/28 no_trade (shy). Momentum
baseline 57%/+8.7%.
**Lessons:** (1) The exemplar became a fade reflex — single-story overfit, the exact trap the
desk keeps warning itself about, reproduced in a prompt. (2) **Post-event drift is real in this
universe** (+8.7%/event fwd10 market-relative) — consistent with the FDR-validated
earnings-beat/momentum mechanisms the quant layer already trades. The LLM's bar is not
"sounds smart," it is "beats that rule." (3) The harness itself works: a catastrophic strategy
was caught for ~$20 of API calls instead of weeks of live losses. Live authoring was held at
DECOMP_MAX=0 the same hour.

## Corpus & method (fixed unless noted)

64 events (2 max/ticker, ≥10td apart), tracked universe, window **2026-03-01..2026-07-08**
(hard floor: starts post-model-training-cutoff so outcomes cannot be memorized — do NOT lower),
|1d move| 4–30%, price ≥$5, $5M+ dollar-volume. Stance graded at event close → +10td/+21td
excess vs SPY. LLM responses cached by (namespace, model, ticker, date, prompt-hash) — a prompt
change re-runs only what changed; an unchanged rerun is free.
