---
name: alpaca
description: Alpaca paper trading and market data access for account state, orders, positions, and quotes/bars. Use paper endpoints only unless explicitly authorized otherwise.
metadata: {"openclaw":{"emoji":"🦙","os":["linux"],"requires":{"bins":["curl","jq"]},"primaryEnv":"ALPACA_API_KEY"}}
---

# Alpaca

Use Alpaca for broker-aware paper trading workflows and market data support in Druck's Phase II process.

## Scope and safety

- Default to paper account only: endpoint in credentials must be `https://paper-api.alpaca.markets/v2`.
- Use Alpaca for account state, positions, open orders, and paper order placement within Aaron's approved paper-trading policy.
- For research/ranking tasks, use Alpaca as a live quote/bars corroboration layer, not as catalyst authority.
- Never output raw credentials, headers, or secrets in chat.

## Source authority

**Primary use**
- paper account health
- positions and orders
- near-open live quote/snapshot confirmation
- intraday execution context

**Secondary use**
- daily/intraday bar corroboration
- live spread sanity checks
- lighter live market checks that would otherwise hit Massive unnecessarily

**Do not use Alpaca as**
- the primary catalyst source
- the main historical price-feature engine when Massive is available
- a substitute for Schwab when real-book holdings context is needed

Routing note:
- prefer Alpaca before Massive for live/open checks, spread checks, latest quote/snapshot, and intraday trigger confirmation

## Credentials

Source of truth:
- `~/.openclaw/credentials/alpaca-api.json`

Expected keys:
- `endpoint`
- `api key`
- `secret`

Export helpers:

```bash
export ALPACA_ENDPOINT="$(jq -r '.endpoint' ~/.openclaw/credentials/alpaca-api.json)"
export ALPACA_API_KEY="$(jq -r '."api key"' ~/.openclaw/credentials/alpaca-api.json)"
export ALPACA_API_SECRET="$(jq -r '.secret' ~/.openclaw/credentials/alpaca-api.json)"
```

Auth headers:
- `APCA-API-KEY-ID: $ALPACA_API_KEY`
- `APCA-API-SECRET-KEY: $ALPACA_API_SECRET`

## Readiness probes (run first)

Account health:

```bash
curl -sS "$ALPACA_ENDPOINT/account" \
  -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
  -H "APCA-API-SECRET-KEY: $ALPACA_API_SECRET" \
| jq '{status, account_blocked, trading_blocked, buying_power, currency}'
```

Clock:

```bash
curl -sS "$ALPACA_ENDPOINT/clock" \
  -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
  -H "APCA-API-SECRET-KEY: $ALPACA_API_SECRET" \
| jq '{timestamp, is_open, next_open, next_close}'
```

## Trading API patterns (paper)

Positions:

```bash
curl -sS "$ALPACA_ENDPOINT/positions" \
  -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
  -H "APCA-API-SECRET-KEY: $ALPACA_API_SECRET" \
| jq 'map({symbol, qty, avg_entry_price, market_value, unrealized_pl})'
```

Open orders:

```bash
curl -sS "$ALPACA_ENDPOINT/orders?status=open&limit=50" \
  -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
  -H "APCA-API-SECRET-KEY: $ALPACA_API_SECRET" \
| jq 'map({id, symbol, side, type, qty, status, submitted_at})'
```

Submit paper order (ONLY if explicitly requested):

```bash
curl -sS -X POST "$ALPACA_ENDPOINT/orders" \
  -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
  -H "APCA-API-SECRET-KEY: $ALPACA_API_SECRET" \
  -H "Content-Type: application/json" \
  -d '{"symbol":"AAPL","qty":"1","side":"buy","type":"market","time_in_force":"day"}' \
| jq '{id, symbol, side, type, qty, status, filled_qty, submitted_at}'
```

Cancel order:

```bash
curl -sS -X DELETE "$ALPACA_ENDPOINT/orders/<ORDER_ID>" \
  -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
  -H "APCA-API-SECRET-KEY: $ALPACA_API_SECRET" \
| jq '.'
```

## High-value endpoints

Trading/account:
- `GET /v2/account`
- `GET /v2/clock`
- `GET /v2/positions`
- `GET /v2/orders`
- `POST /v2/orders` when authorized by current paper-trading policy

Market data:
- latest quote
- snapshot
- intraday and daily bars

## Market data patterns

Historical bars (IEX feed):

```bash
curl -sS "https://data.alpaca.markets/v2/stocks/AAPL/bars?timeframe=1Day&start=2026-05-01T00:00:00Z&end=2026-05-08T23:59:59Z&limit=5&feed=iex" \
  -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
  -H "APCA-API-SECRET-KEY: $ALPACA_API_SECRET" \
| jq '{symbol, bars: [.bars[] | {t,o,h,l,c,v}]}'
```

Latest quote:

```bash
curl -sS "https://data.alpaca.markets/v2/stocks/AAPL/quotes/latest?feed=iex" \
  -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
  -H "APCA-API-SECRET-KEY: $ALPACA_API_SECRET" \
| jq '{symbol, bid: .quote.bp, ask: .quote.ap, ts: .quote.t}'
```

Snapshot:

```bash
curl -sS "https://data.alpaca.markets/v2/stocks/AAPL/snapshot?feed=iex" \
  -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
  -H "APCA-API-SECRET-KEY: $ALPACA_API_SECRET" \
| jq '{symbol, latest_trade: .latestTrade.p, latest_quote_bid: .latestQuote.bp, latest_quote_ask: .latestQuote.ap, daily_bar_close: .dailyBar.c}'
```

## Phase II integration rules (Druck)

- Catalyst/earnings/news truth remains Finnhub + NewsAPI.
- Price structure remains Massive primary for ATR/dollar-volume features.
- Alpaca is primary for live confirmation checks near Monday open:
  - quote spread sanity
  - latest quote/trade drift vs previous close
  - intraday bar confirmation for trigger/invalidator
- If Alpaca and Massive disagree materially intraday, annotate as `data_discrepancy` and cap class at `conditional_buy`.

## Most important fields

Account / clock:
- `status`
- `buying_power`
- `trading_blocked`
- `is_open`
- `next_open`, `next_close`

Positions / orders:
- `symbol`
- `qty`
- `avg_entry_price`
- `market_value`
- `unrealized_pl`
- `status`
- `submitted_at`

Market data:
- latest bid / ask
- latest trade
- snapshot daily close
- intraday bar OHLCV

## Output contract when using Alpaca

For each ticker mention:
1. `alpaca_quote_ts`
2. `alpaca_bid` / `alpaca_ask` (or last)
3. `alpaca_intraday_context` (e.g., above trigger, below invalidator)
4. whether this changed recommendation class (`yes/no` + reason)

Anti-drift rule: Alpaca live confirmation can downgrade or block a trade, but it cannot create a catalyst that was not verified elsewhere.

## Failure handling

- 401/403: auth/entitlement issue → report and stop.
- 422: bad params/symbol → correct once, retry once.
- 429: rate limit → back off and reduce breadth.
- 5xx: one retry then mark data unavailable and fail closed.

## Full endpoint reference

See `workspace/skills/alpaca/API_REFERENCE.md` for endpoint table, required params, and response fields.