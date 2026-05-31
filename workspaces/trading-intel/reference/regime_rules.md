# Regime Rules

Status: active reference. Loaded into `regime_rules` as `rule_version = "live"`.
Effective: 2026-05-29.
Owner: `quant`.
Authority: this page is the concrete numeric realization of the regime model declared in
`docs/03_EXECUTION_STATE_MACHINE.md` §9.1 and `docs/04_SHARED_STATE_SCHEMA.md` `regime` /
`regime_rules`.

## 1. Inputs

All four signals are computed deterministically from public sources. No model-derived inputs.

| Signal | Source | Computation |
| --- | --- | --- |
| `spy_trend` | Alpaca Market Data API (daily bars, `SPY`) | Close vs 200-day simple moving average; also 50-day vs 200-day cross. |
| `credit_spreads` | FRED series `BAMLH0A0HYM2` (ICE BofA US High Yield OAS), daily | Latest value plus 20-day delta. |
| `vix_term_structure` | Alpaca Market Data (`VIX`, `VIX3M`) or CBOE published values | Ratio `VIX / VIX3M` (contango when `< 1.0`, backwardation when `> 1.0`). |
| `yield_curve` | FRED series `T10Y2Y` (10y - 2y Treasury), daily | Latest level in basis points, plus 60-day slope of the series. |

Each signal is computed at the close of the prior US trading day. The `signals_json` payload
recorded in `regime` MUST contain numeric values for all four signals or the classifier
fails closed (see §4).

## 2. Per-signal state mapping

Each signal is reduced to one of `risk_on | neutral | caution | risk_off | crisis`.

### 2.1 `spy_trend`

| Condition | State |
| --- | --- |
| Close > 200d SMA by ≥ 3% AND 50d > 200d (golden cross posture) | `risk_on` |
| Close > 200d SMA by 0–3% | `neutral` |
| Close ≤ 200d SMA AND > -5% below it | `caution` |
| Close 5–10% below 200d SMA | `risk_off` |
| Close > 10% below 200d SMA OR 50d < 200d AND falling for ≥ 20 sessions | `crisis` |

### 2.2 `credit_spreads` (BAMLH0A0HYM2, in basis points)

| Condition | State |
| --- | --- |
| ≤ 350 bps AND 20-day delta ≤ +25 bps | `risk_on` |
| 350–450 bps AND 20-day delta ≤ +50 bps | `neutral` |
| 450–600 bps OR 20-day delta > +75 bps | `caution` |
| 600–900 bps | `risk_off` |
| > 900 bps OR 20-day delta > +200 bps | `crisis` |

### 2.3 `vix_term_structure` (ratio `VIX / VIX3M`)

| Condition | State |
| --- | --- |
| ≤ 0.90 (deep contango) | `risk_on` |
| 0.90–0.95 | `neutral` |
| 0.95–1.05 | `caution` |
| 1.05–1.20 (backwardation) | `risk_off` |
| > 1.20 OR VIX spot > 40 | `crisis` |

### 2.4 `yield_curve` (T10Y2Y, basis points)

| Condition | State |
| --- | --- |
| ≥ +50 bps AND 60-day slope ≥ 0 | `risk_on` |
| 0 to +50 bps | `neutral` |
| -25 to 0 bps (mild inversion) | `caution` |
| -100 to -25 bps (deep inversion) | `risk_off` |
| < -100 bps OR re-steepening from deep inversion within 60 days (recession trigger) | `crisis` |

## 3. Aggregation to `regime.current`

The aggregate uses the **worst-of with credit override** rule, deterministically:

1. Map each of the four signal states to a numeric severity:
   `risk_on=0`, `neutral=1`, `caution=2`, `risk_off=3`, `crisis=4`.
2. Compute `worst = max(severity[s])` across the four signals.
3. If `credit_spreads` severity is `crisis`, the aggregate is `crisis` regardless of other signals
   (credit-led shocks dominate).
4. If three or more signals are at severity ≥ 2 (`caution` or worse), bump the aggregate severity
   up by 1 (capped at `crisis`).
5. Map the final severity back to the enum.

This rule is what `regime_rules.thresholds_json.aggregation` encodes; quant must implement it
exactly. Any change requires a `DECISION_LOG.md` entry and updated `experiment_id` tags in
affected rows.

## 4. Fail-closed behavior

If any of the following is true, quant MUST write `regime.current = 'caution'` with
`signals_json.fail_closed = true` and a structured `notes` field:

- Any of the four signals is missing or stale (> 36 hours old for daily series).
- FRED, Alpaca, or CBOE source returned an error and no fallback snapshot is available.
- Active `regime_rules` row is missing or malformed.

The system never invents an implicit `risk_on` or `neutral` from partial inputs.

## 5. Gate coupling

Trader gate consequences as defined in `docs/01_OPERATING_AUTHORITY.md` and
`docs/03_EXECUTION_STATE_MACHINE.md`:

| Aggregate | New `open` allowed | `add` allowed | Shorts allowed | Max single-position sizing |
| --- | --- | --- | --- | --- |
| `risk_on` | Yes | Yes (all tranches) | Yes | 5–15% |
| `neutral` | Yes | Yes (≤ mid tranche) | Yes | 5–10% |
| `caution` | Yes (smaller) | No | Yes | 3–7% |
| `risk_off` | No (defensive only) | No | Yes | 2–5% |
| `crisis` | No | No | Yes (exits/hedges) | 0–3% |

`exits` and `trims` are always allowed regardless of regime.

## 6. Seed row

The canonical row is shipped at `sql/seeds/regime_rules.json` and loaded into
`regime_rules` on schema bootstrap. The seed `thresholds_json` exactly mirrors the tables above.

## 7. Change procedure

- Update this file and `sql/seeds/regime_rules.json` together.
- Append a `DECISION_LOG.md` entry with rationale and refreshed `experiment_id` tags.
- Upsert the active row in `regime_rules` with `rule_version = "live"` and updated `effective_at`.
- Quant emits new `regime` rows using the updated thresholds after the decision is approved.
