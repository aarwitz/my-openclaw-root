-- Trading Intelligence — Canonical Shared State Schema
-- Authority: docs/04_SHARED_STATE_SCHEMA.md
-- Engine: SQLite (WAL recommended)
-- Effective: 2026-05-28

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
INSERT OR IGNORE INTO meta(key, value) VALUES ('_schema_version', '2');
INSERT OR IGNORE INTO meta(key, value) VALUES ('_effective_date', '2026-05-28');

-- ----------------------------------------------------------------------------
-- Hypotheses
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS hypotheses (
  id                        TEXT PRIMARY KEY,
  created_at                TEXT NOT NULL,
  created_by                TEXT NOT NULL CHECK (created_by IN ('researcher','quant','critic','risk','trader','executor','archivist','developer','overseer','bessent','human')),
  tickers                   TEXT NOT NULL,                    -- JSON array
  thesis_summary            TEXT NOT NULL,
  state                     TEXT NOT NULL CHECK (state IN ('raw','scored','challenged','ready','active','dormant','resolved','retired')),
  confidence                TEXT CHECK (confidence IN ('low','medium','high')),
  time_horizon              TEXT,
  quant_score               REAL,
  scored_at                 TEXT,
  edge_decay_monthly_pct    REAL,
  last_critic_review_at     TEXT,
  resolved_at               TEXT,
  resolved_state            TEXT CHECK (resolved_state IN ('correct_right_reasons','correct_wrong_reasons','wrong') OR resolved_state IS NULL),
  archivist_grade           TEXT,
  rationale_concise         TEXT CHECK (rationale_concise IS NULL OR length(rationale_concise) <= 500),
  journal_ref               TEXT
);
CREATE INDEX IF NOT EXISTS idx_hypotheses_state       ON hypotheses(state);
CREATE INDEX IF NOT EXISTS idx_hypotheses_resolved_at ON hypotheses(resolved_at);

-- ----------------------------------------------------------------------------
-- Hypothesis Evidence
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS hypothesis_evidence (
  id              TEXT PRIMARY KEY,
  hypothesis_id   TEXT NOT NULL REFERENCES hypotheses(id) ON DELETE CASCADE,
  indicator       TEXT NOT NULL,
  value           TEXT NOT NULL,
  source          TEXT NOT NULL,
  source_url      TEXT,
  retrieved_at    TEXT NOT NULL,
  released_at     TEXT,
  as_of           TEXT,
  vintage         TEXT,
  signal_type     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_hypothesis ON hypothesis_evidence(hypothesis_id);
CREATE INDEX IF NOT EXISTS idx_evidence_signal     ON hypothesis_evidence(signal_type, hypothesis_id);

-- ----------------------------------------------------------------------------
-- Falsifier Signals
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS falsifier_signals (
  id                 TEXT PRIMARY KEY,
  hypothesis_id      TEXT NOT NULL REFERENCES hypotheses(id) ON DELETE CASCADE,
  condition          TEXT NOT NULL,
  monitor_frequency  TEXT,
  current_status     TEXT NOT NULL CHECK (current_status IN ('no_signal','monitoring','warning','broken')),
  updated_at         TEXT NOT NULL,
  source_ref         TEXT
);
CREATE INDEX IF NOT EXISTS idx_falsifier_hyp_status ON falsifier_signals(hypothesis_id, current_status);

-- ----------------------------------------------------------------------------
-- Expression Candidates
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS expression_candidates (
  id                   TEXT PRIMARY KEY,
  hypothesis_id        TEXT NOT NULL REFERENCES hypotheses(id) ON DELETE CASCADE,
  vehicle              TEXT NOT NULL CHECK (vehicle IN ('direct_equity','etf','leaps','short_options','competitor_short','pair_trade')),
  ticker               TEXT NOT NULL,
  option_contract      TEXT,
  event_date           TEXT,
  conviction_weight    REAL CHECK (conviction_weight IS NULL OR (conviction_weight >= 0 AND conviction_weight <= 1)),
  quant_rationale      TEXT CHECK (quant_rationale IS NULL OR length(quant_rationale) <= 500),
  recommended          INTEGER NOT NULL DEFAULT 0 CHECK (recommended IN (0,1)),
  score_json           TEXT,
  created_at           TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_expr_hyp ON expression_candidates(hypothesis_id);

-- ----------------------------------------------------------------------------
-- Regime
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS regime (
  id                TEXT PRIMARY KEY,
  determined_at     TEXT NOT NULL,
  determined_by     TEXT NOT NULL DEFAULT 'quant',
  current           TEXT NOT NULL CHECK (current IN ('risk_on','neutral','caution','risk_off','crisis')),
  signals_json      TEXT,
  implications_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_regime_determined_at ON regime(determined_at);

-- ----------------------------------------------------------------------------
-- Trade Intents
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS trade_intents (
  id                       TEXT PRIMARY KEY,
  hypothesis_id            TEXT NOT NULL REFERENCES hypotheses(id),
  expression_candidate_id  TEXT NOT NULL REFERENCES expression_candidates(id),
  created_by               TEXT NOT NULL,
  created_at               TEXT NOT NULL,
  action                   TEXT NOT NULL CHECK (action IN ('open','add','trim','exit','rotate')),
  tranche_type             TEXT CHECK (tranche_type IN ('starter','confirmation_add','conviction_add','max_conviction') OR tranche_type IS NULL),
  ticker                   TEXT NOT NULL,
  vehicle                  TEXT NOT NULL,
  size                     REAL NOT NULL,
  entry_price_target       TEXT,
  stop_rule                TEXT,
  time_horizon             TEXT,
  triggered_by             TEXT,
  edge_scorecard_json      TEXT,
  evidence_freshness_status TEXT CHECK (evidence_freshness_status IN ('pass','fail') OR evidence_freshness_status IS NULL),
  factor_overlap_status    TEXT CHECK (factor_overlap_status IN ('pass','fail') OR factor_overlap_status IS NULL),
  provenance_completeness_pct REAL,
  counterargument_quality_score REAL,
  explainability_status    TEXT CHECK (explainability_status IN ('pass','fail') OR explainability_status IS NULL),
  experiment_id            TEXT,
  max_fillable_size        REAL,
  modeled_slippage_bps     REAL,
  modeled_fill_price       REAL,
  state                    TEXT NOT NULL CHECK (state IN ('proposed','critic_review','risk_review','approved','blocked','submitted','filled','partial','canceled','rejected')),
  blocked_reason           TEXT,
  submitted_at             TEXT,
  executed_at              TEXT,
  actual_price             REAL,
  actual_size              REAL,
  broker_order_id          TEXT,
  direction                TEXT NOT NULL DEFAULT 'long'  -- long|short (migration 0013)
);
CREATE INDEX IF NOT EXISTS idx_intent_state ON trade_intents(state);
CREATE INDEX IF NOT EXISTS idx_intent_hyp   ON trade_intents(hypothesis_id);
CREATE INDEX IF NOT EXISTS idx_intent_experiment_state ON trade_intents(experiment_id, state);

-- ----------------------------------------------------------------------------
-- Critic Reviews
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS critic_reviews (
  id                          TEXT PRIMARY KEY,
  target_type                 TEXT NOT NULL CHECK (target_type IN ('hypothesis','trade_intent')),
  target_id                   TEXT NOT NULL,
  reviewed_at                 TEXT NOT NULL,
  reviewed_by                 TEXT NOT NULL DEFAULT 'critic',
  challenges_json             TEXT NOT NULL,
  all_challenges_addressed    INTEGER NOT NULL CHECK (all_challenges_addressed IN (0,1))
);
CREATE INDEX IF NOT EXISTS idx_critic_target ON critic_reviews(target_type, target_id);

-- ----------------------------------------------------------------------------
-- Risk Reviews (intent->order gate: sizing/exposure/concentration/correlation/
--   drawdown limits + VETO authority, owned by the `risk` agent)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS risk_reviews (
  id                  TEXT PRIMARY KEY,
  target_type         TEXT NOT NULL CHECK (target_type IN ('trade_intent','portfolio')),
  target_id           TEXT NOT NULL,
  reviewed_at         TEXT NOT NULL,
  reviewed_by         TEXT NOT NULL DEFAULT 'risk',
  verdict             TEXT NOT NULL CHECK (verdict IN ('approved','resized','blocked')),
  approved_size       REAL,
  limits_json         TEXT NOT NULL,
  breaches_json       TEXT,
  rationale_concise   TEXT CHECK (rationale_concise IS NULL OR length(rationale_concise) <= 500),
  experiment_id       TEXT
);
CREATE INDEX IF NOT EXISTS idx_risk_target ON risk_reviews(target_type, target_id);

-- ----------------------------------------------------------------------------
-- Orders (mirror of Alpaca order events)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS orders (
  broker_order_id   TEXT PRIMARY KEY,
  trade_intent_id   TEXT NOT NULL REFERENCES trade_intents(id),
  symbol            TEXT NOT NULL,
  side              TEXT NOT NULL CHECK (side IN ('buy','sell')),
  qty               REAL NOT NULL,
  type              TEXT NOT NULL CHECK (type IN ('market','limit','stop','stop_limit')),
  limit_price       REAL,
  status            TEXT NOT NULL,
  submitted_at      TEXT NOT NULL,
  filled_at         TEXT,
  avg_fill_price    REAL,
  raw_payload_path  TEXT,
  book              TEXT NOT NULL DEFAULT 'desk'   -- 0014: parallel paper books
);
CREATE INDEX IF NOT EXISTS idx_orders_intent ON orders(trade_intent_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);

-- ----------------------------------------------------------------------------
-- Positions and Tranches
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS positions (
  id                       TEXT PRIMARY KEY,
  hypothesis_id            TEXT NOT NULL REFERENCES hypotheses(id),
  ticker                   TEXT NOT NULL,
  vehicle                  TEXT NOT NULL,
  qty                      REAL NOT NULL DEFAULT 0,
  cost_basis               REAL NOT NULL DEFAULT 0,
  current_price            REAL,
  current_value            REAL,
  unrealized_pnl_pct       REAL,
  pnl_ideal                REAL,
  pnl_slippage_adjusted    REAL,
  regime_at_first_open     TEXT,
  state                    TEXT NOT NULL CHECK (state IN ('opening','open','scaling','trimming','closing','closed')),
  opened_at                TEXT NOT NULL,
  closed_at                TEXT,
  book                     TEXT NOT NULL DEFAULT 'desk'   -- 0014: parallel paper books
);
CREATE INDEX IF NOT EXISTS idx_positions_hyp_state ON positions(hypothesis_id, state);

CREATE TABLE IF NOT EXISTS tranches (
  id                TEXT PRIMARY KEY,
  position_id       TEXT NOT NULL REFERENCES positions(id) ON DELETE CASCADE,
  trade_intent_id   TEXT NOT NULL REFERENCES trade_intents(id),
  tranche_type      TEXT NOT NULL,
  qty               REAL NOT NULL,
  entry_price       REAL NOT NULL,
  entry_at          TEXT NOT NULL,
  exit_price        REAL,
  exit_at           TEXT,
  exit_reason       TEXT,
  return_pct        REAL
);
CREATE INDEX IF NOT EXISTS idx_tranches_position ON tranches(position_id);

-- ----------------------------------------------------------------------------
-- System Pauses
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS system_pauses (
  id              TEXT PRIMARY KEY,
  scope           TEXT NOT NULL CHECK (scope IN ('new_entries_only','adds_only','shorts_only','exits_trims_only','full_system')),
  reason          TEXT NOT NULL,
  started_at      TEXT NOT NULL,
  ended_at        TEXT,
  source_actor    TEXT NOT NULL,
  block_list_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_pauses_active ON system_pauses(scope, ended_at);

-- ----------------------------------------------------------------------------
-- Reconciliation Runs
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS reconciliation_runs (
  id                TEXT PRIMARY KEY,
  started_at        TEXT NOT NULL,
  finished_at       TEXT,
  divergences_json  TEXT,
  resolved          INTEGER NOT NULL DEFAULT 0 CHECK (resolved IN (0,1))
);

-- ----------------------------------------------------------------------------
-- Postmortems and Patterns
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS postmortems (
  id                          TEXT PRIMARY KEY,
  hypothesis_id               TEXT NOT NULL REFERENCES hypotheses(id),
  resolved_at                 TEXT NOT NULL,
  grade                       TEXT,
  thesis_analysis_json        TEXT,
  expression_analysis_json    TEXT,
  critic_analysis_json        TEXT,
  researcher_analysis_json    TEXT,
  external_mechanism_check_json TEXT,
  experiment_id               TEXT
);

CREATE TABLE IF NOT EXISTS patterns (
  id                    TEXT PRIMARY KEY,
  created_at            TEXT NOT NULL,
  pattern               TEXT NOT NULL,
  confidence            TEXT CHECK (confidence IN ('low','medium','high')),
  applies_to_json       TEXT,
  source_postmortem_id  TEXT REFERENCES postmortems(id),
  external_validation_status TEXT CHECK (external_validation_status IN ('pass','fail','unknown') OR external_validation_status IS NULL),
  experiment_id         TEXT
);

-- ----------------------------------------------------------------------------
-- Validation Cases and Regime Rules
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS validation_cases (
  id                    TEXT PRIMARY KEY,
  masked_case_json      TEXT NOT NULL,
  case_class            TEXT NOT NULL CHECK (case_class IN ('winner','negative_control','post_cutoff')),
  fake_date_variant     TEXT,
  model_decision_json   TEXT,
  resolved_outcome_json TEXT,
  passed                INTEGER NOT NULL DEFAULT 0 CHECK (passed IN (0,1)),
  created_at            TEXT NOT NULL,
  experiment_id         TEXT
);
CREATE INDEX IF NOT EXISTS idx_validation_class_passed ON validation_cases(case_class, passed);

CREATE TABLE IF NOT EXISTS regime_rules (
  id              TEXT PRIMARY KEY,
  rule_version    TEXT NOT NULL,
  effective_at    TEXT NOT NULL,
  thresholds_json TEXT NOT NULL,
  notes           TEXT,
  experiment_id   TEXT
);

-- ----------------------------------------------------------------------------
-- Audits (append-only)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audits (
  id                  TEXT PRIMARY KEY,
  timestamp           TEXT NOT NULL,
  actor               TEXT NOT NULL CHECK (actor IN ('researcher','quant','critic','risk','trader','executor','archivist','developer','overseer','bessent','human','system')),
  entity_type         TEXT NOT NULL,
  entity_id           TEXT NOT NULL,
  action              TEXT NOT NULL,
  before_state        TEXT,
  after_state         TEXT,
  rationale_concise   TEXT CHECK (rationale_concise IS NULL OR length(rationale_concise) <= 500),
  journal_ref         TEXT,
  experiment_id       TEXT
);
CREATE INDEX IF NOT EXISTS idx_audits_entity ON audits(entity_type, entity_id, timestamp);

-- ----------------------------------------------------------------------------
-- Rule proposals (closed-loop self-improvement)
--   Archivist (or any agent) proposes; Aaron approves via Druck; Bessent applies.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS rule_proposals (
  id                 TEXT PRIMARY KEY,
  created_at         TEXT NOT NULL,
  proposer           TEXT NOT NULL CHECK (proposer IN ('researcher','quant','critic','risk','archivist','trader','executor','developer','overseer','bessent','human','system')),
  target_artifact    TEXT NOT NULL,
  current_value      TEXT,
  proposed_value     TEXT NOT NULL,
  rationale          TEXT NOT NULL,
  evidence_refs_json TEXT,
  status             TEXT NOT NULL CHECK (status IN ('proposed','approved','rejected','applied','superseded')),
  decided_by         TEXT,
  decided_at         TEXT,
  applied_at         TEXT,
  experiment_id      TEXT
);
CREATE INDEX IF NOT EXISTS idx_rule_proposals_status ON rule_proposals(status, created_at);

-- ----------------------------------------------------------------------------
-- Experiments (A/B tracking for rule changes and pipeline tweaks)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS experiments (
  id            TEXT PRIMARY KEY,
  started_at    TEXT NOT NULL,
  ended_at      TEXT,
  scope         TEXT NOT NULL,
  hypothesis    TEXT NOT NULL,
  outcome_json  TEXT,
  decided_by    TEXT
);
CREATE INDEX IF NOT EXISTS idx_experiments_scope_started ON experiments(scope, started_at);

-- ----------------------------------------------------------------------------
-- Attribution (per-trade realized edge vs SPY, per horizon)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS attribution (
  id                          TEXT PRIMARY KEY,
  hypothesis_id               TEXT REFERENCES hypotheses(id),
  position_id                 TEXT REFERENCES positions(id),
  horizon                     TEXT NOT NULL CHECK (horizon IN ('intraday','swing_1_5d','position_1_4w','trend_1_3m','long_6m_plus')),
  opened_at                   TEXT NOT NULL,
  closed_at                   TEXT,
  portfolio_return_pct        REAL,
  spy_return_pct              REAL,
  realized_edge_vs_spy_bps    REAL,
  attribution_json            TEXT,
  computed_at                 TEXT NOT NULL,
  experiment_id               TEXT
);
CREATE INDEX IF NOT EXISTS idx_attribution_horizon_closed ON attribution(horizon, closed_at);
CREATE INDEX IF NOT EXISTS idx_attribution_hypothesis     ON attribution(hypothesis_id);

-- ----------------------------------------------------------------------------
-- Benchmarks (rolling portfolio vs SPY per horizon bucket)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS benchmarks (
  id                     TEXT PRIMARY KEY,
  captured_at            TEXT NOT NULL,
  horizon                TEXT NOT NULL CHECK (horizon IN ('intraday','swing_1_5d','position_1_4w','trend_1_3m','long_6m_plus','all')),
  period_start           TEXT NOT NULL,
  period_end             TEXT NOT NULL,
  portfolio_return_pct   REAL,
  spy_return_pct         REAL,
  alpha_pct              REAL,
  sharpe_estimate        REAL,
  turnover_pct           REAL,
  source_run_id          TEXT
);
CREATE INDEX IF NOT EXISTS idx_benchmarks_horizon_captured ON benchmarks(horizon, captured_at);

-- ----------------------------------------------------------------------------
-- Portfolio snapshots (equity curve + SPY reference)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
  id                 TEXT PRIMARY KEY,
  captured_at        TEXT NOT NULL,
  equity             REAL NOT NULL,
  last_equity        REAL,
  day_pl             REAL,
  cash               REAL,
  buying_power       REAL,
  spy_close          REAL,
  spy_as_of          TEXT,
  account_status     TEXT,
  source             TEXT NOT NULL DEFAULT 'alpaca_paper'
);
CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_captured_at ON portfolio_snapshots(captured_at);

-- ----------------------------------------------------------------------------
-- Regime current view convention
-- ----------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS regime_current AS
SELECT r.*
FROM regime r
WHERE r.determined_at = (SELECT MAX(determined_at) FROM regime);

-- ============================================================================
-- WORLD MODEL & CALIBRATION LAYER (schema v8)
--   Market-knowledge self-improvement: a probabilistic causal world model that
--   learns from outcomes (trades) and from the market itself (debriefs).
-- ============================================================================

-- ----------------------------------------------------------------------------
-- mechanisms (the world model — causal links with Beta-distributed strength)
--   antecedent -> transmission_chain -> consequent, with a Beta(alpha,beta)
--   posterior that updates from outcomes and decays with a half-life.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mechanisms (
  id                      TEXT PRIMARY KEY,
  created_at              TEXT NOT NULL,
  created_by              TEXT NOT NULL CHECK (created_by IN ('researcher','quant','critic','risk','archivist','trader','executor','developer','overseer','human','system')),
  name                    TEXT NOT NULL,
  antecedent_class        TEXT NOT NULL,
  transmission_chain_json TEXT NOT NULL,
  consequent_class        TEXT NOT NULL,
  direction               TEXT NOT NULL CHECK (direction IN ('long','short','neutral','risk_off','risk_on')),
  horizon                 TEXT CHECK (horizon IN ('intraday','swing_1_5d','position_1_4w','trend_1_3m','long_6m_plus') OR horizon IS NULL),
  regime_context          TEXT,
  prior_alpha             REAL NOT NULL DEFAULT 1.0,
  prior_beta              REAL NOT NULL DEFAULT 1.0,
  observed_hits           REAL NOT NULL DEFAULT 0,
  observed_misses         REAL NOT NULL DEFAULT 0,
  posterior_mean          REAL,
  posterior_ci_low        REAL,
  posterior_ci_high       REAL,
  half_life_days          REAL NOT NULL DEFAULT 180,
  last_observed_at        TEXT,
  status                  TEXT NOT NULL CHECK (status IN ('candidate','active','deprecated','crowded')),
  notes                   TEXT,
  experiment_id           TEXT
);
CREATE INDEX IF NOT EXISTS idx_mechanisms_status     ON mechanisms(status);
CREATE INDEX IF NOT EXISTS idx_mechanisms_antecedent ON mechanisms(antecedent_class);

-- ----------------------------------------------------------------------------
-- mechanism_observations (append-only evidence ledger; Beta recomputed from
--   these rows with half-life decay by calibrate.py)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS mechanism_observations (
  id                  TEXT PRIMARY KEY,
  mechanism_id        TEXT NOT NULL REFERENCES mechanisms(id),
  observed_at         TEXT NOT NULL,
  source_type         TEXT NOT NULL CHECK (source_type IN ('prediction','market_event','manual')),
  source_id           TEXT,
  outcome             TEXT NOT NULL CHECK (outcome IN ('hit','miss','partial')),
  weight              REAL NOT NULL DEFAULT 1.0,
  regime_at_obs       TEXT,
  notes               TEXT,
  experiment_id       TEXT
);
CREATE INDEX IF NOT EXISTS idx_mech_obs_mechanism ON mechanism_observations(mechanism_id, observed_at);

-- ----------------------------------------------------------------------------
-- predictions (probabilistic hypothesis call + calibration record)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS predictions (
  id                    TEXT PRIMARY KEY,
  hypothesis_id         TEXT NOT NULL REFERENCES hypotheses(id),
  predicted_at          TEXT NOT NULL,
  predicted_by          TEXT NOT NULL DEFAULT 'quant',
  horizon               TEXT NOT NULL CHECK (horizon IN ('intraday','swing_1_5d','position_1_4w','trend_1_3m','long_6m_plus')),
  p_correct             REAL NOT NULL CHECK (p_correct >= 0 AND p_correct <= 1),
  return_p10            REAL,
  return_p50            REAL,
  return_p90            REAL,
  mechanism_ids_json    TEXT,
  regime_at_prediction  TEXT,
  evidence_quality      REAL,
  prior_log_odds        REAL,
  realized_outcome      TEXT CHECK (realized_outcome IN ('correct','incorrect','inconclusive') OR realized_outcome IS NULL),
  realized_return_pct   REAL,
  brier_component       REAL,
  resolved_at           TEXT,
  experiment_id         TEXT
);
CREATE INDEX IF NOT EXISTS idx_predictions_hyp      ON predictions(hypothesis_id);
CREATE INDEX IF NOT EXISTS idx_predictions_resolved ON predictions(resolved_at);

-- ----------------------------------------------------------------------------
-- market_events (daily market debrief — learn from the market, trade or not)
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS market_events (
  id                            TEXT PRIMARY KEY,
  event_date                    TEXT NOT NULL,
  created_at                    TEXT NOT NULL,
  created_by                    TEXT NOT NULL DEFAULT 'archivist',
  headline                      TEXT NOT NULL,
  catalyst_class                TEXT NOT NULL CHECK (catalyst_class IN ('macro_release','geopolitical','earnings','policy','liquidity','technical','other')),
  observed_moves_json           TEXT NOT NULL,
  surprise_vs_expectation       TEXT,
  attributed_mechanism_ids_json TEXT,
  our_pnl_that_day              REAL,
  our_exposure_alignment        TEXT CHECK (our_exposure_alignment IN ('benefited','suffered','neutral','flat') OR our_exposure_alignment IS NULL),
  lesson_concise                TEXT CHECK (lesson_concise IS NULL OR length(lesson_concise) <= 800),
  primary_source_refs_json      TEXT,
  experiment_id                 TEXT
);
CREATE INDEX IF NOT EXISTS idx_market_events_date ON market_events(event_date);

-- ----------------------------------------------------------------------------
-- Internal paper engine (0014, docs/07): parallel books, own equity curve,
-- audited corporate actions.
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS sim_accounts (
  book           TEXT PRIMARY KEY,
  cash           REAL NOT NULL,
  starting_cash  REAL NOT NULL,
  created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS book_equity (
  book    TEXT NOT NULL,
  date    TEXT NOT NULL,
  equity  REAL NOT NULL,
  cash    REAL NOT NULL,
  PRIMARY KEY (book, date)
);

CREATE TABLE IF NOT EXISTS sim_corporate_actions (
  id        TEXT PRIMARY KEY,
  book      TEXT NOT NULL,
  ticker    TEXT NOT NULL,
  action    TEXT NOT NULL CHECK (action IN ('split','dividend')),
  ratio     REAL,
  amount    REAL,
  ex_date   TEXT NOT NULL,
  applied_at TEXT NOT NULL,
  UNIQUE (book, ticker, action, ex_date)
);

-- Sim engine gets its OWN position/order tables: desk queries read
-- positions/orders unfiltered, so sim rows must never share those tables
-- (the book columns above remain for the eventual desk cutover).
CREATE TABLE IF NOT EXISTS sim_positions (
  id            TEXT PRIMARY KEY,
  book          TEXT NOT NULL,
  ticker        TEXT NOT NULL,
  qty           REAL NOT NULL,
  cost_basis    REAL NOT NULL DEFAULT 0,
  current_price REAL,
  current_value REAL,
  state         TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open','closed')),
  opened_at     TEXT NOT NULL,
  closed_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_sim_positions_book ON sim_positions(book, state);

CREATE TABLE IF NOT EXISTS sim_orders (
  order_id       TEXT PRIMARY KEY,
  book           TEXT NOT NULL,
  symbol         TEXT NOT NULL,
  side           TEXT NOT NULL,
  qty            REAL NOT NULL,
  fill_price     REAL NOT NULL,
  source         TEXT NOT NULL,
  filled_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sim_orders_book ON sim_orders(book, filled_at);

-- 0015_book_equity_intraday — intraday equity samples per book (D53).
-- book_equity stays the daily EOD curve; this powers the 1D/1W chart ranges.
CREATE TABLE IF NOT EXISTS book_equity_intraday (
  book   TEXT NOT NULL,
  ts     INTEGER NOT NULL,          -- epoch ms
  equity REAL NOT NULL,
  PRIMARY KEY (book, ts)
);
