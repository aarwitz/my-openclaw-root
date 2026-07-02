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

---

## 2026-07-02 — from the first walk-forward GBM ranker evaluation (`ml_ranker.py`, 62k samples, 600 names, 2020–2026)

- **The same model is noise on 2020–2023 and institutional-grade on 2024–2026 — data
  breadth, not model quality, is the binding constraint.** Quarterly-retrained rank-GBM:
  full-period ICIR 0.49, decile L/S +6.3%/yr net; restricted to the era where the
  alt-data columns actually exist (2024+): **ICIR 0.92, L/S +18.3%/yr net, top-decile
  long-only ≈ +22%/yr net**. Corollary: a dollar spent backfilling alt-data history is
  currently worth more than any amount of hyperparameter tuning. Also humbling: the
  ensemble (era IC 0.034) does NOT yet beat the best single era feature (realized vol,
  0.072) — complexity has not yet earned its keep.

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
