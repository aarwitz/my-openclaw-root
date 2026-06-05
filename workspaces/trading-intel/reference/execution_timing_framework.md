# Execution Timing Framework

Status: active reference. This is not a sixth authority doc. It operationalizes the active authority stack for trader scheduling and order placement behavior.

## 1. Objective

Improve autonomous entry timing without turning the system into a constant low-value poller.

Core idea:
- use scheduled decision passes to prepare execution plans,
- use resting broker-native orders to react to price,
- use event-driven order/fill updates for feedback,
- avoid discretionary market-chasing between checkpoints.

## 2. What to prefer

Preferred order behavior for approved setups:
1. If a thesis is approved and the desired entry is below the current market, prefer a resting limit order at the approved entry band.
2. If a position is opened, prefer attaching protective stop logic immediately when the vehicle/order type supports it.
3. If a setup requires breakout confirmation rather than mean-reversion entry, require the confirmation condition to be named explicitly in the intent before any stop/stop-limit style entry is used.
4. If the setup has not passed all execution gates, do not place a resting order just to create a notification surrogate.

## 3. What to avoid

- Do not rely on market orders at the open unless the setup is time-critical and the expected edge still clears slippage-adjusted hurdles.
- Do not chase a name just because it moved fast between checkpoints.
- Do not create a new polling loop solely for price alerts when a broker-native order can express the action more directly.
- Do not leave stale resting buy orders live into the close unless the thesis explicitly calls for overnight exposure at that level.

## 4. Pass-by-pass behavior

### 09:00 ET pre-market decision pass

- Build or refresh the execution watchlist for any `ready` or `active` hypothesis that could lead to an `open`, `add`, `trim`, or `exit`.
- For each actionable name, record:
  - thesis status,
  - trigger type (`buy-the-dip`, `breakout-confirmation`, `risk-reduction`, `exit-on-falsifier`),
  - preferred entry band,
  - invalidation or stop logic,
  - preferred order type,
  - expiry window for any resting order.
- If all gates pass and the setup is price-sensitive rather than time-sensitive, prefer staging a resting limit order instead of waiting for the next checkpoint.

### 09:30 ET market-open reaction pass

- Focus on opening dislocations versus the 09:00 plan.
- If a name trades into the approved entry band and all gates still pass, convert the plan into a live resting order or fill-management action.
- If the opening move gaps through the band, do not auto-chase; reassess whether the expected edge remains above both SPY and cash after slippage.

### 11:00 ET confirmation / invalidation pass

- Review any live resting orders.
- Cancel or tighten orders whose thesis or tape confirmation deteriorated.
- Consider confirmation adds only if a new independent signal exists and tranche rules permit it.

### 13:30 ET replacement / rotation pass

- Reallocate attention and capital from stale or lower-edge setups toward higher-edge approved names.
- Resting orders may be replaced, resized, or canceled here if relative opportunity changed materially.

### 15:30 ET close-risk pass

- Cancel stale resting buy orders that should not remain live into the next session.
- Keep overnight orders only when the thesis explicitly tolerates overnight gap risk and the expected edge still clears slippage-adjusted hurdles.
- Ensure protective stops and exit logic are in place for any newly opened positions.

## 5. Notification design

Efficient price-hit awareness without a dedicated price-alert daemon:
- resting limit/bracket orders provide the action path,
- Alpaca order/position events provide the notification path,
- scheduled passes provide the reassessment path.

That is materially better than simple alerts because the broker can act at the price rather than merely telling trader that the price was touched.

## 6. Fail-closed rules

- If account, order, or position state is unavailable, do not place new resting orders.
- If reconciliation is unresolved, do not add new resting entry orders for the affected hypothesis.
- If the hypothesis is not `ready` or `active`, do not place any new entry order.
- If critic review, freshness, explainability, factor-overlap, or fill-realism gates fail, cancel candidate entry staging and report the reason.
