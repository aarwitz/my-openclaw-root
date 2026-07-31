# 07 — Internal Paper-Trading Engine

Status: active. Reconciled 2026-07-30. The owned simulator is the only broker
surface. There is no external-broker runtime switch, fallback, credential, or
parity path.

## Books

- `desk`: the canonical paper account used by the agent pipeline.
- `model`: quarantined mechanical ranker experiment.
- Historical `shadow` rows may remain for audit continuity but are inert.

Operational mark, corporate-action, and integrity commands accept only `desk`
or `model`; they fail closed for `shadow` or an unknown book. Agent inventory
and narration must always scope simulator queries to an operational book.

## Fill and ledger contract

- Input is an approved `trade_intent`; only
  `workspaces/executor/scripts/broker.py` may submit it.
- A live Massive mark is required. Missing marks fail closed.
- The simulator crosses an estimated half-spread,
  `max(1bp, 8/sqrt(ADV$ millions))`.
- A non-marketable limit never becomes a fabricated fill.
- Participation is capped at 2% of trailing 21-session dollar volume.
- `apply_fill` is the single cash/position mutation point. It writes a
  `sim_orders` row and an audit in the same transaction boundary.
- Cash, position quantity, basis, and marked value must be finite. The nightly
  `integrity` check verifies account and mark arithmetic.

## Marks, cash yield, and corporate actions

- Equity is `cash + sum(qty * fresh_mark)`, persisted to `book_equity`.
- Intraday samples are retained in `book_equity_intraday` for the GUI.
- Cash yield is credited at most once per book/day and separately attributed.
- Splits are applied once per book/ticker/ex-date and always audited.
- If any held name cannot be marked, the engine refuses to write an equity row.

## Model-book quarantine

The `model` book is a forward experiment, not an authorization path into the
desk book. It is long-only, equal-weight top decile, monthly, with the same
spread/participation rules. Its results may support a human-reviewed proposal;
they never self-promote a feature, mechanism, or sizing rule.

## Deliberate limitations

The simulator currently models spread and market participation, but not queue
position, stochastic partial fills, halts, borrow availability/fees by name,
dividend withholding, margin interest, options, or intraday impact. Until those
are implemented and tested, production readiness is limited to modest,
liquid-equity paper orders and results must not be presented as live-execution
equivalence.
