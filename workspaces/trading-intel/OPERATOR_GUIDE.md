# Operator Guide — Trading Intelligence

Your daily interface with Druck via Telegram (`@druck_rsl_bot` in the Trading Desk group, topic Ask Druck, topic_id 641).

Companion doc: `HUMAN_USE_GUIDE.md` for efficient prompt patterns, notification strategy, and system-to-system interfacing.

---

## System state right now

- **Status:** clean slate, no positions, no pauses, no active hypotheses.
- **Regime:** not yet assessed. Druck needs to run a regime classification before opening new positions.
- **First action:** ask Druck to assess the current regime and produce thesis candidates.

---

## Starting a trading cycle

### Step 1 — Regime assessment

```
@druck_rsl_bot
Assess current regime using the live deterministic classifier. Pull SPY trend, VIX term structure,
credit spreads, and yield curve. Snapshot the result to the DB and summarize.
```

Druck will read the regime rules from `regime_rules` and write a row to the `regime` table. You will see the current state (risk_on / neutral / caution / risk_off / crisis) and which signals drove it.

### Step 2 — Request thesis candidates

```
@druck_rsl_bot
Produce top 3 thesis candidates for this regime. Include evidence, expected edge vs
SPY and 3% cash, and a concrete falsifier for each.
```

Druck follows the DRUCK_UPDATE format. Each candidate becomes a `hypothesis` row in state `draft`.

### Step 3 — Approve or reject

After critic review, Druck will present each hypothesis for your approval before submitting any trade intent.

```
@druck_rsl_bot /approve hypo_abc123
@druck_rsl_bot /reject hypo_abc123 rationale: too dependent on single data point
```

---

## Daily Telegram commands

| Command | What it does |
|---|---|
| `/summary` | Full system snapshot: regime, active hypotheses, open positions, intents, any pauses |
| `/hypothesis [id]` | List all active hypotheses, or show one in detail |
| `/intent [id]` | List open trade intents, or show one in detail |
| `/approve <id>` | Approve a hypothesis or trade intent to advance it |
| `/reject <id> [reason]` | Reject with a reason — written to audits |
| `/exit <ticker>` | Request a market close on a position |
| `/trim <ticker> <pct>` | Trim a position by a percentage |
| `/regime` | Show the current regime snapshot and which signals are driving it |
| `/critic <hypo_id>` | Request a fresh critic challenge on a hypothesis |
| `/archivist` | Request the archivist to resolve any completed hypotheses |
| `/audit [n]` | Show the last n audit rows (default 10) |

---

## Understanding Druck's output

Every substantive Druck reply is structured as:

```
DRUCK_UPDATE
request_id: <id or none>
thesis: <1-3 line summary>
evidence_with_sources:
- <source + finding>
expected_edge_vs_sp500: <daily/weekly/monthly/quarterly>
expected_edge_vs_cash: <daily/weekly/monthly/quarterly>
falsifier: <the condition that would prove this wrong>
next_action: <what happens next and when>
```

You will also see the raw tool trace output (script runs, DB queries, API calls) before the final DRUCK_UPDATE block. This is intentional — it gives you visibility into exactly what Druck is doing in real time.

---

## Understanding pauses

If Druck detects a problem, it opens a `system_pauses` row and enters `exits_trims_only` mode — no new opens or adds until resolved.

Common triggers:
- Broker/DB position divergence (Alpaca positions not reflected in the DB)
- Stale regime snapshot (>36 hours old)
- Missing critic review on a pending intent

To see what's paused:
```
@druck_rsl_bot /summary
```

To manually clear after you've verified the cause:
```
python3 ~/.openclaw/workspaces/trader/scripts/flatten_and_reset.py   # if positions need wiping
```
Or ask Druck to resolve and clear after you've confirmed the state is clean.

---

## Autonomous behavior (what Druck does without prompting)

Per the schedule in `docs/05_IMPLEMENTATION_POLICY.md` section 3:

| Time (US/Eastern) | What runs |
|---|---|
| 09:00 | Pre-market decision pass |
| 11:00 | Confirmation / invalidation pass |
| 13:30 | Replacement / rotation pass |
| 15:30 | Close-risk pass |
| Event-driven | On Alpaca order/position events |
| Sunday 09:00 | Weekly portfolio pattern extraction (Archivist) |

Druck will post updates to Telegram at each pass if there is anything material to report.

---

## Approval gates — what Druck checks before submitting any trade

Every trade intent must clear all of these before Druck submits to Alpaca:

1. Valid `hypothesis_id` in state `ready` or `active`
2. Critic has reviewed and approved the hypothesis
3. Data freshness: all source signals < 36 hours old
4. Explainability: thesis + falsifier + provenance + counterargument all present
5. Fill realism: sizing respects ADV fraction limits and includes slippage estimate
6. Expected edge beats both SPY and 3% annualized cash yield after slippage
7. Reconciliation gate: no open unresolved divergences

If any gate fails, the intent stays in `draft` or `critic_review` and Druck tells you why.

---

## Operator scripts

Located at `~/.openclaw/workspaces/trader/scripts/`:

| Script | Purpose |
|---|---|
| `summary_report.py` | Print the same output `/summary` generates — useful to verify DB state directly |
| `flatten_and_reset.py` | Close all Alpaca positions at market and clear any active system pause |
| `reconcile_legacy_positions.py` | Import pre-existing Alpaca positions into the DB as tracked legacy positions |
| `../trading-intel/reference/validation_corpus/export_learning_queue.py` | Export resolved hypotheses to a review queue for corpus promotion |

---

## Corpus growth loop (10-15 min/week)

As Druck resolves hypotheses, you can promote high-quality outcomes into the validation corpus:

1. `python3 reference/validation_corpus/export_learning_queue.py` — exports resolved outcomes to `tmp/learning_queue.jsonl`
2. Review the queue; author good ones as raw cases in `reference/validation_corpus/raw_cases/`
3. `python3 reference/validation_corpus/build_masked_from_raw.py` — converts to anonymized evaluation cases
4. `python3 reference/validation_corpus/validate_corpus.py` — validates counts and schema

See `reference/validation_corpus/CONTINUOUS_LEARNING_APPROVAL_LOOP.md` for full detail.

---

## Authority hierarchy (what wins when things conflict)

```
01_OPERATING_AUTHORITY.md  >  02_ARCHITECTURE.md  >  03_EXECUTION_STATE_MACHINE.md
    >  sql/schema.sql  >  04_SHARED_STATE_SCHEMA.md  >  05_IMPLEMENTATION_POLICY.md
```

Any change to these docs requires a `DECISION_LOG.md` entry.
