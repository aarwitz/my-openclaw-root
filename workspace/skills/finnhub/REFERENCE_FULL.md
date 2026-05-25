---
name: finnhub
description: Market data and events via Finnhub (quotes, earnings calendar, company news, fundamentals). Use for price/event context and catalyst checks, not for trade execution.
metadata: {"openclaw":{"emoji":"📊","os":["linux"],"requires":{"bins":["curl","jq"]},"primaryEnv":"FINNHUB_API_KEY"}}
---

# Finnhub

Use Finnhub when the user needs market data context: quote snapshots, earnings dates, company news, and basic financial metrics.

## Auth and key source

Source of truth: /home/aaron/.openclaw/credentials/finnhub-api.json

OpenClaw config should provide the key through:

- secrets.providers.finnhub
- skills.entries.finnhub.apiKey with id /api key

For ad hoc shell calls:

```bash
export FINNHUB_API_KEY="$(jq -r '."api key"' /home/aaron/.openclaw/credentials/finnhub-api.json)"
```

## Source authority

**Primary use**
- catalyst verification
- earnings actual vs estimate checks
- revenue actual vs estimate checks
- company-news event evidence
- earnings timing / event-window truth

**Secondary use**
- quick live price context
- basic financial quality context
- simple quote/context requests that would otherwise waste Massive quota

**Do not use Finnhub as**
- the main price-structure engine for ATR, volume-ratio, or multi-day setup geometry
- an execution venue or portfolio-state source
- a substitute for detailed analyst-sentiment breadth when FMP is available

Routing note:
- default to Finnhub before Massive for simple ticker triage, earnings checks, and event-driven research
- use Massive only if the request truly needs scoring-grade historical price structure

## Endpoints

High-value endpoints for Druck:
- Quote: GET https://finnhub.io/api/v1/quote?symbol=<TICKER>&token=<KEY>
- Earnings calendar: GET https://finnhub.io/api/v1/calendar/earnings?from=<YYYY-MM-DD>&to=<YYYY-MM-DD>&symbol=<TICKER>&token=<KEY>
- Company news: GET https://finnhub.io/api/v1/company-news?symbol=<TICKER>&from=<YYYY-MM-DD>&to=<YYYY-MM-DD>&token=<KEY>
- Basic financials: GET https://finnhub.io/api/v1/stock/metric?symbol=<TICKER>&metric=all&token=<KEY>
- Market news: GET https://finnhub.io/api/v1/news?category=<general|forex|crypto>&token=<KEY>
- Stock symbols / reference: GET https://finnhub.io/api/v1/stock/symbol?exchange=US&token=<KEY>
- Company profile: GET https://finnhub.io/api/v1/stock/profile2?symbol=<TICKER>&token=<KEY>

## Command patterns

Quote snapshot:

```bash
curl -s "https://finnhub.io/api/v1/quote?symbol=AAPL&token=$FINNHUB_API_KEY" \
  | jq '{current: .c, high: .h, low: .l, open: .o, prevClose: .pc, ts: .t}'
```

Upcoming earnings:

```bash
curl -s "https://finnhub.io/api/v1/calendar/earnings?from=2026-05-01&to=2026-05-31&symbol=NVDA&token=$FINNHUB_API_KEY" \
  | jq '.earningsCalendar[:10] | map({date, symbol, epsActual, epsEstimate, revenueActual, revenueEstimate})'
```

Recent company news:

```bash
curl -s "https://finnhub.io/api/v1/company-news?symbol=MSFT&from=2026-05-01&to=2026-05-08&token=$FINNHUB_API_KEY" \
  | jq '.[0:10] | map({datetime, headline, source, url})'
```

## Most important fields

Quote:
- `c` current price
- `pc` previous close
- `o`, `h`, `l`
- `t` timestamp

Earnings calendar:
- `date`
- `epsActual`, `epsEstimate`
- `revenueActual`, `revenueEstimate`
- `hour` when available

Company news:
- `headline`
- `source`
- `datetime`
- `url`
- summary/category when available

Basic financials / profile:
- margins, growth, valuation context
- industry/sector metadata
- market cap context

## Output discipline

For trading support responses, format as:

1. Catalyst/event layer first
2. Market snapshot second
3. News highlights (3-5 bullets with source/date)
4. What to watch next (invalidator/next check)

Anti-drift rule: a positive Finnhub price snapshot without verified catalyst evidence does not justify a top-ranked recommendation.

Do not provide order-entry instructions unless Aaron explicitly asks.
