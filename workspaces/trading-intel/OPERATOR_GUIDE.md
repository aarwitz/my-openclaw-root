# Operator Guide — AutoTrade

Status: active. Reconciled 2026-07-30.

AutoTrade is an internal-paper simulation. The owned SQLite ledger is the only
account and execution surface; no external broker switch or fallback exists.
Druck’s Telegram messages are narration, not the source of truth.

## Current posture

- Strategy verdict: **NO EDGE / do not scale**. The robust 1,542-name replay
  found zero FDR survivors and all 100 mechanisms are deprecated.
- Open-risk authoring is quarantined. Cash is intentional while no robust
  mechanism is active; idle-cash reduction is not a success target.
- Existing paper positions remain subject to stops, falsifiers, horizons, risk,
  reconciliation, marks, and attribution.
- The locked forward shadow evaluation begins 2026-08-03 and must run at least
  60 sessions without tuning from its outcomes.

Do not copy position counts or P&L from this document. Query current state:

```bash
python3 ~/.openclaw/workspaces/trader/scripts/summary_report.py
python3 ~/.openclaw/workspaces/executor/scripts/sim_broker.py integrity --book desk
python3 ~/.openclaw/workspaces/trading-intel/scripts/integrity_check.py
```

Operational scripts must normally run through
`~/.openclaw/scripts/run-with-trace.sh`.

## Telegram contract

Druck sends at most one useful, action-first message per scheduled pass.
“Pass completed,” “note sent,” raw IDs, and a second cron delivery receipt are
bugs. A quiet pass is one short sentence. Health pages are separate,
deterministic alerts.

Ask in plain language for:

- the current regime, ledger, exposure, and benchmark snapshot;
- what changed in positions or cash and why;
- the strongest research hypotheses and their falsifiers;
- pending proposals or blocked pipeline state;
- a fresh critic review or an exit/trim proposal.

Treat every Telegram number as a summary of canonical state. If it conflicts
with the scripts above, the scripts/ledger win and the narration bug should be
filed.

## Scheduled behavior

Weekday passes run around 09:00, 09:30, 11:00, 13:30, and 15:30 ET. A pass:

1. verifies the internal-paper-only architecture;
2. serializes on the money-path lock;
3. refreshes regime, valuation, signals, predictions, and reviews;
4. applies protective exits through the normal gates;
5. refuses new risk while robust active mechanism count is zero;
6. executes only approved internal-paper intents;
7. reconciles, marks, attributes, learns, and rebuilds the GUI snapshot;
8. returns non-zero if any stage failed.

The internal simulator fills only marketable limits immediately. It has no
resting-order queue. An intent deferred while the market is closed is not an
open order and must be freshly rechecked at the open.

## Non-bypassable execution path

New risk must follow:

```text
ready hypothesis
  → prediction created before the intent
  → trader-authored intent
  → substantive Critic review
  → deterministic gates
  → Risk review
  → approved
  → internal-paper execution
  → fill/position lineage
  → reconciliation and attribution
```

Required gates include fresh sourced evidence, factor overlap, provenance,
two developed/adjudicated critic challenges, explainability, size/slippage,
stop rule, tranche consistency, and a pre-intent forecast with
`p_correct >= 0.52`, positive P50, and positive Kelly sizing. Risk-reducing
exit/trim intents are exempt from new-edge gates but still traverse execution,
lineage, and reconciliation.

## Proposals

No proposal is permission to change live parameters until it is decided and,
where necessary, implemented/reviewed in code. List the live queue with:

```bash
python3 ~/.openclaw/workspaces/developer/scripts/apply_proposal.py --list
```

Decisions are explicit and audited:

```bash
python3 ~/.openclaw/workspaces/developer/scripts/apply_proposal.py \
  --reject <proposal-id> --decider human --reason "<specific reason>"
```

Applying a proposal may only perform the narrow DB actions supported by the
script; code/config changes still require review, tests, commit, and deployment.

## Pauses and failures

Do not wipe or flatten the book to clear a health finding. The legacy reset and
reconciliation scripts were deliberately deleted because they could destroy
lineage or recreate stale state.

For a failure:

1. run the integrity, summary, and simulator checks above;
2. inspect the failing stage in the traced pass output;
3. preserve the ledger and audit history;
4. repair the smallest authoritative source;
5. re-run money-path tests and reconciliation;
6. verify the GUI snapshot and health sweep.

Missing marks, foreign-key violations, matured unresolved predictions,
closed trades without attribution, or a reintroduced broker path are critical.
`NO_EDGE` is an honest strategy warning, not a machine outage.

## Simulator limits

The simulator models spread and participation but not queue position,
stochastic partial fills, halts, name-specific borrow, dividend withholding,
margin interest, options, or full intraday impact. Results are paper evidence,
not live-execution equivalence.

## Authority hierarchy

```text
SYSTEM_ARCHITECTURE.md
  > docs/01_OPERATING_AUTHORITY.md
  > docs/03_EXECUTION_STATE_MACHINE.md
  > sql/schema.sql
  > docs/04_SHARED_STATE_SCHEMA.md
  > docs/05_IMPLEMENTATION_POLICY.md
```

Architecture changes require an entry in `DECISION_LOG.md`.
