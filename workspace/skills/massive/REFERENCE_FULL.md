---
name: massive
description: Massive market data (formerly Polygon) for aggregates, previous close, snapshots, and reference data.
metadata: {"openclaw":{"emoji":"📉","os":["linux"],"requires":{"bins":["curl","jq"]},"primaryEnv":"MASSIVE_API_KEY"}}
---

# Massive

Use Massive for market structure and price-series context: previous close, OHLC aggregates, snapshots, and reference tickers.

## Auth and key source

Source of truth: /home/aaron/.openclaw/credentials/massive-api.json

OpenClaw config should provide the key through:

- secrets.providers.massive
- skills.entries.massive.apiKey with id /api key

For shell use:

```bash
export MASSIVE_API_KEY="$(jq -r '."api key"' /home/aaron/.openclaw/credentials/massive-api.json)"
```

## Source authority

**Primary use**
- price structure
- historical daily aggregates
- ATR-style volatility framing
- volume ratio and dollar-volume inputs
- multi-day follow-through confirmation

**Secondary use**
- reference ticker checks
- previous-close baseline

**Do not use Massive as**
- the primary catalyst/news authority
- broker/account state
- the sole near-open execution confirmation layer when Alpaca is available

## Routing and preservation policy

Use Massive selectively.

Prefer other providers first for lighter work:
- Finnhub for catalyst research and simple price context
- Alpaca for live/open market checks
- FMP for analyst sentiment and target context

Reserve Massive for scoring-grade structure work:
- ATR / volatility framing
- moving averages
- 5d / 10d structure
- extension vs moving averages
- dollar volume
- sector-relative price work
- formal regime calculations

When rate limits are a risk:
1. use cache-first reads
2. fetch only missing or newly needed bars
3. prioritize SPY/sector ETFs, holdings, watchlist, then broader universe
4. stop gracefully on `429` and resume later
5. prefer stale-but-labeled cached structure over hard failure when safe

## Endpoint base

- Primary: https://api.massive.com
- Compatibility fallback (if needed): https://api.polygon.io

Use query param `apiKey=<KEY>`.

## High-value endpoints

- Previous close: `/v2/aggs/ticker/{ticker}/prev`
- Daily aggregates: `/v2/aggs/ticker/{ticker}/range/1/day/{from}/{to}`
- Snapshot endpoints when entitlements permit
- Reference tickers: `/v3/reference/tickers`

## Command patterns

Previous close for ticker:

```bash
curl -s "https://api.massive.com/v2/aggs/ticker/AAPL/prev?adjusted=true&apiKey=$MASSIVE_API_KEY" \
  | jq '{status, ticker: .ticker, close: .results[0].c, volume: .results[0].v, t: .results[0].t, error}'
```

Daily range aggregates:

```bash
curl -s "https://api.massive.com/v2/aggs/ticker/NVDA/range/1/day/2026-05-01/2026-05-08?adjusted=true&sort=asc&limit=50&apiKey=$MASSIVE_API_KEY" \
  | jq '{status, ticker: .ticker, count, first: .results[0], last: .results[-1]}'
```

Reference tickers probe:

```bash
curl -s "https://api.massive.com/v3/reference/tickers?market=stocks&active=true&limit=5&apiKey=$MASSIVE_API_KEY" \
  | jq '{status, count: (.results|length), sample: .results[0]}'
```

## Most important fields

Daily / prev aggregates:
- `o`, `h`, `l`, `c`
- `v` volume
- `t` timestamp

Derived features Druck should care about:
- 5d price change
- ATR% / realized volatility framing
- dollar volume
- distance from breakout / moving-average extension
- follow-through after earnings or catalyst day

## Output discipline

For Druck responses combining Finnhub + Massive:

1. Finnhub: catalyst/event metadata
2. Massive: price action and structure context
3. NewsAPI/web: narrative support when needed
4. End with invalidators and next-check time

Anti-drift rule: strong tape alone is not enough for `buy_ready` without a verified catalyst gate.

Do not provide order-entry instructions unless Aaron explicitly asks.

---

## Alpaca Fallback (Rate Limit Handling)

**When**: Massive returns `429 Too Many Requests` or quota exceeded error during Druck catalyst research.

**Action**: Switch to Alpaca for live snapshot + latest quote as temporary fallback.

**Why**: Alpaca has higher rate limits (100k/min) and is better for position context (actual holdings). Use only when Massive is rate-limited; Massive is still preferred for historical aggregates.

**Fallback pattern**:
```bash
# If Massive 429 occurs:
# 1. Log "Massive quota exceeded; switching to Alpaca snapshot"
# 2. Use Alpaca: GET /v2/positions (existing holdings) + GET /v2/assets/{symbol} (latest quote)
# 3. For non-held tickers, return "Alpaca fallback: use /v2/assets/{symbol} for last quote"
# 4. Continue with Finnhub for earnings, NewsAPI for catalyst, Schwab for account context
```

**Druck workflow**: If Massive fails during Sat 10:00 ET catalyst pull, Druck will note "Alpaca fallback used for [tickers]" in research log and continue. Do not halt catalyst research on Massive rate limit; pivot to Alpaca intelligently.

---

## See Also

- Alpaca details: ~/.openclaw/workspace/skills/alpaca/SKILL.md
- FMP analyst/earnings data: ~/.openclaw/workspace/skills/financialmodeling-prep-api/SKILL.md
- Finnhub earnings/news: ~/.openclaw/workspace/skills/finnhub/SKILL.md
- Druck Phase II: ~/.openclaw/workspaces/druck/PHASE_II_PLAN.md
