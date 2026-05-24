# Intraday Alpha Infrastructure Request

## Goal
Improve Druck's ability to manage the Alpaca paper portfolio for one specific objective:

**Maximize weekly return and maximize performance versus SPY over the current week.**

Current system quality is not good enough for this use case. We need a better intraday decision engine, better visibility into trade quality, and better execution support.

## Core Problems To Solve

1. Current pipeline is too oriented toward weekend / Monday candidate selection and not strong enough for live intraday re-ranking.
2. Candidate generation is too shallow and too conservative when the goal is short-horizon alpha.
3. There is not enough portfolio attribution, so Druck cannot clearly see which positions are hurting or helping SPY-relative performance.
4. Execution support is too primitive. Druck needs better support for limit entries, staged entries, and price-aware rotations.
5. Market-data access should be cache-first and gap-filling only. Massive / Polygon should be preserved and used only where it adds real value.

## What We Need Built

### 1. Intraday Alpha Ranking Engine
Build a script or service that ranks both:
- current holdings
- replacement candidates

for **expected outperformance vs SPY over the next 1 to 5 trading days**.

It should combine, at minimum:
- relative strength vs SPY on 1d / 3d / 5d windows
- event freshness (earnings beat, guide raise, contract, partnership, analyst revision cluster, sector sympathy)
- price structure (distance from 20d high, distance from 20d and 50d moving averages)
- volume confirmation
- ATR / volatility framing
- extension / crowding penalties
- sector leadership
- portfolio overlap / redundancy penalties
- macro regime overlay

Output should include both:
- **absolute score**
- **replacement delta score** versus current holdings

### 2. Portfolio Attribution Engine
Build a portfolio analysis step that shows, in real time:
- portfolio return since a chosen anchor time, especially since Sunday open / Monday open / current week start
- SPY return over same interval
- active return (alpha) vs SPY
- per-position contribution to P&L
- per-position contribution to active return vs SPY
- sector concentration
- factor concentration
- positions ranked by:
  - biggest drag on active return
  - biggest positive contributor
  - weakest expected forward alpha

This is critical. Druck should be able to answer:
- what is hurting us most?
- what should be replaced first?
- what is working and deserves more capital?

### 3. Execution Planning Layer
Build trade planning output for each candidate and each proposed rotation.

For each proposed trade, output:
- ticker
- side
- suggested entry type (`market` or `limit`)
- preferred entry price
- acceptable chase range
- trigger condition
- invalidator
- stop logic
- expected holding horizon
- confidence level
- whether it beats the current cash alternative

For rotations, output:
- sell ticker / size
- buy ticker / size
- expected improvement in weekly alpha score
- whether sell should complete before buy
- whether buy should use market or limit
- if limit, what price and why

### 4. Limit Order Recommendation Logic
Add logic for when Druck should prefer a limit order.

Default guidance should be something like:
- use market for extremely liquid names when urgency is high and spread is tight
- use limit for less liquid names, wider spreads, or when price is extended intraday
- provide a recommended limit price from current bid/ask, recent intraday range, and volatility context

Druck needs concrete guidance such as:
- "Buy PANW with a limit at 214.20, acceptable chase to 214.90, invalidate above 216.10"

### 5. Cache-First Market Data Layer
Implement a durable local cache / local data store with these priorities:
- daily bars
- rolling ATR
- moving averages
- volume averages
- recent scans
- recent quotes / snapshots
- sector ETF state
- benchmark / regime state
- candidate history

Rules:
- use local cached data whenever fresh enough
- update incrementally, not by repeated full-window refetches
- only call Massive / Polygon for missing or stale structural fields
- use Finnhub / Alpaca for lightweight current-state checks where possible
- preserve provider quota aggressively

### 6. Candidate Discovery Expansion
Broaden candidate discovery beyond the current narrow list.

Need discovery buckets like:
- fresh earnings drift leaders
- analyst revision clusters
- strong sector peers reacting to a leader catalyst
- defensive outperformers
- high-relative-strength non-tech names
- tactical rebound setups
- recent IPO / new listing strength
- theme baskets like cyber, infrastructure, waste, animal health, power, industrials

The engine should not return zero actionable names unless the market is truly barren.

### 7. Weekly Alpha Mode
Add an explicit mode for:

**`objective = beat_spy_this_week`**

This mode should optimize for:
- high expected active return versus SPY over a short horizon
- willingness to hold cash if alternatives are weak
- higher tolerance for concentration in the strongest few ideas
- lower tolerance for benchmark overlap that cannot outperform
- explicit tracking-error-aware rotations

This is distinct from generic long-only stock scoring.

## Operational Requirements

1. Outputs must be deterministic enough to audit.
2. Each score must be decomposable into components.
3. Every recommendation must include source provenance.
4. Cache metadata must include freshness timestamps.
5. The system should gracefully degrade if one provider is limited.
6. The system must be fast enough for intraday use.
7. The response format should be easy for Druck to read and act on programmatically.

## Preferred Response Format Back To Druck
When the team delivers a tool, script, or agent-facing interface, please also provide a machine-friendly response in this structure.

### A. Capability Summary
- `capability_name`
- `status` (`ready` | `partial` | `blocked`)
- `entrypoint` (command, script path, API route, or skill path)
- `one_sentence_description`

### B. Inputs
List exact required and optional inputs.
Example:
- `objective`
- `portfolio_source`
- `candidate_universe`
- `as_of_time`
- `use_cache_only`
- `max_massive_calls`

### C. Output Schema
Describe exact returned fields.
For example:
- `ranked_holdings[]`
- `ranked_replacements[]`
- `proposed_rotations[]`
- `macro_regime`
- `cash_hurdle_result`
- `limit_order_plan`
- `source_freshness`

### D. Example Invocation
Provide a copy-pastable example command or API request.

### E. Example Response
Provide a realistic example JSON response.

### F. Data Freshness Rules
State:
- what is cached
- freshness TTLs
- what triggers a live refresh
- what providers are queried in fallback order

### G. Failure Modes
State exactly what happens if:
- Massive is unavailable
- Finnhub is unavailable
- Alpaca quote data is stale
- no candidates pass thresholds
- cash beats all candidates

### H. Decision Semantics
This is very important.
For every recommendation, tell Druck how to interpret it:
- does `score` mean absolute attractiveness or expected alpha?
- is `rotation_delta` additive?
- what threshold means actionable?
- when should Druck choose `market` versus `limit`?

## Preferred JSON Shape For Trade Recommendations
If possible, return something like:

```json
{
  "objective": "beat_spy_this_week",
  "as_of": "2026-05-12T14:05:00-04:00",
  "macro_regime": {
    "label": "caution",
    "benchmark": "SPY",
    "vix_level": 22.4,
    "notes": "Hot inflation and geopolitical stress are pressuring high-beta growth."
  },
  "portfolio_state": {
    "portfolio_return_week": -0.032,
    "spy_return_week": -0.011,
    "active_return_week": -0.021,
    "cash_hurdle_apr": 0.03
  },
  "ranked_holdings": [
    {
      "ticker": "RDDT",
      "forward_alpha_score": -12,
      "action": "trim",
      "reason": "Weak relative strength, no fresh catalyst, poor replacement efficiency."
    }
  ],
  "ranked_replacements": [
    {
      "ticker": "PANW",
      "forward_alpha_score": 18,
      "recommendation_class": "conditional_buy",
      "entry_plan": {
        "order_type": "limit",
        "limit_price": 214.2,
        "acceptable_chase_to": 214.9,
        "trigger": "holds above intraday VWAP and remains green vs SPY",
        "invalidator": "loses 212.8 on 15-minute closing basis"
      }
    }
  ],
  "proposed_rotations": [
    {
      "sell": {"ticker": "RDDT", "qty": 30},
      "buy": {"ticker": "PANW", "qty": 25},
      "rotation_delta_score": 22,
      "cash_beats_trade": false,
      "execution_notes": "Wait for sell fill, then place limit buy."
    }
  ],
  "source_freshness": {
    "massive": "cached 4m ago",
    "finnhub": "live 1m ago",
    "alpaca": "live 20s ago"
  }
}
```

## What Druck Will Do With This
Druck will use these outputs to:
- explain trades more clearly to Aaron
- decide whether a trade is better than holding cash at 3% APR
- choose market vs limit orders
- trim benchmark overlap
- rotate into stronger weekly-alpha candidates
- avoid low-conviction churn

## Priority Order
If the team cannot build everything at once, prioritize in this order:

1. Portfolio attribution engine
2. Intraday alpha ranking engine
3. Execution planning with limit-price logic
4. Cache-first data layer improvements
5. Candidate discovery expansion
6. Weekly alpha mode

## Final Request
Please optimize for practical usefulness over elegance.
Druck needs tools that make him materially better at intraday portfolio management this week, not just cleaner architecture.