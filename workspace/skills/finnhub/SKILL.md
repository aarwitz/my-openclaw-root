---
name: finnhub
description: Deterministic Finnhub router for catalyst checks, earnings/news context, and lightweight quote corroboration.
metadata: {"openclaw":{"emoji":"📊","os":["linux"],"requires":{"bins":["curl","jq"]},"primaryEnv":"FINNHUB_API_KEY"}}
---

# Finnhub Router (Lean + Deterministic)

Use Finnhub for event truth and lightweight market context.
Prefer this before heavier historical providers for simple ticker/event checks.

## Operation Table

| Operation | Deterministic Endpoint | Fail-Closed Rule |
|---|---|---|
| Live quote context | `/api/v1/quote` | if missing/stale, mark price confidence low |
| Earnings truth | `/api/v1/calendar/earnings` | if no event data, do not infer catalyst |
| Company catalyst/news | `/api/v1/company-news` | if no source/date, exclude from evidence |
| Fundamentals quick check | `/api/v1/stock/metric?metric=all` | if unavailable, skip quality overlay |

## Authority Boundaries

Primary:
- catalyst and earnings verification
- company news evidence
- lightweight quote corroboration

Secondary:
- basic fundamentals context

Not primary for:
- scoring-grade multi-day structure
- broker/account state
- order execution

## Hard Rules

- Never emit secrets or raw keys.
- Always include source + date for event claims.
- Positive quote action alone cannot promote top-ranked thesis without catalyst evidence.
- If quota/auth fails, report once and stop retry loops.

## Output Contract

Return:
1. event/catalyst layer
2. quote snapshot layer
3. news evidence bullets (source/date)
4. confidence and missing-data notes
5. next verification step

## On-Demand Deep Reference

- `workspace/skills/finnhub/REFERENCE_FULL.md`
