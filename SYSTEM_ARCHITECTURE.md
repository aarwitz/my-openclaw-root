# AutoTrade — Canonical System Architecture

> **Status:** AUTHORITATIVE. This is the single source of truth for the AutoTrade
> agentic trading platform that runs on the OpenClaw gateway. Where any other
> document disagrees with this one, **this document wins**. The formerly stale
> docs (`ARCHITECTURE.md`, `workspaces/trading-intel/FULL_DESIGN_ASCII.md`,
> `workspaces/trading-intel/docs/02_ARCHITECTURE.md`) were **archived 2026-07-02**
> to `archive/docs-retired-20260702/`.
>
> **Topology:** v4 · **DB schema:** v11 · **Last reconciled:** 2026-06-17

---

## 1. What this system is

AutoTrade is a **self-improving, agentic paper-trading desk**. A team of
specialised LLM agents runs an institutional-style research → decision →
execution → learning loop against an Alpaca **paper** account, with the explicit
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
| 6 | `executor` | ⚙️ | Alpaca paper order placement + fill reconciliation. |
| 7 | `archivist` | 📚 | **Learning officer.** Daily market debrief; resolves predictions (Brier); updates the world model; drafts gated rule proposals. |
| 8 | `overseer` (CIO) | 🤖 | Cron pipeline orchestrator + app/Telegram chat front door + heartbeat. Orchestrates only — never writes execution state, never edits scripts. |
| 9 | `developer` | 🔧 | Implements software improvements in git worktrees; opens PRs (human-gated). |
|  – | `jerry` | – | Host/container repair (host cron, sudo). Not an in-gateway agent. |

> **Decoupled 2026-06-17:** `dwight` (general dev/PM + code-task dispatcher for broader
> work, e.g. RSL) is **no longer a desk agent**. It still runs in the gateway but is not
> counted, snapshotted, run-controlled, or metered as part of the trading desk. The
> desk's software-improvement backlog is mirrored deterministically (overseer priority
> queue → `poll_priority_queue.py`); the `developer` agent does the desk's code work.
>
> `main` and `resi` also exist in `openclaw.json` but are **not** part of the AutoTrade
> desk (`main` = default assistant; `resi`/AutoTap untouched).

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
  → author_intents       trader/scripts/author_intents.py      → trade_intents (Kelly size, state=proposed)
  → gate_evaluator       trading-intel/scripts/gate_evaluator.py  proposed|critic_review → risk_review | blocked
  → risk_gate            risk/scripts/gate_risk_intents.py     risk_review → approved|blocked (size capped)
  → execute_intent       executor/scripts/execute_intent.py    approved → submitted (Alpaca paper)
  → reconcile            executor/scripts/reconcile.py          fills vs DB
  → scoreboard           trading-intel/scripts/benchmark_scoreboard.py → benchmarks rows (vs SPY, all horizons)
  → macro_seed/actuals   trading-intel/scripts/macro_calendar.py → macro_releases (+ surprise → market_event)
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

This is how AutoTrade learns *why* prices move and improves its probabilistic
beliefs from data. Shared math lives in
`workspaces/trading-intel/scripts/worldmodel.py` (pure stdlib: regularised
incomplete-beta, Beta PPF via bisection, half-life decay, log-odds combination,
return bands, fractional Kelly).

### 6.1 Mechanisms — the causal library (`mechanisms`)
A mechanism is a falsifiable causal claim:

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

> **As of 2026-06-18 these are *seeds*, not canon.** They are now validated (and supplemented by
> machine-discovered mechanisms) against 20 years of point-in-time, survivorship-safe data with FDR
> control — see **§11**. The live world model will be bootstrapped from the resulting
> `calibrated_mechanisms` set, not from hand-authored beliefs.

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
Price/vol/beta: Alpaca bars (Yahoo blocks this host). Risk-free: FRED. Deterministic +
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
covariance/factor view (pure stdlib, returns-based on Alpaca bars, cached):

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

- **AutoTrade web app** (lidi-solutions) reads the snapshot `data.json` written
  each pass: per-agent headline/last-output, hypotheses, intents, orders,
  regime, pipeline health. The decision process is the product.
- **Live market bridge (same-origin Pages Functions):**
  - `/api/trader-live` returns Alpaca-backed live equity/positions/history for
    intraday charting. The browser may poll this endpoint; it must never call
    OpenClaw directly.
  - Required Pages env vars: `ALPACA_API_KEY_ID` (or `ALPACA_KEY_ID`),
    `ALPACA_API_SECRET_KEY` (or `ALPACA_SECRET_KEY`), optional
    `ALPACA_BASE_URL`.
  - Fail-closed behavior is required: when unset/misconfigured, endpoint returns
    JSON `503` and UI falls back to snapshot data.
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

**Data sources & connectors.** `connectors/fmp.py` (FMP **Premium**) is the historical backbone:
deep (20–30yr) **split-adjusted** prices incl. delisted names, quarterly fundamentals + ratios,
analyst estimates, earnings surprise, constituent history, delisted list. **Alpaca remains the LIVE
broker/execution + real-time** feed; FMP supplies history (Alpaca's free IEX is capped ~6yr). FRED =
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

**Now wired into cron (2026-06-18):**
- `overseer-daily-learning-1630-et` (weekday 16:30 ET) — Step 1 also runs `feature_store.py refresh-live`
  (fresh point-in-time features) + `archivist/scripts/grade_outcomes.py` (grade matured trades → set
  `resolved_state`); Step 4 `calibrate.py` folds the outcomes into posteriors. **The learning loop now runs daily.**
- `world-model-signals-0900-et` (weekday 9:00 ET, NEW) — `signals_to_hypotheses.py` turns the calibrated
  mechanisms' top deterministic signals (`signal_scan.scan()`) into RAW hypotheses; the day's passes +
  the non-bypassable **Risk gate** trade them.
- `mechanism-proposer-weekly` (Sun 10:00 ET, NEW) — the researcher proposes new mechanisms, each gated by
  `validate_mechanism.py` (OOS net-alpha screen, p<0.01); survivors require a full FDR backtest + human
  promotion (`integrate_calibrated.py`). Auto-promotion stays human-gated (invariant 4).

**Still open:** turning `signals_to_hypotheses` confidence into native return-aware sizing in `author_intents`;
broadening the daily refresh universe; and tuning proposer cadence. Plan: `~/.claude/plans/vast-wishing-pony.md`.

---

## 11b. Current state (2026-06-18) — backbone, news, evidence tiers

**Data backbone (revised).** **Massive** (=formerly Polygon; upgraded, UNLIMITED calls, base
`api.massive.com`, `apiKey=` param) is now the **price + news** workhorse: 10yr split-adjusted prices
incl. delisted (`_prices` = Massive→FMP(20yr depth)→Alpaca), and historical ticker news back to ~2021
(`_news` = Massive; AI `insights.sentiment` recent-only, so history uses a deterministic finance
lexicon over title+description). **FMP** = fundamentals, insider, analyst ratings, constituents. FRED =
macro; Event Registry = real-time catalysts; Alpaca = live broker. (Alpha Vantage / GDELT no longer
needed.) Connectors: `connectors/{massive,fmp,fred,edgar,eventregistry,alphavantage,gdelt}.py`.

**Calibrated set: 40 live mechanisms** (17 seed + 23 machine-generated, 30 Bonferroni). The system now
sorts evidence into THREE tiers — this is the core design principle:
1. **Validated mechanical edges** (backtested, cost-net, FDR; live + sized): mean-reversion
   (oversold/deep-drawdown), the multi-feature **drawdown-within-uptrend (+5.8%/qtr, strongest)**, value
   (cheap P/E), growth, momentum/trend, vol factor, earnings-beat, **rating-upgrades**, **news-sentiment
   momentum** (`positive_sentiment`).
2. **Rejected as blind rules → handled by LLM judgment**: the **overreaction-contrarian** (bearish-news +
   cheap-for-growth — fails FDR because it also buys value traps like the saaspocalypse names), insider-
   selling, sector-chasing. These run through the **bull/bear debate** (`catalyst-research` agent, the one
   idea adopted from TauricResearch/TradingAgents) + forward learning, NOT as quant mechanisms.
3. The **closed loop** (`grade_outcomes` → `calibrate`) grades all live trades forward into the posteriors.

**Macro findings (mined 2026-06-18).** The jobs/CPI/Fed → rate-move → duration-repricing → tech-down
chain (`rates_up_duration`) was tested via FRED rate/real-yield/curve/credit-spread/VIX features (the
rate move is the transmission signal). Result: **NOT a robust mechanical edge over 2020–2026** — real in
2022 but the regime flipped (2023–26 was AI-narrative-driven, tech rose regardless of rates) → it joins
tier-2 (regime-dependent, judgment). What DID survive (now live, 47 mechanisms): **VIX-capitulation**
(`vix_high + deep_drawdown → long`, +3%/qtr, robust all horizons) and `vix_high → long`.

**Regime layer (LLM judgment for the regime-dependent chains).** `regime_brief.py` classifies the current
macro regime from FRED (rates rising/falling/dominant, risk_off, curve, credit, VIX) and lists the active
playbooks; it's Step 0 of the `catalyst-research` agent so the bull/bear **judge applies the rate→duration
and risk-off chains ONLY when the regime warrants** (e.g. today = `neutral_narrative_driven` → it explicitly
does NOT apply the rate→tech chain). Validated mechanical edges trade regardless; regime-dependent chains are
gated on the regime. This is the operating principle: **mechanical where proven, LLM judgment where the edge is regime-dependent.**

## 11c. Conviction-quality improvements (2026-06-18) — 5 weaknesses the system surfaced about itself

After using the desk to read the live tape, five weaknesses were fixed. `signal_scan` conviction now
weights each fired mechanism by **redundancy_weight × regime_fit**, ranks names by **correlation-novelty**,
and the learning loop blends backtest + live evidence:

1. **Cross-name correlation (novelty).** `signal_scan._correlation_adjust`: a name's conviction is
   discounted by its return-correlation to higher-ranked candidates (novelty = 1−max corr); reports
   EFFECTIVE independent bets. Data correction: large-caps are only ~0.45 correlated even within a sector
   (not "one bet") → graded discount, not binary clusters. `signals_to_hypotheses` skips novelty<0.5.
2. **Within-name mechanism redundancy.** `mechanism_correlation.py` → `mechanism_clusters` table
   (redundancy_weight = 1/cluster_size from Jaccard>0.5 of firing vectors). De-dups same-theme stacking.
   Data correction: redundancy is only ~1.21× at 0.5 (the families fire on different cells), not 10×.
3. **Regime-conditional weights.** `mechanism_regime.py` → `mechanism_regime` table (per-mechanism alpha
   by VIX/rate regime). signal_scan `regime_fit` = alpha(current regime)/alpha(ALL). **Finding: the
   growth-vs-value rotation is rate-regime-driven** — growth/momentum/high-vol mechanisms pay when rates
   fall, value when rates rise. This is the operator's jobs→rates→duration chain, correctly placed as a
   *conditioner* (it failed FDR as a standalone factor). The QUANT layer now weights by regime too.
4. **Two learning rates.** `calibrate.py` PRIOR_DECAY_N: the backtest prior shrinks as the desk's own
   live observations accrue (shrink = 30/(30+n_live)) → live P&L progressively overrides the backtest.
5. **Short interest (last data gap closed).** Massive FINRA `/stocks/v1/short-interest` →
   `feature_store._short_interest` (days_to_cover, short_int_chg_2m; stamped settlement+8 business days
   to respect FINRA's dissemination lag). New crowded_short/squeeze mechanisms in the engine.

New tables: `mechanism_clusters`, `mechanism_regime` (regenerate via their scripts).

## 12. Invariants (must never be violated)

1. Numbers come from deterministic scripts; LLMs author judgement/prose only.
2. Single canonical store; every transition is audited and `experiment_id`-threaded.
3. An intent reaches the broker **only** via `proposed → critic gate → risk_review
   → Risk approves → executor`. The Risk gate is non-bypassable and fail-closed.
4. Structural/parameter changes are human-gated `rule_proposals`; agents never
   self-approve.
5. Slippage-adjusted realised returns are ground truth for grading.
6. Never `systemctl restart` the gateway; config hot-reloads. Paper account only.
```
