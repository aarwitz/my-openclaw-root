---
name: newsapi-ai
description: Deterministic NewsAPI.ai router for article/event retrieval and trend coverage analysis when the request is explicitly about published news.
metadata: {"openclaw":{"emoji":"📰","os":["linux"],"requires":{"bins":["curl","jq"]},"primaryEnv":"NEWSAPI_AI_KEY"}}
---

# NewsAPI.ai Router (Lean + Deterministic)

Use only for published-news requests.
If request is general web lookup, use web search tools instead.

## Operation Table

| Operation | Deterministic Endpoint | Output |
|---|---|---|
| Article retrieval | `article/getArticles` | top article set with source/date |
| Story clustering | `event/getEvents` | event-level coverage clusters |
| Trend signal | `trends/getTrendingConcepts` | ranked trend concepts |
| Topic disambiguation | `suggestConceptsFast` | canonical concept URI |

## Invocation Gate

Invoke this skill only when user asks for:
- news headlines/coverage
- story trend analysis
- source-to-source coverage comparison

Do not invoke for:
- generic browsing
- non-news product/repo research
- real-time quote/market endpoints

## Hard Rules

- Always include source attribution.
- Avoid raw JSON dumps.
- Keep result set bounded by default.
- If quota/auth fails, report clearly and stop retry loops.

## Output Contract

Return:
1. concise trend summary
2. top sources (title, outlet, date, angle)
3. co-occurring concepts
4. sentiment skew (if requested)
5. optional drill-down query path

## On-Demand Deep Reference

For auth/config details, full endpoint examples, and failure handling:
- `workspace/skills/newsapi-ai/REFERENCE_FULL.md`
