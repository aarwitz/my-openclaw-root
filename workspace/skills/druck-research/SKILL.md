---
name: druck-research
description: Deterministic Phase II trading research router for Druck (gate, classify, score, overlay, and write-back discipline).
metadata: {"openclaw":{"emoji":"📈","os":["linux"]}}
---

# druck-research Router (Lean + Deterministic)

Authoritative strategy spec remains: `~/.openclaw/workspaces/druck/AUTONOMOUS_PM_OPERATING_MODEL.md`.

## Operation Table

| Phase | Deterministic Action | Fail-Closed Rule |
|---|---|---|
| Source ingest | Pull catalyst from Finnhub, structure from Massive, live corroboration from Alpaca, sentiment support from FMP, positions from Schwab/Sheets, macro from SPY+VIX | Missing primary source cannot be treated as neutral |
| Catalyst gate | Require at least one approved catalyst signal | If none pass -> class max `watch_only` |
| Setup label | Assign exactly one setup state | Invalid/inconsistent setup -> no `buy_ready` |
| Score | Compute base score then penalties | Hard-rule caps override score |
| Regime overlay | Apply macro regime downgrade last | Risk-off/crisis downgrades are mandatory |
| Recommendation | Map to `buy_ready` / `conditional_buy` / `watch_only` / `avoid` | Respect all forced caps |
| Write-back | Idempotent sheet write + read-back verify | On mismatch, log and refuse success claim |

## Source Hierarchy

1. Finnhub = catalyst truth
2. Massive = price-structure truth
3. Alpaca = live/open corroboration
4. FMP = secondary sentiment support only

Sentiment cannot override failed catalyst gate.

## Hard Rules

- No order placement.
- No unsupported data substitution.
- Missing critical data -> conservative class.
- Output must never contradict computed score/gates.

## Output Contract

Return structured result containing:
1. gate status and winning catalyst type
2. setup label
3. base score + penalties + final score
4. applied hard-rule caps and regime adjustments
5. final class
6. falsifier and next action

## On-Demand Deep Reference

For full constants, scoring tables, penalties, thresholds, cadence, and write protocol details:
- `workspace/skills/druck-research/REFERENCE_FULL.md`
