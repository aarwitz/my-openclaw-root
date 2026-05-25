---
name: financialmodeling-prep-api
aliases: ["fmp"]
description: "Analyst revisions, earnings estimate quality (actual vs consensus), and options/implied move data for trading catalyst research"
metadata:
  emoji: "📊"
  os: "linux"
  requires: "credentials.financialmodeling-prep-api.json (email, password, api key)"
  primaryEnv: "production"
  authType: "api-key"
  rateLimitPerMinute: 100
  supportedEndpoints:
    - "/stable/grades-consensus"
    - "/stable/grades-historical"
    - "/stable/ratings-snapshot"
    - "/stable/price-target-consensus"
    - "/stable/price-target-summary"
    - "/stable/analyst-estimates"
    - "/stable/financial-estimates"
---

# Financial Modeling Prep API (FMP)

## Overview
Access to analyst-consensus breadth, price-target context, estimates access where available, and financial-quality snapshots for trading catalyst research. Used by Druck as an analyst-sentiment support layer, not as sole catalyst proof.

## Source authority

**Primary use**
- analyst-consensus breadth
- ratings trend context
- price-target range / activity context
- estimate trend context when the subscribed plan exposes it

**Secondary use**
- quality and valuation snapshot color
- sentiment/context requests that should not consume Massive quota

**Do not use FMP as**
- sole proof of a catalyst gate
- the primary price-structure engine
- the execution or portfolio-state source

Routing note:
- prefer FMP before Massive when the task is analyst sentiment, target context, or ratings breadth rather than historical price structure

## Authentication

**Credential file**: `~/.openclaw/credentials/financial-modeling-prep-api.json`

```json
{
  "email": "your-fmp-email@example.com",
  "password": "your-fmp-password",
  "api key": "your-fmp-api-key"
}
```

**Shell export** (for manual CLI testing):
```bash
export FMP_API_KEY="$(jq -r '.\"api key\"' ~/.openclaw/credentials/financial-modeling-prep-api.json)"
```

**Gateway injection**: Druck's systemPrompt includes FMP API key via secrets provider. All calls use the gateway-injected key automatically.

---

## Key Endpoints & Usage Patterns

### 1. Analyst Consensus and Ratings Breadth
**Preferred endpoints**:
- `/stable/grades-consensus?symbol=TICKER`
- `/stable/grades-historical?symbol=TICKER`
- `/stable/ratings-snapshot?symbol=TICKER`

**Use case**: Measure current Street consensus and whether ratings breadth is strengthening or weakening.

**Command patterns**:
```bash
curl -s "https://financialmodelingprep.com/stable/grades-consensus?symbol=AAPL&apikey=${FMP_API_KEY}" | jq '.[0]'
curl -s "https://financialmodelingprep.com/stable/grades-historical?symbol=AAPL&apikey=${FMP_API_KEY}" | jq '.[0:4]'
curl -s "https://financialmodelingprep.com/stable/ratings-snapshot?symbol=AAPL&apikey=${FMP_API_KEY}" | jq '.[0]'
```

**Druck integration**:
- Consensus `Buy` with broad buy count supports sentiment confirmation
- Deteriorating historical ratings breadth is a caution flag
- `ratings-snapshot` is valuation/quality color, not catalyst truth

---

### 2. Price Target Context
**Preferred endpoints**:
- `/stable/price-target-consensus?symbol=TICKER`
- `/stable/price-target-summary?symbol=TICKER`

**Use case**: Compare current price to Street target range and check how active the target revision stream has been.

**Command patterns**:
```bash
curl -s "https://financialmodelingprep.com/stable/price-target-consensus?symbol=AAPL&apikey=${FMP_API_KEY}" | jq '.[0]'
curl -s "https://financialmodelingprep.com/stable/price-target-summary?symbol=AAPL&apikey=${FMP_API_KEY}" | jq '.[0]'
```

**Druck integration**:
- Consensus target materially above spot, plus fresh target activity, supports analyst-revision context
- Stale or thin target coverage should not be over-weighted
- Use as secondary evidence behind Finnhub/news catalyst verification

---

### 3. Analyst / Financial Estimates
**Preferred endpoints**:
- `/stable/analyst-estimates?symbol=TICKER&period=quarter`
- `/stable/financial-estimates?symbol=TICKER`

**Current access note**:
- These stable endpoints are live, but some fields or periods may be premium-gated depending on plan
- In this workspace, `period=quarter` on `stable/analyst-estimates` currently returns a subscription-gating response
- `stable/financial-estimates` may return empty arrays for some symbols

**Druck integration**:
- Use when accessible for consensus quality and estimate trend checks
- If gated or empty, fail closed and rely on Finnhub for primary earnings estimate/actual evidence

---

### 4. Options / Implied Move
FMP is not the primary implied-move source in this setup.

**Workaround**:
- Use Schwab options data when available
- Otherwise use ATR-based expected move estimates from Massive/Alpaca context
- Treat FMP as analyst/fundamental support, not options authority

---

## Error Handling & Rate Limits

**Rate limit**: 100 requests/minute (FMP standard tier).

**Common errors**:
- `401 Unauthorized`: API key expired or invalid. Re-validate credentials file.
- `402` / premium gating message: endpoint exists, but requested dataset/parameter is not included in the current plan.
- `429 Too Many Requests`: quota exceeded. Wait 60s or batch requests differently.
- `[]` empty array: endpoint reachable, but no data returned for symbol/plan combination. Treat as unavailable, not bullish.

**Fallback discipline**:
- Finnhub remains primary for earnings actuals/estimates, revisions, and catalyst evidence.
- Massive/Alpaca remain primary for price and volatility context.
- Do not substitute Alpaca for analyst estimate sourcing.

---

## Most important fields

Grades consensus / historical:
- `strongBuy`, `buy`, `hold`, `sell`, `strongSell`
- `consensus`
- historical changes in rating distribution

Price target endpoints:
- `targetHigh`, `targetLow`, `targetConsensus`, `targetMedian`
- `lastMonthCount`, `lastQuarterCount`
- average target changes through time

Ratings snapshot:
- `rating`
- `overallScore`
- factor subscores when returned

Analyst estimates when available:
- estimate period/date
- EPS or revenue estimate fields
- analyst count when exposed

Anti-drift rule: positive analyst sentiment can strengthen a verified thesis, but it cannot rescue a failed catalyst gate.

## Druck Workflow Integration

### Sat 10:00 ET: Catalyst Pull Phase
```
For each watchlist ticker:
  1. Finnhub first: earnings actuals/estimates, company news, revision evidence
  2. FMP grades-consensus / grades-historical: sentiment breadth check
  3. FMP price-target-consensus / summary: target dispersion and freshness check
  4. If analyst-estimates access is available on current plan, use it; otherwise mark unavailable
  → Log FMP fields as secondary confirmation, never as sole catalyst proof
```

### Sun 19:00 ET: Gate + Classify
```
If Finnhub/news catalyst verified:
  → Gate: PASS
  → FMP can strengthen or weaken confidence around the same thesis
Else:
  → Gate: FAIL; max watch_only even if FMP sentiment looks positive
```

### Scoring Application
```
Base score = (price_action × 0.4) + (setup_fit × 0.3) + (macro_regime × 0.3)

Catalyst boosts (if gate PASS):
  + Earnings beat > 2%: +2
  + Margin expansion QoQ: +2
  + Analyst upgrade cluster: +1 to +3 (depends on cluster size)
  + Consistency (beat last 3 reports): +1

Catalyst penalties (if gate PASS but risk detected):
  - Miss last 2 reports: -5 (max conditional_buy)
  - Margin compression: -3
  - Recent downgrade: -3
  - Analyst target < current price: -2
```

---

## Example: AAPL Earnings Research

**Query set**:
```bash
# 1. Analyst consensus breadth
curl -s "https://financialmodelingprep.com/stable/grades-consensus?symbol=AAPL&apikey=${FMP_API_KEY}" \
  | jq '.[0] | {symbol, strongBuy, buy, hold, sell, strongSell, consensus}'

# 2. Ratings trend
curl -s "https://financialmodelingprep.com/stable/grades-historical?symbol=AAPL&apikey=${FMP_API_KEY}" \
  | jq '.[0:4] | map({date, analystRatingsStrongBuy, analystRatingsBuy, analystRatingsHold, analystRatingsSell, analystRatingsStrongSell})'

# 3. Price target context
curl -s "https://financialmodelingprep.com/stable/price-target-consensus?symbol=AAPL&apikey=${FMP_API_KEY}" \
  | jq '.[0] | {symbol, targetHigh, targetLow, targetConsensus, targetMedian}'

# 4. Price target activity
curl -s "https://financialmodelingprep.com/stable/price-target-summary?symbol=AAPL&apikey=${FMP_API_KEY}" \
  | jq '.[0] | {symbol, lastMonthCount, lastMonthAvgPriceTarget, lastQuarterCount, lastQuarterAvgPriceTarget}'

# 5. Analyst estimates (may be plan-gated)
curl -i -s "https://financialmodelingprep.com/stable/analyst-estimates?symbol=AAPL&period=quarter&apikey=${FMP_API_KEY}" | head
```

**Druck outcome**:
```
AAPL example:
  Finnhub/news verifies the catalyst gate first
  FMP grades-consensus confirms whether Street breadth is supportive
  FMP price-target-consensus adds upside-range context
  If analyst-estimates is gated on the plan, mark unavailable and continue fail-closed on that field
```

---

## Access & Permissions

**User**: Aaron (credentials stored at `~/.openclaw/credentials/financial-modeling-prep-api.json`)
**Role**: Druck (financial manager, trading research)  
**Scope**: Readonly (estimates, ratings, fundamentals, surprises); no account data or order data
**Channel access**: Druck account in Telegram group -1003846579956 (Trading Desk)
**Availability**: Full API access; rate limit monitored by gateway

---

## References

- FMP API docs: https://site.financialmodelingprep.com/developer/docs
- Druck Phase II spec: ~/.openclaw/workspaces/druck/PHASE_II_PLAN.md
- Alpaca fallback (rate limits): ~/.openclaw/workspace/skills/alpaca/SKILL.md
- Trading Desk sheet: 19LPX1xGCme4umn22GN4Z7WBQxGZBWWcysDjM6JEW-D4
