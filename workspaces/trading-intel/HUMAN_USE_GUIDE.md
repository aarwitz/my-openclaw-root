# Human Use Guide — Trading Desk

Audience: Aaron or any human/operator system interfacing with Druck.

This guide complements `OPERATOR_GUIDE.md`.
- Use `OPERATOR_GUIDE.md` for command reference and workflow order.
- Use this guide for how to interact with Druck efficiently day to day.

## 0. Fast start

If you want the shortest path to useful output, send these in order.

First message:

```text
@druck_rsl_bot /summary
```

If `regime: n/a`, send:

```text
@druck_rsl_bot
Assess current regime, snapshot it to the DB, and summarize the implications for new entries, adds, exits, and cash posture.
```

Then send:

```text
@druck_rsl_bot
Produce top 3 thesis candidates for this regime with evidence, expected edge vs SPY and cash, and a falsifier for each.
```

If you want passive trade/event updates, send this once:

```text
@druck_rsl_bot
Standing rule: whenever a trade intent changes state, an order is submitted, filled, canceled, or blocked, or a system pause opens/closes, post a concise update here with ticker, action, reason, and next step.
```

## 1. Where to talk to it

Canonical routing table:
- [workspaces/trading-intel/reference/TELEGRAM_ROUTING_MATRIX.md](workspaces/trading-intel/reference/TELEGRAM_ROUTING_MATRIX.md)
- Machine-enforced source: [workspaces/trading-intel/reference/telegram_routing_matrix.json](workspaces/trading-intel/reference/telegram_routing_matrix.json)

Surface definitions:
- DM: direct one-to-one chat with a bot account. No group chat id and no topic id.
- Group: a Telegram chat room identified by chat id.
- Topic: a thread inside a forum-enabled group. Topic ids are scoped to their group chat id.

- Primary execution interface: `@druck_rsl_bot` in Trading Desk topic Ask Druck (`topic_id 641`).
- Oversight/escalation interface: Dwight in Trading Desk topic `1`.
- Mention-gated policy is on. In group chat, address Druck explicitly with `@druck_rsl_bot`.
- `FYI @druck_rsl_bot ...` or `cc @druck_rsl_bot ...` means listen-only. Use that when you want Druck to absorb context without replying.

Routing authority rule:
- If there is any conflict between memory/chat notes and the matrix table, follow [workspaces/trading-intel/reference/TELEGRAM_ROUTING_MATRIX.md](workspaces/trading-intel/reference/TELEGRAM_ROUTING_MATRIX.md).
- Any route change must update the matrix JSON and pass [scripts/audit_telegram_routing.py](scripts/audit_telegram_routing.py).

Observed behavior baseline (May 31, 2026):
- Ask Druck topic returns explicit topic identity confirmation: "This is the Ask Druck topic in Trading Desk (topic 641)."
- Infrastructure oversight prompt in Dwight's forum topic returns infrastructure topic identity and oversight check status.
- Dwight DM requires an actual task payload. A pure "status check" can be rejected as "No task was included...".
- Resi DM supports direct status checks and confirms direct-reply path readiness.
- Jerry DM status checks can return platform hardening findings (permissions, update availability, service-path warnings).

## 2. Fastest way to get useful information

Start with the cheapest state read, then drill down.

Best first command:

```text
@druck_rsl_bot /summary
```

Use it when you want the current operating picture:
- active hypotheses
- open intents
- open positions
- current regime
- active pauses

Use narrower commands for drilldown:

```text
@druck_rsl_bot /regime
@druck_rsl_bot /hypothesis
@druck_rsl_bot /hypothesis <id>
@druck_rsl_bot /intent
@druck_rsl_bot /intent <id>
@druck_rsl_bot /audit 20
```

Use natural-language requests when you want synthesis rather than raw state:

```text
@druck_rsl_bot
What changed since the last material update? Focus on regime, active candidates, open risk, and any blocked actions.
```

```text
@druck_rsl_bot
Summarize the current portfolio posture in plain English: what we own, why we own it, what would make us add, and what would make us exit.
```

## 3. Best command patterns

Requests work best when they specify:
- objective
- constraints
- output format
- time horizon

Good pattern:

```text
@druck_rsl_bot
Objective: produce the top 3 actionable ideas for the current regime.
Constraints: no orders yet, evidence must be primary-source-backed, compare against SPY and cash.
Return: one ranked table plus a DRUCK_UPDATE block for the top idea.
```

If you want machine-friendly replies, ask for one `DRUCK_UPDATE` block and provide a `request_id`:

```text
@druck_rsl_bot
request_id: morning-check-001
objective: summarize current state and next best action
constraints: no order submission, concise, source-backed
required_outputs:
- thesis
- evidence_with_sources
- expected_edge_vs_sp500
- expected_edge_vs_cash
- falsifier
- next_action
```

## 4. Notifications and passive monitoring

What is already built in:
- scheduled trading passes at `09:00`, `11:00`, `13:30`, and `15:30` ET
- event-driven handling on Alpaca order and position events
- weekly audit summary into the trader Telegram thread
- material updates are posted when there is something worth reporting

What is not currently built as a separate surface:
- no dedicated subscribe/unsubscribe notification settings
- no separate notification command contract documented beyond the trading thread itself

Practical implication:
- the trading thread is the notification surface
- if you want tighter reporting, ask Druck for an explicit standing reporting rule in that thread

Example:

```text
@druck_rsl_bot
Standing rule: whenever a trade intent changes state, an order is submitted, filled, canceled, or blocked, or a system pause opens/closes, post a concise update here with ticker, action, reason, and next step.
```

For a lighter cadence:

```text
@druck_rsl_bot
Standing rule: only notify me here for fills, exits, new pauses, regime changes, and anything that changes expected edge or risk materially.
```

## 5. How to stay in touch optimally

Use three interaction modes.

Mode 1: snapshot
- `/summary` when you are re-entering context
- `/regime` when you care about market posture

Mode 2: decision support
- ask for top candidates
- ask for critic challenge on a specific hypothesis
- ask for edge vs SPY and cash before approving anything

Mode 3: management loop
- approve, reject, trim, or exit
- ask what changed since last check-in
- ask what is blocked and why

Good recurring human loop:
1. Run `/summary`.
2. If regime is `n/a`, request a regime assessment.
3. Ask for top candidates for the current regime.
4. Review one idea deeply with `/hypothesis <id>` or a natural-language challenge.
5. Approve or reject.
6. Check `/audit 20` or `/intent` later for trail and current posture.

## 6. When to use Dwight versus Druck

Use Druck when you want:
- current trading state
- hypothesis, intent, position, or regime details
- execution, trim, exit, or approval actions
- direct portfolio monitoring

Use Dwight when you want:
- supervisory pressure on Druck
- a missed update chased down
- a higher-level portfolio accountability pass
- a request framed as oversight, KPI enforcement, or escalation

Example Dwight escalation:

```text
@dwight_rsl_bot
ACTION_REQUEST_DRUCK
request_id: oversight-check-001
objective: verify Druck has current regime, top active risks, and next required actions
why_now: I want assurance the desk is not drifting silently
constraints: concise, evidence-backed, no order changes
required_outputs:
- thesis
- evidence_with_sources
- expected_edge_vs_sp500
- expected_edge_vs_cash_3pct
- falsifier
- concrete_next_action
deadline_et: 11:15
```

## 7. Best prompts for common jobs

Morning open:

```text
@druck_rsl_bot
Assess current regime, summarize current posture, and tell me the highest-value next action before market open.
```

Find ideas:

```text
@druck_rsl_bot
Produce top 3 thesis candidates for this regime with evidence, expected edge vs SPY and cash, and a falsifier for each.
```

Check risk:

```text
@druck_rsl_bot
What is the biggest current risk in the book or pipeline, what would trigger defensive action, and what is the monitoring plan?
```

Check execution drift:

```text
@druck_rsl_bot
Tell me anything currently blocked, stale, unreconciled, or waiting on critic review.
```

Portfolio explanation:

```text
@druck_rsl_bot
Explain the current book as if I have been away for a week: what matters, what changed, and what decisions are coming next.
```

## 8. If you want another system to interface with it

Best contract:
- include a stable `request_id`
- specify objective and constraints
- ask for explicit outputs
- prefer one request per message
- keep listen-only context separate from reply-expected requests

Recommended machine-oriented shape:

```text
@druck_rsl_bot
request_id: <stable-id>
objective: <single concrete goal>
context: <facts or prior state>
constraints: <risk, timing, no-trade rules, output length>
required_outputs:
- thesis
- evidence_with_sources
- expected_edge_vs_sp500
- expected_edge_vs_cash
- falsifier
- next_action
```

Avoid:
- mixing multiple unrelated goals in one message
- asking for execution and broad brainstorming in the same prompt
- assuming a reply without `@druck_rsl_bot` in the group topic

## 9. CLI backstops when Telegram is not enough

Use these only for verification or recovery:

```bash
python3 ~/.openclaw/workspaces/trader/scripts/summary_report.py
python3 ~/.openclaw/workspaces/trader/scripts/reconcile_legacy_positions.py --report
python3 ~/.openclaw/workspaces/trader/scripts/flatten_and_reset.py --dry-run
openclaw health
openclaw gateway status
```

Do not use gateway restart as a normal operating step. Use hot reload for config changes and the safe restart script only if a restart is truly required.

## 9.1 Script tracing and safe cleanup (operator workflow)

Purpose:
- keep script inventory lean without deleting active operational tooling
- require evidence before pruning anything

Rules:
- do not delete scripts based on age alone
- use run tracing first, then audit, then manual review
- treat deletion candidates as candidates only until dependency review is complete

Core commands:

```bash
# 1) Run scripts via trace wrapper so usage is recorded
~/.openclaw/scripts/run-with-trace.sh ~/.openclaw/scripts/<script>.sh [args...]

# 2) Generate stale/deletion candidate report
python3 ~/.openclaw/scripts/audit_script_inventory.py --stale-days 45

# 3) Export JSON snapshot for review
python3 ~/.openclaw/scripts/audit_script_inventory.py --stale-days 45 --json > ~/.openclaw/scripts/script_inventory_snapshot.json
```

What to review before deleting any candidate:
- referenced by cron jobs (`~/.openclaw/cron/jobs.json`)
- referenced by hooks (`~/.openclaw/hooks/`)
- referenced by docs/playbooks/automation wrappers
- still required for recovery or incident response

Recommended cadence:
1. Run with trace for 1-2 weeks of normal operation.
2. Re-run script inventory audit.
3. Remove only scripts that are stale, unreferenced, and clearly superseded.

Current implementation note:
- tracing is guaranteed when scripts are invoked through `run-with-trace.sh`
- not all scripts are self-logging on direct invocation yet

## 10. If Telegram is silent or behaving oddly

Fast checks:

```bash
openclaw health
openclaw gateway status
```

Telegram-side checks:
- make sure you are messaging the right account: `@druck_rsl_bot`
- in the group thread, include the explicit `@druck_rsl_bot` mention
- if you are using direct Telegram chat and the bot was never started there, send `/start`
- if pairing/access changed, re-check Telegram pairing and allowlist status

If `/summary` differs between DM and Ask Druck topic:
- treat Ask Druck topic (`topic_id 641`) as canonical for trading state snapshots
- run `python3 ~/.openclaw/workspaces/trader/scripts/summary_report.py` to inspect canonical DB state
- run `python3 ~/.openclaw/workspaces/trader/scripts/reconcile_legacy_positions.py --report` to quantify broker-vs-DB drift before taking action
- if broker shows live positions/orders while DB shows zeros, treat as reconciliation debt and do not assume autonomous logic has full position context

Useful recovery checks from the existing OpenClaw docs:
- if Telegram is not responding at all, confirm pairing completed
- if the wider system looks unhealthy, use `openclaw health` before touching anything else

Avoid treating restart as the first fix. Normal operating order is:
1. verify Telegram message shape,
2. verify gateway health,
3. verify the trading DB state with `summary_report.py`,
4. only then consider gateway recovery steps.

## 11. Bottom line

- Treat Druck as the trading front door, not as a vague chat bot.
- Use `/summary` first, then drill down.
- Prefer Ask Druck topic (`topic_id 641`) as the canonical summary surface; DM and topic responses can differ in detail and consistency.
- Ask for explicit standing notification rules if you want a tighter update cadence.
- Use Dwight for oversight pressure, Druck for execution and state.
- Keep requests structured so the reply can be acted on or parsed immediately.