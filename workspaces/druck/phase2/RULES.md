# Phase II Quant Calibration & Rule Definitions

Companion to [`AUTONOMOUS_PM_OPERATING_MODEL.md`](../AUTONOMOUS_PM_OPERATING_MODEL.md). This is where every
"why this number?" answer lives so calibration can be reviewed in one place.

All constants are in [`phase2/scoring.py`](scoring.py) at the top of the
file (search for `CALIBRATION`).

---

## 1. Setup-state classifier

Implemented in [`setup_classifier.py`](setup_classifier.py). Rules-only,
mutually exclusive, deterministic. Precedence (highest first):

1. `overextended_chase` — safety override
2. `post_earnings_drift` — event-anchored
3. `sell_the_news_digestion` — event-anchored, requires reclaim
4. `sympathy_momentum` — event-anchored, weakest event
5. `breakout_continuation` — structural
6. `mean_reversion_bounce` — contrarian, blocked if extended
7. `none`

Why precedence in this order:

- Safety first. `overextended_chase` is the only rule that can outright kill
  a candidate, so it must win regardless of structural signals.
- Event-anchored setups have higher prior conviction than purely structural
  ones, so they are evaluated next.
- `breakout_continuation` and `mean_reversion_bounce` rely only on price
  geometry — these are last and lower confidence.

### Rule definitions

| State | Required conditions |
|---|---|
| overextended_chase | `(close - 20dMA) / ATR > 2.0` AND no catalyst in last 5d |
| post_earnings_drift | double-beat in last 10d, close ≤ post-earnings-high AND ≥ post-earnings-high − 1·ATR, gap holding |
| sell_the_news_digestion | beat AND immediate sell-off AND reclaim of pre-event close on volume |
| sympathy_momentum | sector-sympathy gate passed upstream (named leader + peer correlation > 0.6 — caller responsibility) |
| breakout_continuation | close ≥ 20d high, vol ratio ≥ 1.5, close − 20d high ≤ 1·ATR |
| mean_reversion_bounce | RSI(14) < 30, close > prior_close + 0.5·ATR, extension ≤ 1·ATR |

`mean_reversion_bounce` proxy `prev_close + 0.5·ATR` is used in place of
"previous day high" because daily bars give us that field cleanly without
storing prior-day OHLC separately.

---

## 2. ATR + volatility-efficiency

### ATR window
**14-day Wilder ATR** ([`massive.wilder_atr`](adapters/massive.py)). Why 14:

- Industry default for swing horizons (2–10 day window matches Druck's spec).
- Long enough to absorb single-bar shocks (earnings).
- Short enough to react when a name's regime changes.

### ATR percent
`atr_pct = atr_abs / last_close`. Comparable across price levels.
Typical liquid US large-cap: 0.015–0.04. Aggressive momentum names: 0.05+.

### Realized volatility
Annualized close-to-close log-return stdev over last 5 returns
([`realized_vol`](adapters/massive.py)). Used as supporting context, not in
scoring directly — kept on the row for replay.

### Expected move framing (5d horizon)
`HIST_5D_MOVE_BY_SETUP` is the historical 5-day expected move expressed in
**ATR units** (so it's already vol-normalized). Initial values:

| Setup | Expected 5d move (ATR units) |
|---|---|
| breakout_continuation | 1.6 |
| post_earnings_drift | 1.8 |
| sell_the_news_digestion | 1.2 |
| sympathy_momentum | 1.0 |
| mean_reversion_bounce | 1.1 |
| overextended_chase | 0.4 |
| none | 0.5 |

These are starting estimates from generic momentum literature — re-fit
quarterly via [`replay.recompute_score_for`](replay.py) once you have ≥ 50
labeled outcomes per state.

### Volatility-efficiency formula
```
atr_pct_norm = atr_pct / 0.03                   # 3% ATR = 1.0
score = clip( (expected_move_atr_units / max(atr_pct_norm, 0.5)) * 10, 0..10 )
```

Behavior:

- **Tight breakout (atr_pct=2%, expected 1.6 ATR move):** norm=0.67, score = 1.6/0.67 = 2.39 → 23.9 → capped at 10.
- **Moderate momentum (atr_pct=4%, expected 1.6):** norm=1.33, score = 1.20 → 12 → capped at 10.
- **Chaotic mover (atr_pct=8%, expected 1.6):** norm=2.67, score = 0.6 → 6.0.
- **Overextended chase (atr_pct=8%, expected 0.4):** norm=2.67, score = 0.15 → 1.5.

This rewards setups where the historical follow-through is large *relative
to* daily noise, which is exactly the inefficiency we want to capture.

---

## 3. Penalty calibration

All penalties are **subtracted after** base score is computed and stored
separately. They never push the score below 0 in a hidden way; the floor is
the math itself.

### Extension penalty
Linear from 0 at 1.5 ATR above 20d MA to −15 at 2.5 ATR.

| Extension (ATR) | Penalty |
|---|---|
| ≤ 1.5 | 0 |
| 1.75 | −3.75 |
| 2.0 | −7.5 |
| 2.25 | −11.25 |
| ≥ 2.5 | −15 |

Why linear: avoids hidden interactions and is easy to inspect in a row.

### Crowding penalty
Two-stage:

| 5d move | Penalty |
|---|---|
| < 15% | 0 |
| 15%–25% (graded) | linear toward −10 |
| ≥ 25% | −10 |

Replace 5d% with 10d% once that field is on the row.

### Redundancy penalty
| Overlap | Penalty |
|---|---|
| both sector and factor match an existing position | −10 |
| sector OR factor match | −5 |
| neither | 0 |

This is gentler than "−10 only on full match" because partial overlap (same
sector, different factor) still concentrates risk during a sector drawdown.

### Hard rules (apply after penalties)

- single penalty ≤ −10 ⇒ class capped at `conditional_buy`
- total penalties ≤ −20 ⇒ capped at `watch_only`
- `liquidity_score == 0` ⇒ `watch_only`
- `setup_state == overextended_chase` ⇒ `watch_only` max
- `alpaca_live_conflict_flag` ⇒ capped at `conditional_buy`

---

## 4. Sector-sympathy logic

In `normalize._populate_sector_support`:

1. Pull the candidate's sector ETF (mapped via `_TICKER_TO_ETF`).
2. Pull 5d % moves for all 11 sector ETFs.
3. Rank the candidate's sector by 5d return.
4. `sector_score = 10 × percentile_rank`.

For the **`sympathy_momentum` setup gate** specifically (separate from
sector-support scoring), the upstream logic must verify:

- the named leader (with its own catalyst) has been identified
- the leading event is within the last 3 trading days
- a peer basket reacted in the same direction
- rolling 30d correlation between candidate and leader ≥ 0.6
- candidate move started AFTER leader event (timing proximity)

This is currently an upstream-supplied flag (`sector_sympathy_flag`).
Adding a deterministic implementation is the next quant-dev task once you
nominate canonical leader names per sector.

---

## 5. Regime overlay validation

Logic in [`regime.classify`](regime.py). Boundaries match the spec:

| Regime | Trigger |
|---|---|
| crisis | SPY 5d ≤ −5% OR VIX > 35 |
| risk_off | SPY < 50d MA OR VIX > 25 |
| caution | SPY < 20d MA AND VIX in [18, 25] |
| risk_on | SPY > 20d > 50d AND VIX < 18 |
| neutral | otherwise |

### Validation suggestion

Run [`replay.fill_outcomes`](replay.py) over the last 60 trading days and
group by regime. Acceptance criteria for the current overlay:

| Regime | Expected hit-rate behavior |
|---|---|
| risk_on | breakout/PED setups ≥ 55% winners (5d) |
| neutral | ≥ 50% winners |
| caution | breakout downgrade should reduce false buy_ready by ≥ 30% |
| risk_off | mean-reversion → watch_only should avoid ≥ 60% of bouncing-knife losers |
| crisis | zero buy_ready issued (audit) |

If the caution downgrade triggers ≥ 1 in 5 winning breakouts being skipped,
loosen to `vix > 20` instead of `>= 18`.

---

## 6. Replay framework

Two modes in [`replay.py`](replay.py):

1. **`fill_outcomes(as_of)`** — for past Candidates rows, computes 1d/5d/10d
   returns, max runup/drawdown, deterministic outcome label
   (`winner`/`loser`/`delayed_*`/`chop`). Idempotent via
   `sheets.upsert_outcome`.

2. **`recompute_score_for(record)`** — re-runs the current scoring engine
   against a frozen Candidates row. Lets you change scoring constants and
   immediately see how each historical row would have been classified now.

CLI:
```bash
python3 -m phase2.cli outcomes --as-of 2026-05-09
python3 -m phase2.cli replay --since 2026-04-01 --format text
```

Suggested replay metrics to track per quant review:

- hit-rate by `recommendation_class`
- hit-rate by `setup_state`
- score-bucket performance (50–60, 60–75, 75+)
- falsifier_resolved rate (text-resolution, manual)
- extension_penalty effectiveness (compare class drift vs realized 5d ret)
- redundancy_penalty effectiveness (drawdown contribution analysis)

---

## 7. Tuning checklist

When changing a calibration constant:

1. Edit constant in `scoring.py` (or the relevant module).
2. Run `python3 -m phase2.cli replay --since YYYY-MM-DD > before.json`
   **before** the change with the old code.
3. Apply change, re-run, save as `after.json`.
4. Diff to confirm only intended classes shifted.
5. Add a one-line note to this file with the change date and rationale.

Calibration changelog (append below):

- 2026-05-10 — initial Phase II calibration set.
