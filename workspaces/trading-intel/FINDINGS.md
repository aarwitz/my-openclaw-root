# FINDINGS — the desk's lab notebook

A running log of **interesting, non-obvious factoids about the market or our datasets**,
discovered by any agent or the operator during research, backtests, debriefs, or incidents.
Not user-facing; not a decision log (that's `DECISION_LOG.md`); not canon (claims here can be
wrong — date them, source them, and revise them). If a finding later graduates into a
calibrated mechanism or a rule change, link it.

**What belongs here:** a feature that's predictive in some eras and not others; a dataset
quirk that changes interpretation; a consensus belief the data contradicts; a structural
market behavior worth remembering. **Format:** date, one-line claim in bold, then the
evidence and caveats in 2-5 sentences. Newest first.

**Expiry convention (2026-07-04):** quantitative claims (ICs, hit rates, return spreads)
decay with the regime. Tag them with a `revalidate-by: YYYY-MM-DD` line; the weekly
`scripts/doc-lint.py` pass flags expired tags so the claim gets re-verified or revised
instead of silently rotting into false canon. Structural/one-off findings (dataset quirks,
incident postmortems) need no tag.

---

## 2026-07-08 — one colliding audit id silently cost the desk a day of learning

- **A batch-unsafe id scheme in a shared helper killed the learning chain the first time the
  desk did the right thing.** `developer/_db.py` built audit ids as
  `AUDIT-<second-resolution-ts>-<entity_id[:24]>`; every attribution row id begins with the
  same `ATTR-<same second>` prefix, so the first multi-row batch (the four 2026-07-07 stop
  exits — exactly the losers the loop exists to learn from) hit `UNIQUE audits.id` and the
  chain died at `compute_attribution`, taking the daily debrief down with it. The executor's
  copy of the same helper had already been fixed (ts+uuid) — the fix never propagated to the
  duplicated code. Lessons: (1) ids must be collision-safe under batch writes, not just under
  "one write per second" luck; (2) duplicated helpers rot independently — when one copy gets
  a correctness fix, grep for its siblings. Backfilled: 4 attribution rows + the 07-07
  debrief (`mev-d2ff861fc41a432d8417`).

## 2026-07-08 — the dashboard served an empty equity curve for a month of ranges, and no check noticed

- **The web equity chart broke at the field-name boundary between two components built the
  same day.** The KV snapshot's `equityHistory` rows carry `timestamp`; the Pages function
  parsed `ts`/`date` → every daily point dropped → 1M/YTD/1Y/All rendered "Not enough
  history" since the D52 sim cutover, and 1W collapsed to a 1.3-hour intraday sliver.
  `scripts/audit-trader-live.mjs` existed and would have failed loudly on `empty_1m` — it was
  simply never wired into the deploy path. Contract mismatches live at component seams;
  verification that isn't executed automatically is documentation, not verification.
  (Fixed: parse + merged 1W ranges; publish-trader-intel.sh now gates "ok" on the live audit
  passing post-deploy.)

## 2026-07-06 — a state machine that depends on timing luck looks healthy until the first slow fill

- **Every historical order that filled instantly hid the fact that nothing tracked fills at
  all.** `execute_intent` polled once at submit; mid-session market orders fill inside that
  poll, so the books looked right for weeks. The first morning of at-the-open auction orders
  (today) exposed it: fills sat `pending_new`, reconcile "repaired" vanished orders to
  `closed_unknown` (price lost — 14 of 29 lifetime orders), and re-created the positions as
  placeholders with fabricated hypotheses — **70% of the book had severed lineage**, meaning
  the calibration engine would have graded outcomes against theses that never existed.
- The general lesson for every gate/stage here: ask "what happens when the async thing
  completes AFTER my window?" — and never let a repair path *fabricate* lineage; a repair
  that invents a placeholder hypothesis converts missing data into wrong data, which is
  strictly worse for a learning system. Fixed via the `sync_fills` stage + lineage backfill
  (D52); first prediction cohort (69 preds, 2026-06-19) matures ~2026-07-10 with clean
  lineage — barely in time.

## 2026-07-07 (pm) — the desk declared stops it never enforced, and a mixed-semantics basis column almost made enforcement fire wrong

- **Every intent carries `stop_rule: "-8% from entry"`; nothing enforced it.** Live
  P&L at 2:41 PM ET: ORCL −22.6%, CRM −9.9%, CEG −8.2% still open while the desk
  bought new names. A written-but-unexecuted rule is worse than no rule — it
  produces false confidence in the risk story. Enforcer shipped (D53); the three
  breaches were cut at live prices the same hour.
- **`positions.cost_basis` meant different things depending on which code wrote
  the row** — total cost for some (CEG 782.34 = 3×260.78), per-share for others
  (ORCL 182.75). The enforcer's dry run flagged CRWD at −42.9% when the truth was
  +14% — one false stop-out away from selling the book's best performer. Lesson:
  a column whose semantics vary by writer is not data, it's a trap; normalize to
  one convention against an authoritative ledger (the sim book) and audit the
  repair. The dry-run-first discipline is what caught it.
- Learning-loop reality check: 166 predictions live, ZERO resolved yet (oldest
  2026-06-19; first maturations mid-July). The +5.2% / +3.7pp-alpha record so far
  is selection skill, not learning — the learning claim gets its first graded
  test this month.

## 2026-07-07 — a broker can lie to you transiently: 21 of 24 positions "vanished" and came back

- **Alpaca's positions endpoint served a partial account for a window this morning**
  — 3 of 24 positions, equity showing $79,948 vs the real $103,995 — and the desk's
  reconcile dutifully flagged a "21-position divergence" while the overseer demanded
  repairs. The positions were never gone: **cash was intact to the penny and there
  were no sell orders** — a real liquidation always leaves closing orders and
  proceeds. An hour later the endpoint healed.
- The terrifying counterfactual: `reconcile.py --repair` marks DB-only positions
  closed. Run during the glitch window, it would have CLOSED THE ENTIRE HEALTHY BOOK
  in the DB on the strength of one bad API response. Only the flag-gating (the pass
  runs without --repair) saved it.
- Fix shipped: reconcile now runs a **proceeds test** — ≥3 positions missing at the
  broker with no filled sell orders ⇒ `broker_data_suspect`, all repairs refused,
  re-poll later. Lesson for every broker-truth invariant: *truth requires
  consistency between the position claim and the cash/order ledger; a single
  endpoint is not truth.* Also the strongest argument yet for the internal paper
  engine (docs/07): our own ledger cannot glitch-liquidate itself.

## 2026-07-04 — options audition ($0 spent): net premium direction is the keeper, volume-spike was a mirage

- **The 20-name preview's headline feature evaporated at full sample** — opt_vol_z
  showed IC −0.109 on 20 names, then **0.001 on 63 names**. Small-cross-section ICs
  with 10 rebalances are noise until proven otherwise; widening the panel before
  deciding saved us from buying data for a phantom signal.
- **`opt_net_prem` (dollar-weighted call-vs-put premium direction) is the real
  candidate: pooled IC 0.059 on 63 names × 1yr** (t 0.99 — only 10 monthly obs, so
  no statistical claim yet). If it held at 4yr depth it would slot near the top of
  the feature table (realized vol = 0.071). Put/call volume ratio: 0.018, weak.
- Audition cost $0 (ThetaData free tier, 14,835 daily aggregates now banked in
  features.sqlite::options_daily and reusable). Per the pre-registered rule
  (|IC| ≥ 0.03 → buy history), the next step is Massive Options Developer
  ($79/mo, 4yr) for the full FDR backtest — OPERATOR PURCHASE DECISION pending.
- revalidate-by: 2026-10-01 (moot earlier if the 4yr FDR backtest runs first)

## 2026-07-03 (evening) — v7: analyst price targets are the biggest single-family lift yet

- **Adding three price-target features (`pt_upside`, `pt_rev_60d`, `pt_count_90d` from
  FMP's dated target actions) improved EVERY coverage-era metric at once**: IC 0.0428→
  0.0481, t 1.75→1.93, ICIR 1.12→1.24, decile L/S +12.5→+21.7%/yr net, top-decile
  long-only +1.41→+1.65%/rebalance (~+21%/yr), 2026 IC 0.060→0.072. Unlike the v6
  size-axis trap, the LONG side improved as much as the spread — this is signal, not a
  cost-model artifact. `pt_upside` alone: pooled test IC 0.0318. Brav-Lehavy target-
  revision drift is alive in this tape, and it was sitting inside an FMP subscription
  we already pay for, unwired. The promotion bar (t>3) is now within sight of the live
  track record accrual. On the record: the nightly scorer trains with 43 features from
  tonight (2026-07-03) — v7's first live ranks land in ml_scores tomorrow pre-market.
- revalidate-by: 2026-10-03 (quarterly retrain checkpoint — confirm the pt lift holds OOS)

## 2026-07-03 — the size-axis experiment (v6): a feature can fatten the backtest while making the model worse

- **Giving the ranker a liquidity/size feature (`dollar_vol_63d_log`) inflated decile
  L/S from +12.5% to +19.1%/yr net — and it's (mostly) fake.** The gain comes from
  shorting small/illiquid names (size solo IC is negative, −0.02/−0.03), a spread that a
  flat 20bps round-trip cost model understates badly in exactly those names. Meanwhile
  every consistency metric got WORSE (rank IC 0.0428→0.039, t 1.75→1.57, ICIR 1.12→1.01,
  positive months 62%→52%) and the top-decile long side — what the desk actually uses —
  was unchanged (+1.411 vs +1.414/rebalance). Verdict: reverted from the live feature
  set; the finding kills the "let the tree learn the attention×size interaction" shortcut.
  The X-tier hypothesis now needs the honest construction (per-tier z-scores), not a
  size column. Lesson for every future feature: judge on rank-IC consistency and the
  long side, never on decile L/S alone — L/S rewards cost-model blind spots.

## 2026-07-02 (evening) — v5 ranker on the full 600-name panel (X-600 + LLM-news + EDGAR + peer features complete)

- **X-attention dilution CONFIRMED, and it plateaus rather than collapses**: pooled IC
  0.056 (32 mega-caps) → 0.034 (83 names) → **0.0338 (600 names)**. The signal loses ~40%
  of its strength once you leave the mega-cap core and then stabilizes — the mid-cap tail
  adds noise but not enough to destroy it. Verdict: build attention-per-liquidity-tier
  (separate z-scores within market-cap buckets) rather than one global column; the
  mega-cap tier likely still carries ~0.05+.

- **v5 (600 names, all four new feature families live) is the best model yet on
  risk-adjusted terms but still below the promotion bar**: coverage-era IC 0.0428,
  **ICIR 1.12** (v4: 1.01), t-stat 1.75 (v4: 1.58), decile L/S +12.5%/yr net, top-decile
  long-only +1.41%/rebalance, 62% positive months. Per-year IC is monotonically improving
  (2024: 0.031 → 2025: 0.048 → 2026: 0.060) — exactly the data-breadth story. Still short
  of promotion (needs t>3) and realized vol alone (0.0713) still beats the ensemble on
  raw coverage-era IC. The widened universe traded raw spread (18.3%→12.5% L/S) for
  consistency (ICIR up, t up) — breadth diversifies away the semis-concentration luck.

## 2026-07-02 — from the first walk-forward GBM ranker evaluation (`ml_ranker.py`, 62k samples, 600 names, 2020–2026)

- **The same model is noise on 2020–2023 and institutional-grade on 2024–2026 — data
  breadth, not model quality, is the binding constraint.** Quarterly-retrained rank-GBM:
  full-period ICIR 0.49, decile L/S +6.3%/yr net; restricted to the era where the
  alt-data columns actually exist (2024+): **ICIR 0.92, L/S +18.3%/yr net, top-decile
  long-only ≈ +22%/yr net**. Corollary: a dollar spent backfilling alt-data history is
  currently worth more than any amount of hyperparameter tuning. Also humbling: the
  ensemble (era IC 0.034) does NOT yet beat the best single era feature (realized vol,
  0.072) — complexity has not yet earned its keep.

- **First live ML ranks (2026-07-02, on the record): the model's entire top-10 is
  semiconductors — and MU is #1.** gbm-rank-v1, trained on 62k samples through
  mid-May, scored 599 names: MU, TSM, AMD, INTC, ASML, QCOM, KLAC, ARM, LRCX, MRVL.
  Two things to grade in ~21 trading days: (1) MU at #1 is a direct contrarian call
  on the operator's "everyone expected up, it went down" week — the model reads the
  pullback as the top entry in the universe; (2) a one-sector top decile is exactly
  the "ten names, one bet" concentration the risk model exists to catch — if the
  ranker is promoted, its output must flow THROUGH the correlation-cluster cap,
  never around it. Bottom-5 (avoid tilt): CBRS, Q, UNP, NVO, MA.

- **The X attention signal dilutes as its universe widens** — pooled IC fell from
  0.056 (32 mega-caps) to 0.034 when the panel expanded to 83 names (2026-07-02 v4
  interim, backfill to 600 still running). Hypothesis: attention spikes are most
  informative where the crowd actually is — mega-caps and meme-adjacent names — and
  noisier in the mid-cap tail. If the 600-name run confirms, the right construction is
  attention-signal-per-liquidity-tier, not one global column.

- **Feature families that add nothing as single features can still lift the ensemble**
  — adding `peer_mom_21d` + `filing_delta` (neither cracks the top-15 solo ICs) took
  the coverage-era model from IC 0.034/ICIR 0.92 to **IC 0.042/ICIR 1.01** (v4 interim).
  Interaction value is real: economic-link momentum and filing-language change matter
  *conditionally*, which is precisely what the rule-based mechanism layer cannot see.

- **In 2024–2026 the yield curve beat almost every stock-specific feature for
  cross-sectional stock selection** (`yield_curve_10y2y` era IC 0.066, `curve_10y3m`
  0.052, 2y-rate features −0.03). Macro shape features — constant across names on any
  given day — still rank names because they interact with which *kinds* of names win
  (steepening → cyclical/value tilts pay). Cross-sectional alpha and macro timing are
  not separable in this tape.

- **X (Twitter) attention spikes were the single most predictive feature of the entire
  2020–2026 test period** (`x_mention_vol_z`, pooled Spearman IC +0.056, beating every
  technical, fundamental, and macro column). Abnormal cashtag mention volume — not
  sentiment, just *attention* — carried more forward 21-day market-relative signal than
  RSI, momentum, valuation, or margins. Caveat: X history only starts 2024-01 in our
  panel (32→64 names), so its "test period" is really 2024–2026, a narrative-driven
  tape where attention plausibly matters most. The FDR backtest (Sunday rediscovery)
  will grade it as a mechanism candidate; positive sign = crowding-momentum, which can
  invert violently at regime turns.

- **The 2023 regime break destroyed a raw-value cross-sectional model and per-date rank
  normalization fixed it.** A GBM trained on 2016–2022 raw feature values had IC −0.098
  in 2023 (catastrophic) because feature *scales* drifted (what a P/E of 30 or a 40%
  drawdown "means" changed when the AI narrative took over). Rank-transforming features
  cross-sectionally per date — "is this name cheap *relative to today's market*" — took
  2023 to −0.009 and doubled aggregate IC (0.012→0.023). Lesson: in regime shifts,
  relative position survives; absolute levels lie.

- **Our panel is feature-poor before 2024, and pooled backtest stats understate the
  model as fielded today.** Short interest, X attention, and news-breadth columns barely
  exist pre-2024, and the ranker's ICs jump exactly when they arrive (2024: 0.066,
  2025: 0.051, 2026: 0.097 vs ≈0 in 2021–2023). Any evaluation pooling across eras
  mixes "model quality" with "data availability" — evaluate on the full-coverage era
  before judging.

- **High realized volatility predicted OUTperformance in 2020–2026** (`vol_20d_annual`
  IC +0.047) — the *opposite* of the classic low-vol anomaly. In a narrative/retail-flow
  tape, vol is where the action (and the forward return) lives. Expect this sign to flip
  in a genuine risk-off regime; treat as regime-conditional, not structural.

- **Sector 63-day relative strength mean-REVERTS at the monthly horizon** (`sector_rel_63d`
  IC −0.037): names in the hottest sectors underperformed market-relative over the next
  21 trading days. Chasing 3-month sector winners at monthly rebalance frequency was a
  consistently losing trade in this period. Same story for `rsi14` (−0.020) and
  `dist_sma50` (−0.020): short-horizon overbought → underperform.

## 2026-07-02 — from the day's pipeline forensics

- **Fail-closed without a stuck-state alarm is silent paralysis: the desk approved ZERO
  trades for a week and every dashboard stayed green.** A crashing risk gate (missing
  SELECT column, 06-25→07-02) left every intent frozen in `risk_review` — which is
  exactly what fail-closed is supposed to do, and exactly why it needs its own alarm.
  The 1-month scoreboard alpha (+2.8%) was earned by POSITIONS COASTING, not decisions:
  for a week the "self-improving trading desk" was a buy-and-hold portfolio with
  elaborate paperwork. Monitor throughput (state-transition recency), never just exit
  codes. Same lesson as the cache-TTL finding, one layer up.

- **The desk was authoring alpha it could not express: 13 of 21 blocked intents in one
  day were short theses** (STZ, KMX, SHAK, FND, DHI, APP, DAL) self-blocked because the
  executor was buy-only. If a strategy layer can only act on half its signal
  distribution, measured performance reflects the *plumbing*, not the alpha. (Fixed:
  migration 0013.)

- **A 7-day API cache TTL silently became a 7-day information lag for the live desk.**
  Insider filings, analyst grades, short interest, and sector strength were all being
  served from week-old disk cache while every dashboard said "fresh" — because the
  freshness of the *pipeline run* was monitored, but not the freshness of the *data
  inside it*. Monitor data recency per source, not job exit codes. (Fixed + sweep check.)

## Earlier findings worth preserving (from §11b/11c research, 2026-06-18)

- **The jobs→rates→duration→tech-down chain is real but regime-dependent** — it failed
  a 2020–2026 FDR backtest as a standalone rule (worked in 2022, inverted in the
  2023–26 AI tape) yet works as a *conditioner*: growth/momentum/high-vol mechanisms pay
  when rates fall, value pays when rates rise. The operator's macro intuition was right
  about the mechanism and wrong about its unconditional tradability.

- **Large-cap names within the same sector are only ~0.45 return-correlated** — "it's
  all one AI bet" was empirically wrong at the single-name level; graded novelty
  discounts beat binary cluster caps.

- **Anonymizing the episode library destroyed its signal** — masking tickers/dates to
  "prevent overfitting" removed exactly the context that made analogs useful.
  Overfitting is controlled by walk-forward discipline, not by blinding the model.

- **VIX-capitulation long (vix_high + deep drawdown) survived every horizon and rigor
  control** (+3%/qtr net) — one of the few macro-conditioned entries that is robustly
  mechanical rather than judgment-dependent.
