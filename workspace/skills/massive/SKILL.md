---
name: massive
description: Deterministic Massive router for historical price structure, aggregates, volatility framing, and regime math.
metadata: {"openclaw":{"emoji":"📉","os":["linux"],"requires":{"bins":["curl","jq"]},"primaryEnv":"MASSIVE_API_KEY"}}
---

# Massive Router (Lean + Deterministic)

Use Massive for scoring-grade price structure and regime calculations.
Prefer lighter providers for simple checks when appropriate.

## Operation Table

| Operation | Deterministic Endpoint Pattern | Fail-Closed Rule |
|---|---|---|
| Previous close baseline | `/v2/aggs/ticker/{ticker}/prev` | if missing, mark baseline unavailable |
| Daily structure window | `/v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}` | if partial, label stale/incomplete |
| Reference checks | `/v3/reference/tickers` | if unavailable, do not infer listing state |
| Regime inputs | SPY + VIX aggregate pulls | if missing, apply conservative regime |

## Authority Boundaries

Primary:
- historical OHLCV structure
- ATR/volatility framing
- dollar-volume and extension metrics

Not primary for:
- catalyst/news truth
- broker/account state
- sole near-open execution confirmation

## Preservation Policy

- cache-first where safe
- incremental fetches over long-window refetch
- prioritize core universe before expansion
- stop gracefully on 429 and resume later

## Output Contract

Return:
1. structural metrics used
2. data freshness/coverage note
3. any discrepancy/fallback note
4. implication for final class

## On-Demand Deep Reference

For full command patterns, auth, and fallback specifics:
- `workspace/skills/massive/REFERENCE_FULL.md`
