# AutoTrade — Canonical System Architecture

> **Status:** AUTHORITATIVE. This is the single source of truth for the AutoTrade
> agentic trading platform that runs on the OpenClaw gateway. Where any other
> document disagrees with this one, **this document wins**. The formerly stale
> docs (`ARCHITECTURE.md`, `workspaces/trading-intel/FULL_DESIGN_ASCII.md`,
> `workspaces/trading-intel/docs/02_ARCHITECTURE.md`) were **archived 2026-07-02**
> to `archive/docs-retired-20260702/`.
>
> **Topology:** v5 · **DB schema/migrations:** through 0022 · **Last reconciled:** 2026-07-30

---

## 1. What this system is

AutoTrade is a **self-improving, agentic paper-trading desk**. A team of
specialised LLM agents runs an institutional-style research → decision →
execution → learning loop against an owned SQLite **paper simulator**, with the explicit
goals of (a) beating the S&P on a risk-adjusted basis and (b) making the agents'
decision process **visible** in the AutoTrade web app and over Telegram.

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
  sub), **no API keys, no per-agent model selection**. `openai-codex` refresh
  tokens are single-use and fragile; backups rotate in
  `credentials/token-backups/`.
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
> `jerry` and `resi` also exist in `openclaw.json` but are **not** part of the AutoTrade
> desk (`jerry` = default assistant, containerized with a `/home/aaron/repos:rw` bind mount;
> `resi`/AutoTap untouched).

---

## 4. Data store

Single SQLite database: `~/.openclaw/state/trading-intel.sqlite` (WAL mode),
**schema v8**. Source-of-truth DDL: `workspaces/trading-intel/sql/schema.sql`;
numbered migrations under `workspaces/trading-intel/sql/migrations/`.

Core pipeline tables: `hypotheses`, `hypothesis_evidence`, `regime`,
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
  → author_intents       trader/scripts/author_intents.py      → trade_intents (adaptive deployment
                                                               governor + fractional-Kelly, state=proposed)
  → gate_evaluator       trading-intel/scripts/gate_evaluator.py  proposed|critic_review → risk_review | blocked
  → risk_gate            risk/scripts/gate_risk_intents.py     risk_review → approved|blocked (size capped)
  → execute_intent       executor/scripts/execute_intent.py    approved → submitted (internal ledger)
  → sync_fills           executor/scripts/sync_fills.py        broker truth per order id → orders (fill
                                                               price/time), intents (actuals), positions
                                                               (real hypothesis lineage; added 2026-07-06)
  → reconcile            executor/scripts/reconcile.py          fills vs DB (placeholder repair = last resort)
  → sim_mark             executor/scripts/sim_broker.py mark   → book_equity + SGOV-proxy cash-yield accrual
                                                               + book_return_attribution (trading vs yield, D55)
  → scoreboard           trading-intel/scripts/benchmark_scoreboard.py → benchmarks rows (vs SPY, all horizons)
  → macro_seed/actuals   trading-intel/scripts/macro_calendar.py → macro_releases (+ surprise → market_event)
  → capital_efficiency   trading-intel/scripts/capital_efficiency_audit.py → capital_efficiency_snapshots
                                                               (ranked dollar bottlenecks, D55)
  → snapshot (+overlay)  developer/scripts/snapshot_builder.py → app data.json
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

Two 2026-07-02 refinements (schema v13, D47/D48 in `DECISION_LOG.md`):
- **Risk-reducing intents (`exit`/`trim`) face only sanity gates** — never
  idea-quality gates (an exit blocked on stale evidence traps a loser).
- **Shorts execute end-to-end**: `trade_intents.direction` (`long`|`short`,
  migration 0013) → executor submits sell-to-open / buy-to-cover; on exits the
  actual held-position sign wins. All risk caps apply to abs exposure.

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

### 6.7 Episode library (`episodes`) — named, dated ground truth (schema v9)
The desk learns market structure from a curated library of **real, named,
dated** market episodes (migration `0009`; seeded by
`trading-intel/scripts/seed_episodes.py` from the operator's ground-truth cases).
This **replaces** the abandoned anonymized `validation_corpus` (masking tickers/
dates to "prevent overfitting" destroyed the signal). Overfitting is instead
controlled by **walk-forward discipline**, not anonymization:
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
- **Concurrent names:** ≤ 24 (8→12 per D46, 12→24 per rp-e907106afbfb49a4aff1).
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

- **Five market-time weekday passes** (`OVERSEER-DRIVE-V2`): pre-market 09:00,
  open 09:30, confirmation 11:00, rotation 13:30, close-risk 15:30 ET. Each runs
  the deterministic core, then spawns agents in strict order
  `researcher → quant → critic → trader → risk → executor → archivist` (Codex
  `spawn_agent` + `wait`). After each child result is consumed, overseer must
  explicitly `close_agent` before moving to the next stage rather than relying
  on later archive reaping. Then narrates to Telegram via `druck`.
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
  - **Full deploy (code changes):** `scripts/publish-trader-intel.sh`
    (vite build + `wrangler pages deploy`) or the canonical GitHub `main`
    deployment. The reviewed `data.json` baked into that commit is the app's
    fallback when `/api/trader-data` is unavailable.
- **Live paper-ledger bridge (same-origin Pages Function):**
  - `/api/trader-live` returns internal-paper equity, positions, orders, and
    ledger history from the session-gated `TRADER_DATA` snapshot.
  - The browser never calls OpenClaw, a broker, or a market-data provider.
  - Missing/malformed snapshot data returns a fail-closed error; there is no
    external-broker fallback or broker credential in the Pages runtime.
- **Telegram** narration goes out on the `druck` bot (cron handles routing to
  the group topic / DM). Narration is action-first, source-backed, no tables.
- Any bot that should stay visibly responsive in Telegram needs the
  `group:messaging` capability in its tool allowlist/profile. A routed Telegram
  account with `group:messaging` missing can warn or fail on replies,
  attachments, and thread actions.
- **Backlog visibility (Task Manager):** `poll_priority_queue.py` is a **one-way,
  deterministic** mirror — it reads the overseer's append-only priority queue
  (`state/priority-queue.jsonl`) and creates/updates Task Manager issues for
  visibility. It is the *only* desk-side Task Manager mutation path and it **does not
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
 overseer → priority queue → TM mirror (poll_priority_queue.py, visibility) ; Developer worktrees → PRs
```

- **Knowledge** improves continuously and autonomously (mechanism beliefs).
- **Software** improves through human-gated Developer changes; the deterministic TM
  mirror provides the visible backlog. Self-improvement uses **OpenClaw-native delegation**
  (overseer/developer), **not** an auto-dispatch rail. The legacy
  `dwight-lane-bridge` auto-dispatch is **retired**: its cron entries were
  deleted and its scripts archived to `archive/scripts-retired-20260702/`
  (2026-07-02 prune).

---

## 11. Empirical foundation — feature store, backtesting & mechanism discovery (2026-06-18)

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
- Universe: **1,510 names, all-cap NASDAQ/NYSE (not S&P-restricted) ∪ delisted/failed** names, each
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

**Backtest / discovery engine — `mechanism_backtest.py` + `worldmodel_stats.py`.** Walk-forward,
strictly point-in-time (decision uses `as_of` features + entry on the next bar; outcome graded on
future bars only — **no-look-ahead proven** by a dataset-truncation diff). Streams ticker-by-ticker
(scales to thousands). Rigor controls:
- **non-overlapping** samples (spacing ≥ horizon) — no autocorrelation inflation;
- graded **market-relative vs the empirical base rate** (~0.49 on the broad universe, not 0.5);
- **train/test holdout**; primary significance = one-sided **t-test on net mean-alpha** (catches
  skew/tail edges), hit-rate secondary;
- **Benjamini-Hochberg FDR + Bonferroni** across every (mechanism × horizon) + cross-sectional factor;
- **data-quality / tradability controls**: $5 price floor, $5M dollar-volume floor, per-horizon return
  winsorization, and a round-trip **transaction-cost** model (+short borrow) — alpha is reported **net**.

Informed by **AlphaAgent (arXiv 2502.16789)**: the enemy is *alpha decay* (overfit + crowding); the
generator is regularized toward originality / hypothesis-alignment / complexity, and only OOS+FDR
survivors earn weight. An LLM proposer (richer multi-feature hypotheses) slots in at the generator.

**Calibrated mechanism set — `features.sqlite::calibrated_mechanisms`** (`promote_mechanisms.py`): the
FDR-significant, net-positive-alpha survivors with their measured edge + a provisional world-model
posterior. This is the **bootstrap source** for the live world model.

**LIVE as of 2026-06-18.** `integrate_calibrated.py` (backup-first) reset the hindsight-biased learned
state and replaced the live `mechanisms` table with the **31 calibrated survivors** (active; calibrated
weight encoded in `prior_alpha/prior_beta` with a pseudo-count so `calibrate.py` preserves it and live
outcomes update it additively). Backup: `~/.openclaw/backups/trading-intel-PRE-CALIBRATION-*.sqlite`.

Three loops now close around it:
- **Live learning loop (closed):** `archivist/scripts/grade_outcomes.py` grades matured predictions from
  realized market-relative returns → sets `hypotheses.resolved_state` → `calibrate.py` folds per-mechanism
  observations into the posteriors. Run via the governed `scripts/trader-learn-deterministic.sh`
  (grade_outcomes → calibrate → extract_patterns); the live trading pass is untouched.
- **Deterministic activation:** `signal_scan.py` fires the calibrated mechanisms from each ticker's
  *current* features → ranked conviction (advisory; wiring into live intents is the next gated step).
- **Mechanism discovery (ongoing):** `mechanism_backtest.py` (`gen_candidates` single-feature +
  `gen_multi` economically-aligned 2-feature) keeps proposing candidates under the same OOS+FDR+cost bar;
  survivors promote via `promote_mechanisms.py`. The free-form LLM proposer is the next layer.

**Current scheduling:** the weekday learning chain refreshes point-in-time
features, resolves matured forecasts with `resolve_prediction_backlog.py`, and
recomputes calibration. Signal and proposal jobs may continue collecting
research evidence, but `author_intents.py` fails closed while the robust active
mechanism count is zero. Promotion requires the full clustered/FDR evaluation
and a gated proposal; a single-script p-value cannot promote a mechanism.

---

## 11b. Current state (2026-07-30) — evidence is quarantined until it earns trust

**Data backbone.** Massive is the primary split-adjusted price, snapshot, and
news source. FMP supplies deeper/delisted history and fundamentals; FRED
supplies macro series; EDGAR supplies primary filings. The internal paper
ledger is the only broker/account surface.

**Robust replay verdict: NO EDGE.** The 1,542-name replay collapses same-entry-
date stocks into portfolios, applies HAC inference, requires at least 30 entry
dates and 20 names, includes costs, and controls false discovery. Zero
mechanisms survived. All 100 live mechanism records are deprecated; no new-risk
intent may be authored from them.

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

`config/evaluation_policy.json` labels the reused 2020 holdout as development
only and locks a forward shadow evaluation beginning 2026-08-03 for at least 60
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
```
