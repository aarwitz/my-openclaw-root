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

## 2026-07-23 — market-graded challenge quality: the resolver's verdict was too kind

**Change:** built `grade_resolutions.py` — outcome-grades every challenge and resolver
decision against forward market-relative return (10td, ±2% noise band). Wired into the
learning chain; `integrity_check` now carries standing `judgment:*` lines.
**Result (42 mature challenges):** 13 false alarms / 16 VINDICATED / 13 neutral —
decisive false-alarm rate **45%**, avg post-challenge thesis-direction excess **−0.88%**.
**Lessons:** (1) The resolver's "59/66 false alarms" was LLM-judging-LLM reasoning quality;
the MARKET says decisive challenges were 55% vindicated — challenges carry mild signal even
when their reasoning is sector-noise ("right for wrong reasons" still saves money).
(2) Never grade a judgment organ with another LLM; only forward outcomes count. (3) The
resolver's 66 HOLD/CLOSEs are now a live experiment — `judgment:resolver_quality` appears
automatically once ≥10 mature (~2 weeks). If the mass-HOLDs grade badly, the resolver gets
revised toward respecting the tape more.

## 2026-07-23 — LANE CLOSED: LLM stance-picking on big movers

**The five-run conclusion:** no prompt (fade, continuation), no diet (headlines, enriched
earnings-surprise + analyst actions), and no model (Sonnet 5, Opus 4.8) beat the naive
follow-the-move rule on the walk-forward corpus. Best case the engine CONVERGES to the rule
(matching its hits while paying for tokens); every configuration's deviations from drift were
net-negative; and every confidence signal graded anti-predictive at the extremes.

**Standing decision:** stance-picking on this event class belongs to the deterministic drift
mechanisms the quant layer already owns (FDR-validated, already trading). `decompose_events`
live authoring stays at `DECOMP_MAX=0` indefinitely. Re-open ONLY with a fundamentally new
point-in-time information source (options IV, filings text, true primary sources) that first
beats both baselines here, then validates on a fresh holdout.

**Where the LLM budget concentrates instead (the measured wins):** thesis resolution
(challenged rot 79→14 in one day; 90% of critic challenges graded false alarms) and
second-order context/falsifier authoring on quant-originated theses.

## 2026-07-23 — model axis: Opus 4.8 on the identical enriched corpus

**Change:** same corpus, same enriched prompt — only the model (sonnet-5 → opus-4-8).
**Result:** engine 58% / +3.14% fwd10 vs momentum 61% / +3.63%; fwd21 56% / +6.44% vs
59% / +7.61%. Deviations: 1 paid (LITE +15.0) vs 3 cost (PL −11.6, CTMX −10.7, QCOM −7.3).
**Lesson:** a stronger reasoner does not change the conclusion — it also converges to the
rule and its deviations also lose. The constraint is informational, not cognitive.

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
