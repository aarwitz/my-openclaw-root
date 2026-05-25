---
name: alpaca
description: Deterministic Alpaca router for paper account state, positions/orders, and live quote/snapshot corroboration.
metadata: {"openclaw":{"emoji":"🦙","os":["linux"],"requires":{"bins":["curl","jq"]},"primaryEnv":"ALPACA_API_KEY"}}
---

# Alpaca Router (Lean + Deterministic)

Use Alpaca for paper account context and live market corroboration.
Default to paper endpoints unless explicitly authorized otherwise.

## Operation Table

| Operation | Deterministic Action | Fail-Closed Rule |
|---|---|---|
| Account readiness | check account + clock endpoints | if blocked/auth failed, stop |
| Position state | fetch positions/open orders | if unavailable, mark account-state unknown |
| Live corroboration | latest quote/snapshot/intraday bars for requested ticker | if stale/missing, downgrade confidence |
| Order action (paper only) | place/cancel only on explicit request | no implicit trade actions |

## Authority Boundaries

Primary:
- paper account health
- positions/orders
- near-open quote/snapshot confirmation

Secondary:
- intraday corroboration

Not authoritative for:
- catalyst truth
- broad historical feature engineering

## Hard Rules

- Never output secrets.
- No live-money assumptions; paper by default.
- Alpaca cannot create a catalyst signal; only corroborates execution context.
- On material conflict with primary structure source, annotate discrepancy and cap aggressiveness.

## Output Contract

For each ticker:
1. timestamp
2. bid/ask or last
3. intraday trigger context
4. recommendation impact (`yes/no` + reason)

## On-Demand Deep Reference

For full endpoint examples and fallback details:
- `workspace/skills/alpaca/REFERENCE_FULL.md`
- `workspace/skills/alpaca/API_REFERENCE.md`
