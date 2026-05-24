# Alpaca API Reference for Druck

This is the quick operational reference for the Alpaca skill in OpenClaw.

## Base URLs

- Trading (paper): `https://paper-api.alpaca.markets/v2`
- Market data: `https://data.alpaca.markets/v2`

## Auth headers (all requests)

- `APCA-API-KEY-ID: <api key>`
- `APCA-API-SECRET-KEY: <secret>`

Credentials file:
- `~/.openclaw/credentials/alpaca-api.json`

## Trading endpoints

| Endpoint | Method | Purpose | Key fields used by Druck |
|---|---|---|---|
| `/account` | GET | Account health/readiness | `status`, `trading_blocked`, `account_blocked`, `buying_power` |
| `/clock` | GET | Market open/close timing | `is_open`, `next_open`, `next_close` |
| `/positions` | GET | Live held positions | `symbol`, `qty`, `avg_entry_price`, `market_value`, `unrealized_pl` |
| `/orders?status=open&limit=50` | GET | Open-order visibility | `id`, `symbol`, `side`, `type`, `qty`, `status`, `submitted_at` |
| `/orders` | POST | Submit paper order (explicit approval only) | `symbol`, `qty`, `side`, `type`, `time_in_force` |
| `/orders/{order_id}` | DELETE | Cancel one order | confirmation payload |
| `/orders` | DELETE | Cancel all orders | array of cancel statuses |
| `/assets/{symbol}` | GET | Tradability checks | `tradable`, `shortable`, `easy_to_borrow`, `status` |

## Market data endpoints

| Endpoint | Method | Purpose | Key params | Key fields |
|---|---|---|---|---|
| `/stocks/{symbol}/quotes/latest` | GET | Latest quote | `feed=iex` | `quote.bp`, `quote.ap`, `quote.t` |
| `/stocks/{symbol}/trades/latest` | GET | Latest trade | `feed=iex` | `trade.p`, `trade.s`, `trade.t` |
| `/stocks/{symbol}/snapshot` | GET | Combined state | `feed=iex` | `latestTrade`, `latestQuote`, `dailyBar`, `prevDailyBar` |
| `/stocks/{symbol}/bars` | GET | OHLCV bars | `timeframe`, `start`, `end`, `limit`, `feed` | `bars[].{t,o,h,l,c,v}` |
| `/stocks/bars` | GET | Multi-symbol bars | `symbols`, `timeframe`, `start`, `end`, `limit`, `feed` | map keyed by symbol |

## Param defaults for Druck

- `feed=iex`
- `timeframe=1Min` for intraday checks near open
- `timeframe=1Day` for short historical context
- For bars: always pass explicit `start` and `end` in UTC ISO format

## Validation checklist before using Alpaca outputs

1. `/account` returns `status=ACTIVE` and not blocked.
2. `/clock` returns valid market times.
3. Requested symbol is tradable (`/assets/{symbol}`) when order context is discussed.
4. Market data response has non-null quote/bar payloads.
5. Timestamp freshness is acceptable for the decision window.

## Error semantics

- `401`/`403`: bad credentials or entitlement issue
- `404`: bad path/symbol
- `422`: invalid query payload or unsupported parameter combo
- `429`: rate limit
- `5xx`: Alpaca service side issue

## Druck decision rules when Alpaca data fails

- If quote/bars needed for trigger validation are unavailable, cap recommendation at `watch_only`.
- If only one supplemental metric is missing but core Phase II evidence is present, cap at `conditional_buy`.
- Log reason in `Candidates.notes` as `alpaca_data_missing:<reason>`.

## Minimal cURL cookbook

Account:

```bash
curl -sS "$ALPACA_ENDPOINT/account" -H "APCA-API-KEY-ID: $ALPACA_API_KEY" -H "APCA-API-SECRET-KEY: $ALPACA_API_SECRET" | jq '.'
```

Latest quote:

```bash
curl -sS "https://data.alpaca.markets/v2/stocks/NVDA/quotes/latest?feed=iex" -H "APCA-API-KEY-ID: $ALPACA_API_KEY" -H "APCA-API-SECRET-KEY: $ALPACA_API_SECRET" | jq '.'
```

Bars:

```bash
curl -sS "https://data.alpaca.markets/v2/stocks/NVDA/bars?timeframe=1Min&start=2026-05-11T13:30:00Z&end=2026-05-11T14:00:00Z&limit=500&feed=iex" -H "APCA-API-KEY-ID: $ALPACA_API_KEY" -H "APCA-API-SECRET-KEY: $ALPACA_API_SECRET" | jq '.'
```
