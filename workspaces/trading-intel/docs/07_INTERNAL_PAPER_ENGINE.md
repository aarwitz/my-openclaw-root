# 07 — Internal Paper-Trading Engine (design, 2026-07-03)

**Decision driver:** Alpaca's paper layer keeps costing us engineering time on
*their* artifacts, not our alpha: split-desynced position math (CRWD 2026-07-03),
sparse/level-shifted portfolio history requiring server-side splicing, a
fail-closed week caused by trusting an external state machine, holiday-blind
order queuing. Execution simulation is deterministic bookkeeping — exactly what
this codebase is best at. Market DATA stays external; the BROKER becomes ours.

## What we replace vs keep

| Alpaca capability | Verdict | Replacement |
|---|---|---|
| Order acceptance/fill simulation | REPLACE | internal fill engine (below) |
| Positions & account state | REPLACE | our ledger (positions/orders + book equity) |
| Portfolio history (equity curve) | REPLACE | computed from our own ledger + EOD marks (kills the history-splicing bug class) |
| Market clock / trading calendar | KEEP (data) | /v2/clock,/v2/calendar are reliable and free |
| Price bars / latest trade | KEEP (data) | still a quote source, alongside FMP/yahoo |

## Fill engine v1 (deterministic, auditable)

- Input: approved `trade_intents` (unchanged state machine — the engine is a
  drop-in for `execute_intent.py`'s broker calls).
- Fill rule: marketable limit vs live last trade ± half-spread estimate
  (spread_est = max(1bp, k/√ADV)); slippage = existing COST_RT model, now
  EXPLICIT per fill instead of implied.
- Partial fills: cap participation at 2% of trailing 21d ADV per pass.
- Corporate actions: splits/dividends applied nightly from FMP corporate-action
  feed to qty/cost-basis with an `audits` row per adjustment — the CRWD bug
  becomes impossible to have silently.
- Marks: EOD close per position; equity curve = cash + Σ(qty × mark), stored in
  a new `book_equity(book, date, equity, cash)` table. Intraday marks optional
  from latest_trade.
- Every fill writes `orders` + `audits` exactly as today (broker_order_id =
  `sim-<uuid>`).

## The killer feature: parallel books

Add `book TEXT NOT NULL DEFAULT 'desk'` to positions/orders (+ book_equity).
Then we can run *experiments as first-class accounts*:

- `desk` — the live desk (current behavior).
- `model` — the GBM ranker trades its own top-decile book mechanically. This
  builds the live track record that decides P2 promotion, with REAL fills
  instead of backtest assumptions.
- `no-x`, `no-llm`, … — cover/uncover ablation books: same pipeline minus one
  feature family, measuring each family's live contribution (the operator's
  cover→model→uncover→backtest loop, running continuously).
- Scoreboard compares books vs SPY and vs each other; FINDINGS gets a monthly
  ablation read.

## Migration plan (safety first — the desk never stops)

- **P0 (this doc)** — design + approval.
- **P1** — `sim_broker.py` (fill engine + ledger) + migration 0014 (book
  columns, book_equity). Shadow mode: every REAL Alpaca fill is mirrored into
  the sim ledger; nightly parity check (qty/basis/equity drift < 5bps).
- **P2** — `model` book goes live on the sim engine (no Alpaca involvement).
  Ablation books as wanted.
  **LIVE 2026-07-06** (`sim_broker.py rebalance-model`, wired into `nightly`).
  Pre-registered policy: long-only top decile of the nightly GBM ranks
  (~60 names), equal-weight, INVEST_FRACTION 98%, rebalance on the first
  nightly run of each calendar month, fills at mark ± half-spread
  `max(1, 8/√ADV$M)` bps with a 2%-of-ADV participation cap, ranks older
  than 5 days refuse to trade. First rebalance 2026-07-06: 59 names,
  $19.78 total spread cost on $98k deployed. The book's `book_equity`
  curve is the ranker's live forward track record — the promotion evidence
  (t>3 bar) that a backtest cannot provide. Surfaced in the web app via
  `snapshot_builder` → `simBooks`.
- **P3 — LIVE 2026-07-07 (D52).** Operator-directed early cutover after the
  second broker-side incident in a week. `broker.py` adapter seam, backend
  `sim` (config/broker-backend), desk book bootstrapped at parity from the
  live account with full curve continuity, web serving the sim ledger.
  Alpaca demoted to market-data-only; quote-source diversification (FMP/
  Massive) is the remaining step to full elimination.

Non-goals v1: intraday microstructure realism, options, margin interest. The
desk trades 21-day-horizon equities in ~$1k clips; fill realism beyond
spread+participation is false precision.
