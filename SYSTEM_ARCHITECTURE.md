# AutoTrade — Canonical System Architecture

> **Status:** AUTHORITATIVE. This is the single source of truth for the AutoTrade
> agentic trading platform that runs on the OpenClaw gateway. Where any other
> document disagrees with this one, **this document wins**. The formerly stale
> docs (`ARCHITECTURE.md`, `workspaces/trading-intel/FULL_DESIGN_ASCII.md`,
> `workspaces/trading-intel/docs/02_ARCHITECTURE.md`) were **archived 2026-07-02**
> to `archive/docs-retired-20260702/`.
>
> **Topology:** v5 · **DB schema/migrations:** through 0030 · **Last reconciled:** 2026-08-03

---

## 1. What this system is

AutoTrade is a **self-improving, agentic paper-trading desk**. A team of
specialised LLM agents runs an institutional-style research → decision →
execution → learning loop against an owned SQLite **paper simulator**, with the explicit
goals of (a) beating the S&P on a risk-adjusted basis and (b) making the agents'
decision process **visible** in the AutoTrade web app and over Telegram.

### 1.1 System shape — canonical short answer

Point-in-time prices, filings, fundamentals, macro, and news support falsifiable
ticker theses. The relational thesis engine records each thesis, its evidence,
falsifiers, valuation, substantive Critic review, forecast, and outcome lineage.
Separate offline evidence graphs provide association and retrieval context, but
never authorize risk. Deterministic code turns only reviewed theses into
market-relative probability/return forecasts, Kelly-based size suggestions,
portfolio-constrained intents, and internal-simulator fills. Realized outcomes
grade forecasts and every selection stage, update calibration, and surface
human-gated rule or code changes.

That paragraph is the default answer to a concise architecture question. Do not
replace it with the agent roster, a stage inventory, or volatile status. Current
positions, performance, edge verdict, and health must be read from the runtime
snapshot and health reports at answer time.

### 1.2 Four distinct research structures

- **Thesis engine (live SQLite, relational):** canonical live thesis per ticker,
  evidence, Critic state, predictions, intents, fills, and realized outcomes.
- **Theme model (live SQLite, relational):** explicit beneficiary and victim
  baskets, point-in-time evidence, measured relative-strength observations,
  lifecycle state, and optional thesis/intent lineage. It supplies research
  context only; a theme score never authorizes or sizes risk.
- **Quant knowledge graph (offline analytics SQLite):** ticker, sector,
  mechanism, and regime nodes plus correlation, co-mention, theme, redundancy,
  and regime-conditioning edges.
- **Evidence graph (offline analytics SQLite):** market states, events,
  catalyst types, policies, themes, tickers, and regimes; edges are explicitly
  `association_validated`, `hypothesis`, or `deprecated`.

These are **not one unified graph**. Episodes remain a separate point-in-time
FTS library. The current graphs do not directly contain thesis, prediction,
selection-outcome, or episode nodes; that lineage remains relational. Never say
the graph is “the memory for the theses” or that it connects forecasts to
outcomes unless those edges are actually implemented and verified.

Two design commitments shape everything below:

1. **Determinism-first.** Every numeric decision — regime classification,
   hypothesis scoring, probabilistic prediction, position sizing, risk caps,
   gate pass/fail, calibration — is produced by a **deterministic Python
   script** reading a single SQLite store. The LLM agents author *judgement and
   prose* (hypotheses, rationale, lessons) and *orchestrate*; they never invent
   the numbers. This makes the pipeline auditable and reproducible.
2. **Dual self-improvement.** The system gets better in two independent ways:
   - **Software/tooling** — the Developer agent implements new scripts, schema,
     and connectors in git worktrees (human-gated merges).
   - **Knowledge/world-model** — the system learns the *mechanisms that move
     prices* from realised outcomes, updating probabilistic beliefs over time
     (the World Model & Calibration layer, §6).

---

## 2. Runtime & infrastructure

- **One containerised gateway** (`openclaw-gateway`, port 18789) hosts **all**
  agents natively (no container-per-agent). Health-checked,
  `restart: unless-stopped`. `openclaw.json` **`gateway.bind` must stay `"lan"`**:
  the compose mapping `127.0.0.1:18789:18789` needs the in-container server on
  0.0.0.0 (host exposure stays loopback-only). With `"loopback"` every host CLI
  call fails (WS 1006) after the next restart. The legacy host systemd unit
  `~/.config/systemd/user/openclaw-gateway.service` must stay **disabled** — if it
  starts, it steals port 18789 and the container crash-loops without networking
  (see incident 2026-07-02).
- **Config hot-reloads** (hybrid mode). Editing `openclaw.json` or
  `cron/jobs.json` is applied within seconds; an **invalid** config is safely
  skipped and the last-good retained. **Never** `systemctl restart` — that can
  corrupt single-use OAuth refresh tokens. Use
  `~/.openclaw/scripts/safe-restart.sh` only if a true restart is unavoidable.
- **Sandbox is OFF** (`agents.defaults.sandbox.mode = "off"`); `tools.exec.host
  = "gateway"` — exec runs directly on the gateway host.
- **Model harness:** all agents use the bundled **Codex app-server harness**
  (`models.providers.openai.agentRuntime.id = "codex"`), OAuth (ChatGPT/Codex
  sub), **no OpenAI API keys and no per-agent model selection**. The model id
  remains `openai/gpt-*`; that namespace does not select API-key auth. The
  effective runtime route must say `authProvider=openai-codex`, every agent
  store must contain the shared `openai-codex` OAuth profile, and neither
  `openai:default` nor `OPENAI_API_KEY` may exist. Local Ollama credentials and
  market/news vendor keys are separate and do not authorize hosted OpenAI
  models. `enforce-codex-oauth.py`, offline preflight, and the live health sweep
  enforce this contract. `openai-codex` refresh tokens are single-use and
  fragile; sanitized OAuth-only recovery copies rotate in
  `credentials/token-backups/`.
- **2026-08-03 auth-drift incident:** the direct `openai:default` fallback had
  existed since the initial 2026-05-24 tracked baseline, and the gateway env
  also carried `OPENAI_API_KEY`, despite this document and the 2026-07-29
  handoff already specifying OAuth-only operation. Per-agent stores later
  diverged: Overseer/Researcher/Dwight could be empty while foreground status
  still exposed synthetic or alternate credentials. The result was an
  interactive-green/cron-red split. Exact evidence does not identify the
  operation that emptied each store, so no more specific cause is asserted.
  Fleet enforcement now removes direct OpenAI tokens from active and recovery
  stores, seeds every agent from one OAuth profile, rejects missing scheduled
  stores, and makes token restore incapable of resurrecting API-token auth.
  Removing a container environment key requires a **recreate through
  `safe-restart.sh`**; direct `docker restart`, direct Compose, and host systemd
  restart are not sanctioned recovery paths.
- **Jerry (host repair)** runs on **host cron** (not systemd). All six prior
  systemd user units were removed; a wrapper-governed host cron runs the
  Jerry/watchdog passes.
- **Governed scripts:** only scripts under `~/.openclaw/scripts` and
  `~/.openclaw/workspaces/trader/scripts` must run through
  `run-with-trace.sh` (they `require_wrapper()` / auto-re-exec). Scripts in the
  `quant`, `critic`, `risk`, `archivist`, `executor`, and `trading-intel`
  workspaces are plain Python.

---

## 3. The desk — 9 agents + Jerry

The AutoTrade desk is a canonical hedge-fund org, deliberately split so the
analytical responsibilities (idea quality / risk constraints / allocation
decision) are separate and individually visible.

| # | Agent | Emoji | Role |
|---|-------|-------|------|
| 1 | `researcher` | 🔎 | Primary-source scan; falsifiable hypotheses + evidence/sources. |
| 2 | `quant` | 🧮 | Deterministic regime classification, hypothesis scoring, **probabilistic predictions** (p_correct + return band). |
| 3 | `critic` | ⚖️ | Adversarial red-team; falsifiers; crowding/consensus flags; the 10-gate critic stack. |
| 4 | `risk` | 🛡️ | **Owns the intent→order veto.** Sizing limits, exposure/concentration caps, drawdown & regime halts. |
| 5 | `trader` (PM) | 💰 | Decision authority: final basket; authors sized intents (fractional Kelly) within the risk budget. |
| 6 | `executor` | ⚙️ | Internal-paper order simulation + ledger/lineage verification. |
| 7 | `archivist` | 📚 | **Learning officer.** Daily market debrief; resolves predictions (Brier); updates the world model; drafts gated rule proposals. |
| 8 | `overseer` (CIO) | 🤖 | Cron pipeline orchestrator + app/Telegram chat front door + heartbeat. Orchestrates only — never writes execution state, never edits scripts. |
| 9 | `developer` | 🔧 | Implements software improvements in git worktrees; opens PRs (human-gated). |
|  – | `jerry` | 🦝 | Default assistant, bound to Telegram. Runs through the Gateway inside the container like every other agent; reaches the real filesystem via bind mounts (`/home/aaron/repos:rw`, `~/.openclaw`). Host docker/systemd ops stay operator-owned. Not part of the AutoTrade desk. |

> **Decoupled 2026-06-17:** `dwight` is **no longer a desk agent**. It still runs in the
> gateway but is not counted, snapshotted, run-controlled, or metered as part of the
> trading desk. **Since 2026-07-14 dwight is the PM of the ATS v6 Trading Intel sprint**
> (Task Manager sprint_id=5, his only sprint): a daily 11:00 ET cron pass grooms the
> board, reads the deterministic scoreboard for a P&L one-liner (never invents numbers),
> files ≤1 issue/day, dispatches ≤2 coding-lane runs/day via
> `scripts/dwight-launch-from-issue.py --execute --detach`, and Telegrams Aaron a
> summary. The `developer` agent does the desk's actual code work in per-issue Codex
> subagent sessions; on success the launcher pushes the task branch, opens the PR
> (`gh`, human-gated merge), and flips the issue to `in_review`. Dwight PMs the
> *product and sprint* only — the intraday pipeline stays overseer-orchestrated and
> dwight never touches trading logic (rule_proposals only).
>
> `jerry` also exists in `openclaw.json` but is **not** part of the AutoTrade desk
> (`jerry` = default assistant, containerized with a `/home/aaron/repos:rw` bind mount).

---

## 4. Data store

Single SQLite database: `~/.openclaw/state/trading-intel.sqlite` (WAL mode).
`sql/schema.sql` contains the baseline schema (its world-model block originated
as schema v8); the live contract is that baseline plus numbered migrations
through **0030** under `workspaces/trading-intel/sql/migrations/`.

Core pipeline tables: `themes`, `theme_observations`, `hypotheses`,
`hypothesis_evidence`, `regime`,
`critic_reviews`, `expression_candidates`, `trade_intents`, `risk_reviews`,
`positions`, `portfolio_snapshots`, `audits`, `rule_proposals`.

World-model tables (migration `0008`, §6): `mechanisms`,
`mechanism_observations`, `predictions`, `market_events`. Episode library
(migration `0009`, §6.7): `episodes` (+ `episodes_fts`). Macro calendar
(migration `0010`, §6.8): `macro_releases`. Valuation layer
(migration `0011`, §6.9): `valuations`. Covariance/factor risk
(migration `0012`, §7.1): `portfolio_risk`.

Everything is threaded by `experiment_id`, and **every** state transition writes
an `audits` row (`actor`, `before_state`, `after_state`, rationale).

A **second, offline analytics DB** — `~/.openclaw/state/features.sqlite` — holds the point-in-time
feature store, backtest results, and the calibrated mechanism set (§11). It is deliberately separate
from the live store so the empirical foundation can be (re)built without touching production.

---

## 5. The trading pipeline (deterministic core)

`~/.openclaw/scripts/trader-pass-deterministic.sh` runs the deterministic
prefix of every pass and emits one consolidated JSON. Stage order:

Research intake and substantive Critic review are the outer thesis funnel, not
steps this routine manufactures on every pass. The routine below processes the
canonical state already present; `critic_baseline` is deterministic triage only,
and `predict` ignores everything except substantively reviewed `ready` theses.

```
classify_regime          quant/scripts/classify_regime.py      → regime row
  → value_universe       trading-intel/scripts/valuation.py     → valuations (fair value, MoS, realized vol)
  → score_hypotheses     quant/scripts/score_hypotheses.py     → quant_score
  → critic_baseline      critic/scripts/critic_baseline.py     → critic challenges
  → predict              quant/scripts/predict.py              → predictions (p + band)
  → ml_evidence_track    trading-intel/scripts/track_ml_evidence.py → ml_evidence_tracking (advisory
                                                               ranker trust ledger; no trading control)
  → enforce_horizons     trader/scripts/enforce_horizons.py    → exit intents for theses past
                                                               wm.HORIZON_DAYS + 5td grace (D55)
  → enforce_stops        trader/scripts/enforce_stops.py       → exit intents for stop breaches (D53)
  → enforce_inventory_lineage trader/scripts/enforce_inventory_lineage.py → full exits for
                                                               legacy/invalid opening provenance
  → author_intents       trader/scripts/author_intents.py      → trade_intents (adaptive deployment
                                                               governor + fractional-Kelly, state=proposed)
  → gate_evaluator       trading-intel/scripts/gate_evaluator.py  proposed|critic_review → risk_review | blocked
  → risk_gate            risk/scripts/gate_risk_intents.py     risk_review → approved|blocked (size capped)
  → sim_integrity_pre    executor/scripts/sim_broker.py        owned-ledger arithmetic preflight
  → reconcile_preflight  executor/scripts/reconcile.py         canonical vs simulator dry-run; nonzero on drift
  → execute_intent       executor/scripts/execute_intent.py    approved → submitted (internal ledger)
  → sync_fills           executor/scripts/sync_fills.py        broker truth per order id → orders (fill
                                                               price/time), intents (actuals), positions
                                                               (real hypothesis lineage; added 2026-07-06)
  → reconcile            executor/scripts/reconcile.py          fills vs DB (placeholder repair = last resort)
  → sim_mark             executor/scripts/sim_broker.py mark   → book_equity + trading-session-only
                                                               SGOV-proxy yield on deployable cash after
                                                               restricted short collateral + return attribution
  → scoreboard           trading-intel/scripts/benchmark_scoreboard.py → benchmarks rows (vs SPY, all horizons)
  → macro_seed/actuals   trading-intel/scripts/macro_calendar.py → macro_releases (+ surprise → market_event)
  → capital_efficiency   trading-intel/scripts/capital_efficiency_audit.py → capital_efficiency_snapshots
                                                               (ranked dollar bottlenecks, D55)
  → snapshot_state       developer/scripts/snapshot_builder.py → canonical-state.json
  → snapshot_project     lidi-solutions snapshot projector → public v4 data.json
  → pipeline_health + app_snapshot   developer watchdogs
```

**`trade_intents` state machine:**

```
proposed ─(critic gate stack passes)→ risk_review ─(risk gate)→ approved ─(executor)→ submitted → filled
   │                                       │                        │
   └──────────────── blocked ◄────────────┴────────────────────────┘
```

The critic gate stack (`gate_evaluator.py`, 10 gates: regime, evidence
freshness, factor overlap, provenance, counter-argument quality,
explainability, size sanity, slippage modelled, stop-rule present, tranche
consistency) routes a passing intent to **`risk_review`** — never straight to
`approved`. Only the **Risk** agent promotes to `approved`.

**Open-inventory lineage:** `developer/scripts/inventory_lineage.py` audits every
current desk position against the exact filled opening order and every later
post-cutover add. Modern lineage
requires a prediction and substantive Critic pass no later than intent creation,
plus an approved/resized Risk review no later than fill. Missing history is never
backfilled with invented provenance. Positions demonstrably opened before the
`_prediction_lineage_cutover` remain `legacy_pre_cutover` and may only be reduced
or freshly re-underwritten through the current gates; any incomplete post-cutover
opening is a red integrity failure. The runtime snapshot exposes counts, gross
value, per-position gaps, and the cutover under `inventoryLineage`.

At an open exchange, `enforce_inventory_lineage.py` turns every legacy or
invalid row into a full risk-reducing exit before new intent authoring. This
creates a clean forward book; it does not grandfather old exposure or liquidate
on a stale closed-market mark.

Two 2026-07-02 refinements (schema v13, D47/D48 in `DECISION_LOG.md`):
- **Risk-reducing intents (`exit`/`trim`) face only sanity gates** — never
  idea-quality gates (an exit blocked on stale evidence traps a loser).
- **Runtime is fail-closed for new shorts.** Research and offline backtests may
  study short signals, but Trader, Risk, Executor, and the simulator independently
  reject sell-to-open/add until borrow availability, borrow fees, collateral,
  rebates, recalls, and margin are modeled. Existing legacy shorts are forced into
  normal-gated buy-to-cover exits; reductions/exits can never be blocked by this
  quarantine. All exposure accounting remains absolute/gross.

---

## 6. World Model & Calibration layer (the learning engine)

This is how AutoTrade records predictive hypotheses and updates probabilistic
beliefs from outcomes. It does not identify *why* prices moved from correlation
alone. Shared math lives in
`workspaces/trading-intel/scripts/worldmodel.py` (pure stdlib: regularised
incomplete-beta, Beta PPF via bisection, half-life decay, log-odds combination,
return bands, fractional Kelly).

### 6.1 Mechanisms — predictive hypotheses (`mechanisms`)
A mechanism is a falsifiable transmission hypothesis:

```
antecedent_class → [transmission_chain] → consequent_class
   direction (long|short|neutral|risk_off|risk_on), horizon, regime_context
```

Each carries a **Beta(α, β)** belief about its reliability, summarised as a
`posterior_mean` with a credible interval and a `half_life_days` (older evidence
decays). Status flows `candidate → active → deprecated|crowded`. Seven starter
mechanisms are seeded, including the canonical
**`mech_jobs_duration_tech`** (hot jobs print → rate-path repricing → real
yields up → duration repricing → high-multiple tech/AI underperforms) and
**`mech_oil_inflation_rates`**. The episode library (§6.7) adds ten more
(AI-fear overreaction, saaspocalypse seat-substitution, datacenter power demand,
govt-contract award, reflexive political signal, launch-dependency shock,
no-cashflow narrative decay, leveraged-ETF trend compounding, memory supercycle,
priced-in insider distribution).

> **Current status (2026-07-30): none is production-validated.** The robust
> replay clustered same-date names, used HAC inference, required date/name
> breadth, controlled FDR, and found zero survivors. All 100 live records are
> deprecated and open-risk authoring is quarantined. Historical rows remain for
> audit and learning continuity.

### 6.7 Episode library (`episodes`) — named, dated research memory (schema v9)
The desk learns market structure from a curated library of **real, named,
dated** market episodes (migration `0009`; seeded by
`trading-intel/scripts/seed_episodes.py` from the operator's ground-truth cases).
This replaced masked cases only as the desk's **research retrieval and
mechanism-learning memory**. It does not replace the separate blinded
`validation_cases` evaluation lane required by §6.12 and the implementation
policy. Research memory keeps names and dates because retrieval needs them;
look-ahead is controlled by **walk-forward discipline**:
- `knowable_at` — earliest primary-source availability. Retrieval at decision
  time may only surface episodes with `knowable_at` strictly before the decision
  (no look-ahead). Enforced by `trading-intel/scripts/retrieve_episodes.py`.
- `resolved_at` — when the outcome materialized (backtest grading).

Each episode ties a real catalyst → mechanism → repricing → outcome → lesson,
records the `correct_action` and the `naive_trap`, and flags negative controls
(`is_negative_control` — correct action = no-trade despite an apparent signal,
e.g. no-cashflow hype). Resolved, directional, non-control episodes fold into
`mechanism_observations` (`source_type='manual'`, aged by `resolved_at`) so the
world model's Beta posteriors learn from them. An FTS5 index (`episodes_fts`)
powers similarity retrieval by theme/catalyst/lesson.

At decision time the researcher and trader call
`trading-intel/scripts/retrieve_episodes.py` (walk-forward gated by `as_of`) to
pull the closest analogues — surfacing each episode's `correct_action` and
`naive_trap` so the desk doesn't repeat a known mistake.

### 6.12 Blinded validation corpus (`validation_cases`)

The named episode library may generate research analogues; it may not prove
reasoning quality. `validation_cases` is the independent evaluation lane. A
human-approved masked packet and its model decision are frozen before the
outcome resolves; the exact packet hash, model id, policy hash, knowledge
cutoff, confidence, rationale hash, and decision timestamp live inside the JSON
contracts. Resolution may add only the outcome object. `validation_corpus.py`
derives the grade, checks fake-date pairs, accuracy, negative-control false
positives, invariance, and ECE, and rejects post-hoc or mutated evidence.

Only structurally valid, resolved, substantive base cases count toward 30
post-cutoff and 60 negative-control minimums. Pending rows and fake-date
companions never inflate sample size. An empty corpus is honest and keeps the
production-edge reasoning gate closed; it is not a reason to invent cases or
stop internal-paper simulation.

### 6.13 Theme model (`themes`, `theme_observations`; schema v30)

Themes are falsifiable cross-name research frames, not prose tags. A theme owns
explicit beneficiary and victim baskets, a thesis, falsifier, status
(`watch|active|challenged|dead`), source and point-in-time evidence timestamps,
plus its latest measured spread/breadth score. `industry_rs.py` scans declared
industry baskets for bottom-to-top-quartile inflections; `score_themes.py`
measures each theme basket against its opposite basket or SPY; and
`theme_context.py` emits compact deterministic context for Researcher.

`hypotheses.theme_id` and `trade_intents.theme_id` preserve lineage when a
thesis genuinely belongs to a theme. The link is optional and never substitutes
for thesis evidence, prediction, Critic, Kelly, or Risk gates. Backfilled seed
observations are labeled observational; they cannot establish edge or alter the
live `NO_EDGE` verdict. Active themes without fresh evidence are a health error,
and stale or unfalsifiable themes must be challenged or killed instead of kept
as permanent narrative.

### 6.8 Macro expectations & surprise (`macro_releases`, schema v10)
The desk's biggest blind spot was being *surprised* by scheduled macro prints it
could have seen coming (the operator's May-2026 jobs/CPI example). Migration
`0010` + `trading-intel/scripts/macro_calendar.py` maintain a forward calendar of
high-impact releases (NFP = first Friday, exact; CPI/UNRATE mid-month) and, after
each print, pull the actual from FRED (keyless), compute the surprise vs prior/
consensus, set a `rate_path_lean` (hawkish/dovish), and on a *large* surprise
write a `market_event` + `mechanism_observation` so the world model learns the
macro→repricing link. `macro_calendar.py upcoming` lets every pass pre-position
duration/risk before the print. Free + deterministic only (FRED/Treasury/Cboe);
the browser is never involved.

### 6.9 Valuation engine (`valuations`, schema v11)
The desk reasoned about *catalysts* (the world model) but had no notion of what a
company is *worth*. `trading-intel/scripts/valuation.py` is the intrinsic-value
layer, run as the `value_universe` stage of every pass (held names + tickers on
live/scored hypotheses). For each US single-name it computes, from free data only:
a two-stage **FCFF DCF** (real FCF from SEC EDGAR, revenue-CAGR growth fading to a
terminal rate, WACC from CAPM beta vs SPY + the FRED 10y), a **reverse-DCF**
market-implied growth ("what is the price assuming?"), a growth-justified
**earnings-multiple** cross-check (skipped when GAAP earnings are distorted, e.g.
acquisition amortization), and diagnostic **multiples** — blended into a **fair
value**, **margin of safety**, **zone** (cheap/fair/rich) and a data-driven
**confidence**. Single-stock DCF is noisy, so margin of safety is **hard-clamped to
±60%** and confidence collapses on extreme/disagreeing values; everything downstream
is confidence-scaled, never a hard price target. Fundamentals: SEC EDGAR companyfacts
(`connectors/edgar.py`, picks the freshest XBRL concept to survive concept switches).
Price/vol/beta: Massive bars. Risk-free: FRED. Deterministic +
cached + never the browser; ETFs/unvaluable names are stored `applicable=0`.

Two consumers wire valuation into decisions:
- **Predictions (§6.2)** — `predict.py` widths the band by the name's realized
  volatility and nudges P50 toward fair value by the (bounded, confidence-scaled)
  margin of safety; degrades to the generic band when no valuation exists.
- **Critic discipline** — `critic_baseline.py` raises the conviction bar to promote a
  **richly-valued** name (negative MoS, decent confidence): don't overpay unless the
  edge is strong. Advisory; the Risk gate stays the hard, non-bypassable stop.

### 6.2 Predictions (`predictions`)
For a hypothesis, `quant/scripts/predict.py` computes a calibrated
**`p_correct`** by combining the linked mechanisms' posteriors in log-odds space,
weighted by confidence and evidence quality, and **direction-aware**: a
mechanism that *opposes* the thesis pushes `p_correct` **down** (its posterior is
reflected). It also emits a return band **P10/P50/P90** for the horizon. Each
linked mechanism is stored with its alignment so calibration can attribute
outcomes correctly. The band is now **name-aware** (§6.9): its P10/P90 width comes
from the name's realized volatility (not a per-horizon constant) and its P50 is
nudged toward fair value by the bounded, confidence-scaled margin of safety.
Predictions are emitted only for `ready` hypotheses after a substantive Critic
review. The deterministic baseline is triage and can challenge, but cannot
promote or stand in for adversarial review.

Each row freezes `thesis_direction`, `prediction_policy_version`, and a SHA-256
fingerprint of the exact predictor/world-model source. Resolution and replay use
that frozen direction, never mutable thesis prose. Legacy rows are explicitly
`legacy_unversioned`; their missing historical code hash is not fabricated.
`prediction_replay.py` recomputes fixed probability variants from frozen rows in
offline preflight, but is retrospective diagnostic evidence with no promotion
authority.

Probability changes use a forward shadow champion/challenger lane
(`prediction_challenger.py`, migration 0025). Paired variants are recorded
without entering `predictions`, so they cannot drive trades or double-count
world-model observations. Promotion criteria, sample size, elapsed sessions,
and clustered inference are source-controlled before the cohort starts.

### 6.3 Sizing — fractional Kelly, capped by Risk
`trader/scripts/author_intents.py` reads the latest unresolved prediction and
sizes via **quarter-Kelly** from `p_correct` and the return band, capped at 10%
of equity as a *suggestion*. If a prediction exists but Kelly is ≤ 0 (no edge),
the trader **declines to author** the intent. The **Risk agent caps the final
size** (§7) — Kelly proposes, Risk disposes.

### 6.4 Daily debrief (`market_events`)
`archivist/scripts/market_debrief.py` records *what moved and why* **every
trading day, even when we don't trade** — index moves (auto-pulled), our day
P&L and exposure alignment, the catalyst class, a concise lesson, and the
mechanisms exercised. Each exercised mechanism appends a
`mechanism_observations` row (`source_type='market_event'`). This is how a
no-trade day like 2026-06-05 still teaches the model.

### 6.5 Calibration (`archivist/scripts/calibrate.py`)
The closed loop, three stages:

1. **Resolve predictions** → compute the **Brier** component once the hypothesis
   is graded; emit one decayed `mechanism_observations` row per linked mechanism
   (an *opposing* mechanism scores a **hit** when the thesis turned out **wrong**).
2. **Recompute Beta posteriors** from the full observation ledger with half-life
   decay — **autonomous**; data accumulation never needs approval.
3. **Draft gated `rule_proposals`** for structural changes a human must approve:
   `candidate → active` promotion (tight, high CI on enough evidence),
   `active → deprecated` retirement (collapsed posterior), and a
   scoring-recalibration review when aggregate Brier degrades.

### 6.6 Two learning rates
- **Fast / autonomous:** mechanism Beta updates + calibration tracking.
- **Slow / human-gated:** structural parameter changes (thresholds, scoring
  weights, mechanism promotion) flow as `rule_proposals` → human approves →
  Developer applies. Agents **never approve their own proposals.**

---

## 7. Risk gate (`risk/scripts/gate_risk_intents.py`)

The mandatory, final checkpoint before any order. It consumes intents in
`risk_review` and is the **single source of truth for limits**:

- **Per-name concentration:** ≤ 10% of equity in any one name (abs — shorts too).
- **Gross exposure:** ≤ 60% of equity deployed (open positions + pending intents,
  abs-summed; a short consumes budget like a long, never netted).
- **Concurrent names:** ≤ 48 (24→48 by operator direction on 2026-07-07).
- **Correlation-cluster cap (§7.1):** ≤ 25% of equity across a cluster of names
  correlated ≥ 0.70 — the "eight names, one bet" guard. Best-effort via the risk
  model; on a data gap it is skipped (it can only *tighten*, never loosen).
- **Daily drawdown halt:** block all new risk if day P&L ≤ −3% of equity.
- **Regime halt:** `risk_off` blocks all new risk.

Verdict is `approved`, `resized` (downsize to the binding cap, then approve), or
`blocked`. Every decision writes a `risk_reviews` row (verdict, approved size,
full limits snapshot, breaches) and an `audits` row (`actor='risk'`).
**Fail-closed:** if equity cannot be read, intents stay in `risk_review` and the
script exits non-zero — never auto-approved.

### 7.1 Covariance / factor risk model (`portfolio_risk`, schema v12)

The caps above were **correlation-blind** — eight names that are all the same
AI-beta bet satisfied every one. `trading-intel/scripts/risk_model.py` adds the
covariance/factor view (pure stdlib, returns-based on Massive bars, cached):

- **Correlation clusters** — connected components at corr ≥ 0.70 (the same-bet
  detector). The gate uses `correlated_cluster()` to cap a candidate's cluster
  (the new name + holdings it co-moves with) at **25% of equity** — applied as one
  more binding cap in the resize chain, fail-safe (skipped on a data gap).
- **Portfolio volatility + parametric 1-day VaR/CVaR** (95/99), on gross exposure.
- **Risk contributions** (Euler/MCR — each name's share of total risk) and the
  **effective number of bets** (1 / HHI of those shares): ten names that are one
  bet score ~1.
- **Factor betas** — univariate portfolio beta to a basket of proxy ETFs
  (market/tech/small-cap/momentum/semis/energy/rates/gold): interpretable tilts.

Run as the `portfolio_risk` pass stage (after reconcile); each snapshot writes a
`portfolio_risk` row. Returns-based, deterministic, cached; never the browser.

---

## 8. Orchestration (cron)

`cron/jobs.json` is the live, hot-reloaded job source (there is **no**
cron→SQLite migration in this build). The overseer drives the desk:

- **Five market-time weekday passes** (`OVERSEER-DRIVE-V3`): pre-market 09:00,
  open 09:30, confirmation 11:00, rotation 13:30, close-risk 15:30 ET. Each runs
  the deterministic core and narrates the material portfolio change (or a
  truthful quiet pass) to Telegram via `druck`. Routine checkpoints do not
  spawn stage agents: the deterministic core owns scoring, baseline triage,
  prediction of already-cleared hypotheses, risk, execution, and reconciliation.
  This prevents the
  former every-pass five-idea quota from manufacturing duplicate theses.
- **One dedicated daily catalyst-research pass** may spawn Researcher once.
  It updates an existing live ticker thesis when one exists, may add at most
  five previously unrepresented tickers, and may correctly add zero. SQLite
  enforces one live thesis per ticker; dormant/resolved/retired rows are
  retained history. It then owns the single bounded substantive Critic pass:
  `critic_review_queue.py` supplies at most ten explicit IDs, the Critic writes
  reviews only, and a deterministic validator moves valid outcomes to
  `ready|challenged`. Forecasting follows ready state.
- **Daily post-close learning pass** (`overseer-daily-learning-1630-et`): runs
  **every weekday, trade or no-trade** — Researcher gathers what moved + why from
  primary sources, Quant quantifies, Archivist runs `market_debrief.py` +
  `calibrate.py`, and any drafted `rule_proposals` are surfaced **AWAITING
  APPROVAL**.
- **Weekly Sunday audit** (`overseer-weekly-audit-0800-et`): retrospective +
  next-week hypothesis sourcing + system audit.

---

## 9. Visibility & chat

- **AutoTrade web app** (lidi-solutions) reads the trader-intel snapshot:
  per-agent headline/last-output, hypotheses, intents, orders, regime,
  pipeline health. The decision process is the product.
- **Snapshot publishing is two-tier (2026-07-02):** data freshness is decoupled
  from code deploys.
  - **Data-only (fast, primary):** `scripts/push-trader-data.sh` regenerates
    the runtime snapshot under `~/.openclaw/state/trader-intel-snapshot/` and
    pushes `data.json` to the Cloudflare KV namespace `TRADER_DATA`
    (id `bc7ab40d…`); the Pages Function `/api/trader-data` (session-gated)
    serves it. Host cron cadence: every 10 min during market hours, hourly
    off-hours. Runtime jobs never write the tracked `lidi-solutions` checkout;
    a KV put takes seconds and has no deploy-count cost.
  - **Full site deploy (code changes):** `scripts/deploy-lidi-solutions.sh`
    (vite build + `wrangler pages deploy`) or the canonical GitHub `main`
    deployment. The reviewed `data.json` baked into that commit is the app's
    fallback when `/api/trader-data` is unavailable.
- **Live paper-ledger bridge (same-origin Pages Function):**
  - `/api/trader-live` returns internal-paper equity, positions, orders, and
    ledger history from the session-gated `TRADER_DATA` snapshot.
  - The browser never calls OpenClaw, a broker, or a market-data provider.
  - Missing/malformed snapshot data returns a fail-closed error; there is no
    external-broker fallback or broker credential in the Pages runtime.
- **Snapshot contract audit:** the Python builder owns canonical ledger and
  safety state in runtime-only `canonical-state.json`; the Node projector owns
  the richer `trader-intel/v4` presentation contract in `data.json`. They never
  overwrite the same file. The projector must preserve selection-funnel,
  prediction-replay, inventory-lineage, and exact pipeline-health state from
  the canonical input. `audit_app_snapshot.py` validates v5 topology,
  internal-paper identity, desk cash/equity/positions, those closed-loop
  reports, capital equity, and health color. KV publication fails closed on a
  red audit, so a shallow presentation-layer green cannot hide a canonical
  yellow/red condition.
- **Telegram** narration goes out on the `druck` bot (cron handles routing to
  the group topic / DM). Narration is action-first, source-backed, no tables.
  Telegram is a view, never the work queue: successful outbound bot messages
  are appended to `state/operator-events.jsonl` by the `message:sent` hook.
  Direct emergency Bot API pages pass through the same intake explicitly.
  INFO remains an observed audit event; explicit WARN/CRIT/FAILED output is
  assigned a stable incident family and appended to `state/priority-queue.jsonl`.
  Identical repeats inside 24 hours are observed without queue spam; changed or
  later recurrences reuse the stable family and can reopen work even when an
  older Task Manager issue is terminal.
- Any bot that should stay visibly responsive in Telegram needs the
  `group:messaging` capability in its tool allowlist/profile. A routed Telegram
  account with `group:messaging` missing can warn or fail on replies,
  attachments, and thread actions.
- **Backlog ownership (Task Manager):** `dwight-pq-rail.sh` runs every five
  minutes under a host lock and invokes `poll_priority_queue.py`, a **one-way,
  deterministic** mirror — it reads the overseer's append-only priority queue
  (`state/priority-queue.jsonl`) and creates/updates Task Manager issues for
  sprint 5. It is the *only* desk-side Task Manager mutation path and it **does not
  launch agents**. (The `dwight` LLM agent — a general dev/PM + dispatcher for broader
  work — was decoupled from the desk 2026-06-17, §3; the desk relies on this
  deterministic mirror, not on dwight.)

---

## 10. Self-improvement loops

```
            ┌──────────────── KNOWLEDGE (fast, autonomous) ────────────────┐
 market →   market_debrief / predict → mechanism_observations → calibrate   │
            → Beta posteriors + Brier ──────────────────────────────────────┘
                              │ (structural drift)
                              ▼
            rule_proposals ──→ HUMAN approves ──→ Developer applies ──→ DECISION_LOG
                              ▲                                            │
            ┌──────────────── SOFTWARE (slow, human-gated) ───────────────┘
 bot/health output → operator-event ledger → priority queue → TM mirror ; Developer worktrees → PRs
```

- **Knowledge** improves continuously and autonomously (mechanism beliefs).
- **Software** improves through human-gated Developer changes; the deterministic TM
  mirror provides the visible backlog. Self-improvement uses **OpenClaw-native delegation**
  (overseer/developer), **not** an auto-dispatch rail. The legacy
  `dwight-lane-bridge` auto-dispatch is **retired**: its cron entries were
  deleted and its scripts archived to `archive/scripts-retired-20260702/`
  (2026-07-02 prune).

---

## 11. Empirical foundation — feature store, replay & mechanism discovery

The world model (§6) was bootstrapped from hand-authored mechanisms + a small, hindsight-biased
episode library (12/13 episodes "confirmed"). That is being replaced by an **empirical, point-in-time,
survivorship-safe foundation** that lets the system *discover and validate* mechanisms from 20 years of
data. Built and validated 2026-06-18; live integration is the next gated step (see end).

**Feature store — `~/.openclaw/state/features.sqlite`** (separate from the live DB; analytics/offline).
One tall, point-in-time table `features(ticker, as_of, name, value, knowable_at, source)`; a read is
"latest `as_of ≤ D`". Built by `workspaces/trading-intel/scripts/feature_store.py`:
- *Technical* (computed from prices): `rsi14`, `mom_12_1`, `vol_20d_annual`, `dist_sma50/200`,
  `drawdown_252`, `dist_52w_high`.
- *Fundamental* (FMP, stamped at **`filingDate`** = knowable_at, NOT the fiscal-period date):
  `revenue_ttm`, `eps_ttm`, `net_margin_ttm`, `revenue_growth_yoy`; `pe_ttm` derived at read time;
  plus `eps_surprise_pct` events.
- Universe: **1,570 names, all-cap NASDAQ/NYSE (not S&P-restricted) ∪ retained delisted/failed** names, each
  usable only within its real [IPO → delist] window. `members_asof()` reconstructs point-in-time S&P
  membership from FMP constituent-change history (for survivorship-safe index studies).

**Data sources & connectors.** Massive is the primary split-adjusted daily and
live snapshot feed; `connectors/fmp.py` supplies deeper delisted history,
quarterly fundamentals/ratios, analyst estimates, earnings surprise,
constituent history, and the delisted list. The owned internal simulator is
the only broker surface. FRED =
macro, EDGAR = filings/insider/13F (free). **MCP is intentionally not wired** for this layer — the
feature store/backtest require deterministic, cached REST, not LLM-mediated MCP; an MCP *client* is a
later agent-research add (FMP and Unusual Whales both publish MCP servers).

**Mechanisms are now declarative data, not code.** A mechanism = `{conds:[(feature, op, threshold)],
direction, horizon, kind}`. The 17 hand-authored mechanisms (§6.1) are **seeds**, not canon; the system
also **generates** candidates (single-feature quintile triggers + cross-sectional rank factors) and
holds seeds, generated, and cross-sectional to the *same* bar.

**Replay / discovery engine — `mechanism_backtest.py` + `historical_walkforward.py` +
`worldmodel_stats.py`.** Strictly point-in-time (decision uses `as_of` features + entry on the next
bar; outcome is graded on future bars only). Streams ticker-by-ticker
(scales to thousands). Rigor controls:
- **non-overlapping** samples (spacing ≥ horizon) — no autocorrelation inflation;
- graded **market-relative vs the empirical base rate** (~0.49 on the broad universe, not 0.5);
- a bounded test interval with an exclusive end; training labels whose exits cross the test start
  are purged, and test labels that mature outside the hidden interval are excluded;
- primary significance = one-sided **HAC test on entry-date-clustered net mean-alpha**; raw ticker
  hits are descriptive and never treated as independent trials;
- every candidate must survive both raw SPY-relative alpha and a point-in-time
  trailing-252-session market-beta residual test (minimum 126 aligned returns,
  beta clipped to [0,3]); the cell p-value is the conservative maximum of the
  two one-sided p-values, so high-beta rebound exposure cannot masquerade as
  alpha;
- **Benjamini-Hochberg FDR + Bonferroni** across every (mechanism × horizon) + cross-sectional factor;
- **data-quality / tradability controls**: $5 price floor, $5M dollar-volume floor, per-horizon return
  winsorization, and a round-trip **transaction-cost** model (+short borrow) — alpha is reported **net**.

The preregistered `purged_walkforward_v1` development lane hides four non-overlapping two-year
periods from 2018 through 2025. Candidate thresholds are recomputed from each fold's training data;
an aggregate candidate needs adequate date/name breadth in at least three folds, positive raw and
beta-neutral median alpha and at least three folds positive on both measures, plus a second Bonferroni correction over combined
one-sided Stouffer p-values. It writes only an offline report under
`state/historical-validation/`, cannot replace `discovered_mechanisms`, and has
`promotion_authority=none`. These years have influenced system design, so even a survivor is a
stable historical development candidate—not untouched proof of edge. The separate locked forward
window remains the production-evidence lane. A completed replay freezes the exact conditions,
direction, horizon, universe digest, and chronologically latest pre-test threshold definition into
a content-addressed candidate set. `forward_shadow.py`, owned by the post-close learning chain,
records every eligible signal and later resolves raw-SPY and beta-neutral cost-net outcomes; it
cannot author intents or mutate live mechanism state. Missing more than two recording sessions
fails closed instead of reconstructing the holdout retrospectively. Canonical replay requires an immutable input directory
created by `historical_snapshot.py`: one SQLite backup plus only the exact frozen price/FRED cache
files the engine consumes, all content-hashed in a manifest. A report fingerprints that snapshot,
the engine, aggregation runner, exact historical-policy subsection, full policy file for
information, and frozen universe. Each fold also holds one
SQLite read snapshot across both passes, and any changed snapshot byte invalidates the run before
aggregation. Coverage records every loaded, empty, and failed symbol independently on both passes
and rejects pass drift. Snapshot creation exercises the engine's exact ordered feature query for
every universe member, preventing a damaged secondary index from becoming a silent exclusion.
Live cache mtimes/WAL state are never used as research identity. Canonical execution
is single-worker until a resource-bounded parallel implementation proves identical completion;
failed parallel launches have no checkpoint and therefore no evidentiary status.

Fundamental observations fail closed unless FMP supplies `filingDate` or `acceptedDate`; fiscal
period dates are never substituted. The 2026-08-02 provenance audit found two missing-timestamp
statements among 93,831 cached rows and removed their ten derived feature rows. The raw provider
records remain cached for audit/recovery. Because FMP's archive is not yet proven to preserve the
original pre-restatement statement values, `pe_ttm`, margin/growth fundamentals, and EPS-surprise
features are excluded from the canonical historical fold lane until they are rebuilt from original
filing accessions. Publication timestamps alone are not mistaken for full vintage safety.

Informed by **AlphaAgent (arXiv 2502.16789)**: the enemy is *alpha decay* (overfit + crowding); the
generator is regularized toward originality / hypothesis-alignment / complexity, and only OOS+FDR
survivors earn weight. An LLM proposer (richer multi-feature hypotheses) slots in at the generator.

**Calibrated mechanism set — `features.sqlite::calibrated_mechanisms`** (`promote_mechanisms.py`):
an untrusted offline research table of FDR-significant, net-positive-alpha survivors with measured
edge and a provisional world-model posterior. It is not, by itself, a deployment artifact.

**Research/live boundary.** `integrate_calibrated.py` fails closed unless a manifest committed
unchanged under `config/approved-strategies/` binds all of: the operator approval and decision-log
D-number, an unexpired approval window, the exact candidate-set digest, and the exact digest of a
completed minimum-duration locked forward-shadow report under `state/research-artifacts/`.
Historical/development reports have no promotion authority. Any post-approval change to results,
conditions, sample breadth, posterior, or candidate membership breaks the digest. Risk-reducing
`--deprecate-all` remains available without a risk-adding approval. There is intentionally no
approved manifest while the verdict is `NO_EDGE`.

**Feature availability boundary.** The current tall store defines `as_of` as the first date a value
is usable, so every writer must emit `as_of == knowable_at`, a finite value, and nonempty source.
Shared Python validation plus SQLite insert/update triggers enforce that contract even for direct
writers, and the release preflight audits the entire store. Economic-period, accession, and vintage
metadata must be kept separately; they may never be represented by moving `as_of` earlier.

**Historical 2026-06-18 cutover (superseded by the current zero-active NO_EDGE
state).** `integrate_calibrated.py` reset the hindsight-biased learned state and
installed 31 then-calibrated survivors. That event remains provenance, not a
statement that those mechanisms are active today.

Three loops now close around it:
- **Live learning loop (closed):** `resolve_prediction_backlog.py` grades each
  matured prediction from its frozen direction, timestamp, and horizon, then
  emits bounded mechanism observations; it does not mutate hypothesis lifecycle.
  `calibrate.py` folds those observations into retained posteriors. The weekday
  `learning-chain.sh` owns this deterministic sequence.
- **Selection-funnel loop:** `developer/scripts/selection_funnel_attribution.py`
  grades every genuine candidate at fixed 5/21/63-session horizons versus SPY,
  including ideas rejected before intent/fill. It freezes each stage at the
  counterfactual entry close and reports entry-date-clustered inference. For
  each zero flag, current process lineage now separates candidates that reached
  the stage later (measured latency) from candidates still not reached; stage
  rankings use only the latter comparison and require at least five independent
  entry dates in each arm. A newest ticker close that arrives before the
  bounded SPY cache is `pending`, not a data hole; historical missing benchmark
  dates remain `data_blocked`. Matured outcomes are immutable.
- **Deterministic activation:** `signal_scan.py` fires the calibrated mechanisms from each ticker's
  *current* features → ranked conviction (advisory; wiring into live intents is the next gated step).
- **Mechanism discovery (ongoing):** `mechanism_backtest.py` (`gen_candidates` single-feature +
  `gen_multi` economically-aligned 2-feature) keeps proposing candidates under the same OOS+FDR+cost bar;
  `promote_mechanisms.py` may materialize survivors only as an untrusted analytics table. Crossing
  into the paper ledger requires the committed exact-artifact manifest described above.

**Current scheduling:** the weekday learning chain refreshes point-in-time
features, resolves matured forecasts with `resolve_prediction_backlog.py`, and
recomputes calibration. Signal and proposal jobs may continue collecting
research evidence, but `author_intents.py` fails closed while the robust active
mechanism count is zero. Promotion requires the full clustered/FDR evaluation,
locked forward-shadow completion, and an exact committed operator manifest; neither a
single-script p-value nor an approved prose proposal can promote a mechanism.

**GBM shadow lane:** `ml_ranker.py --score-live` scores today's current top-cap
universe for research discovery and a separate `model` simulator book that
rebalances monthly. That shadow book is not the canonical desk book and its
ranks do not enter desk intents, Kelly sizing, or Risk. Historical GBM metrics
use today's active top-cap universe, so they are development diagnostics rather
than survivorship-safe proof; only the forward shadow track is genuinely OOS.

---

## 11b. Current state (2026-08-02) — evidence is quarantined until it earns trust

**Data backbone.** Massive is the primary split-adjusted price, snapshot, and
news source. FMP supplies deeper/delisted history and fundamentals; FRED
supplies macro series; EDGAR supplies primary filings. The internal paper
ledger is the only broker/account surface. Scheduled feature refreshes bypass
the normal intraday daily-bar cache and always include open positions plus
recent/live hypothesis tickers in addition to the ranked discovery universe.

**Robust live verdict: NO EDGE.** The latest single-split development replay had cached bars for
1,569 of 1,570 frozen-universe names and collapses same-entry-
date stocks into portfolios, applies HAC inference, requires at least 30 entry
dates and 20 names, includes costs, and controls false discovery. Zero
mechanisms survived. All 100 live mechanism records are deprecated; no new-risk
intent may be authored from them.

The 2026-08-02 development replay found one Bonferroni-significant
cross-sectional research artifact (`xs_filing_delta_hi`, 21 sessions, +1.489%
mean market-relative spread across 48 entry dates). It has no per-name hit-rate
posterior and comes from the explicitly reused development holdout. It is
therefore **not live-integration eligible**, does not change the NO_EDGE verdict,
and cannot authorize or size a trade.

Evidence has three operational tiers:

1. **Robust predictive associations:** promotion-eligible only after the locked
   evaluation policy and forward shadow window pass. Current count: zero.
2. **Research hypotheses:** backtests, correlations, episodes, evidence-graph
   links, regime labels, and LLM judgment may generate tests but cannot authorize
   or size new risk.
3. **Closed-loop observations:** `resolve_prediction_backlog` grades each
   forecast from its own timestamp and horizon; `calibrate` updates retained
   posteriors. One forecast contributes at most one total unit of learning
   credit across all linked hypotheses.

`workspaces/trading-intel/config/evaluation_policy.json` labels the reused 2020 holdout as development
only and locks a forward shadow evaluation beginning 2026-08-04 for at least 60
sessions. No production edge claim is permitted before it completes.

The graph layer is an **evidence graph** despite historical table/file names
containing `causal`. Correlation and co-mention produce association/hypothesis
edges only. Nightly rebuilds demote links when their source mechanisms are
deprecated, and link-count growth is never treated as learning quality.

## 12. Invariants (must never be violated)

1. Numbers come from deterministic scripts; LLMs author judgement/prose only.
2. Single canonical store; every transition is audited and `experiment_id`-threaded.
3. An intent reaches the broker **only** via `proposed → critic gate → risk_review
   → Risk approves → executor`. The Risk gate is non-bypassable and fail-closed.
4. Structural/parameter changes are human-gated `rule_proposals`; agents never
   self-approve.
5. Slippage-adjusted realised returns are ground truth for grading.
6. Never `systemctl restart` the gateway; config hot-reloads. Paper account only.
7. Execution/account state is internal-paper-only. No runtime backend switch,
   external broker credential, connector, or fallback may exist.
8. Association is not causation; zero robust survivors means no new risk and
   intentional cash, not a throughput defect.
9. Baseline Critic triage cannot promote. A substantive `reviewed_by=critic`
   review with two developed counterarguments must precede ready state and any
   new prediction.
10. Code changes do not wait for a trading day to reveal regressions. The merge
    gate and nightly learning chain run `scripts/autotrade-preflight.py`: config,
    syntax, doc/architecture semantics, internal-paper exclusivity, all unit
    tests, money-path conservation, and no-write trading-day/holiday/failure
    scenario replay on offline fixtures. Any critical decision-path, owned-ledger,
    or reconciliation-preflight failure disarms execution for that pass.
```
