# 01 — Operating Authority

Status: active. Authority order: this doc > `02_ARCHITECTURE.md` > `03_EXECUTION_STATE_MACHINE.md` > `04_SHARED_STATE_SCHEMA.md` > `05_IMPLEMENTATION_POLICY.md`.

## 1. Mandate

Beat the S&P 500 in an Alpaca paper account by exploiting slow market diffusion from primary-source world changes into the best liquid public-market expressions.

- Benchmark: SPY total return. Secondary benchmark: cash.
- Measurement windows: rolling 1-month, 3-month, 12-month active return and Sharpe vs SPY.
- Capital profile: long horizon, drawdown-tolerant if thesis evidence keeps confirming.

## 2. Operating principles

1. Selection, timing, and execution are separated across agents. No agent conflates them.
2. Thesis-first. Every trade points back to a `hypothesis_id`. No naked trades.
3. Primary data beats financial data. Read what prices are downstream of.
4. Falsifiers beat targets. Watch what kills the thesis, not the upside.
5. Bet small on new ideas. Bet larger as independent evidence confirms. Stay liquid.
6. Cash is a real competitor on every marginal trade.
7. Critic challenges are first-class. Every challenge requires a written response in the audit log before execution.
8. Archivist is how the system improves. Patterns it extracts feed the other four agents.

## 3. Allowed expression vehicles (first-class from launch)

- Direct equity (long, short with policy gates)
- ETF (broad theme expression)
- LEAPS calls and call spreads (high-conviction, defined horizon)
- Shorter-dated options (defined catalyst windows only)
- Pair trades (long primary + short corollary)
- Competitor shorts (paired with explicit long thesis only)

Constraints:

- Alpaca paper account only at launch.
- Options usage requires hypothesis confidence `high` and explicit catalyst window.
- Shorter-dated options: maximum 10% of the underlying position size and require an explicit event date.
- Competitor shorts: only with an explicit long pair, maximum 50% of the long-side position size.

## 4. Conviction-weighted sizing

Position size scales with accumulated independent evidence, not entry timing.

Tranche ladder, expressed as fraction of intended maximum position:

- Starter: 10–15%. Triggered when a hypothesis reaches `scored` with `high` confidence.
- Confirmation add: +15–20%. Triggered by a new independent confirming signal and prior Critic challenges addressed.
- Conviction add: +20–25%. Triggered when two or more independent signal types confirm and no falsifiers have broken.
- Max conviction add: remainder to maximum. Triggered at thesis mid-horizon with trajectory intact, only in `risk_on` or `neutral` regimes.

Maximum position sizes by conviction (percent of portfolio):

- Core conviction (high, long horizon): 5–10%.
- Emerging (medium): 3–5%.
- Starter: 1–2%.
- Small-cap asymmetry: up to 15% only if average daily volume exceeds $50M.

Pre-authorized conviction scaling:

- Trader may autonomously scale within approved conviction bands when all gates are satisfied (independent signals, intact falsifiers, regime allows, critic quality threshold met).
- Manual Aaron approval is required only for overrides above policy max bands or for concentration exceptions.

## 5. Concentration limits

- No single industry above 40% of portfolio.
- No single economic factor (e.g., supply constraint) above 50% of portfolio.
- Hedges (shorts + pair trades) above 30% of portfolio require Critic re-approval.
- Daily new opens capped at 2 names. Replacements, trims, exits, and rotations are not counted against this cap.

## 6. Regime gates

| Regime | New theses | Add cadence | Existing positions | Cash target |
|---|---|---|---|---|
| `risk_on` | Full | Full (all tranches) | Active, rotation enabled | 5–15% |
| `neutral` | Full | Full | Active, rotation enabled | 15–25% |
| `caution` | Reduced | Starter + confirmation only | Monitor, no new adds | 25–40% |
| `risk_off` | Paused | Paused | Monitor only | 40–60% |
| `crisis` | Paused | Paused | Active defense only | 60–80% |

Regime is owned by Quant. Trader cannot override regime gates without an Aaron-approved standing order.

## 7. Stops and per-trade risk

- Every position carries a stop derived from the falsifier set plus one standard deviation of thesis noise.
- Maximum loss if stopped: 2–3% of portfolio per position. This sets size automatically.
- LEAPS: tighter stops (about 10% on premium) due to theta.
- Shorter-dated options: stops are event-window driven; close on event resolution if thesis not confirmed.

## 8. Drawdown management

- Down 20% YTD: regime forced to `caution`, add cadence pauses, Critic re-approves all open positions.
- Down 30% YTD: regime forced to `risk_off`, only defensive exits permitted.
- Recovery target after a 20% drawdown: 6 months.
- Drawdown circuit breaker is automatic: threshold breach writes `system_pauses` records without manual intervention.

## 9. Autonomous authority for Trader

Trader may autonomously, without per-action human approval, in Alpaca paper:

- Open, add, trim, exit, rotate, cancel orders.
- Hold cash.
- Place options orders consistent with this doc.

Trader may not autonomously:

- Override regime gates.
- Execute a trade without a valid `hypothesis_id` and a Critic review record.
- Use leverage beyond Alpaca paper defaults.
- Use any data source outside the configured shared state.

## 10. Pause semantics

Pauses are defined as scopes over atomic actions, not over checkpoints:

- `new_entries_only`: blocks `open`, allows `add`/`trim`/`exit`/`rotate`.
- `adds_only`: blocks `add` and `open`, allows `trim`/`exit`.
- `shorts_only`: blocks any short-side action.
- `exits_trims_only`: blocks `open` and `add`; allows only `trim`/`exit`.
- `full_system`: blocks all non-defensive actions.

A rotation is treated as `exit` then `open`. If either atomic action is blocked by an active pause scope, the rotation is blocked.
