-- 0008_world_model_calibration.sql
--
-- World Model & Calibration layer (schema v8).
--
-- Adds the market-knowledge self-improvement substrate the desk learns from as
-- it trades and as it observes the market (even on no-trade days):
--
--   1. mechanisms             — the WORLD MODEL: a persistent library of causal
--                               links (antecedent -> transmission -> consequent)
--                               whose strength is a Beta(alpha,beta) posterior
--                               that updates from outcomes, with half-life decay
--                               for crowding/regime drift.
--   2. mechanism_observations — append-only ledger of every hit/miss applied to a
--                               mechanism (from a resolved prediction, a market
--                               debrief, or a manual call). calibrate.py recomputes
--                               each mechanism's Beta from these with time decay.
--   3. predictions            — per-hypothesis probabilistic call: calibrated
--                               p_correct + a P10/P50/P90 return band, the leaning
--                               mechanisms, and (on resolution) the realized
--                               outcome + Brier component for calibration.
--   4. market_events          — daily market debrief: what moved, why, which
--                               mechanisms fired, our P&L alignment, and the
--                               concise lesson. Captures macro/geopolitical lessons
--                               structurally regardless of whether we traded.
--
-- All four are NEW tables (CREATE TABLE IF NOT EXISTS); no existing table is
-- rebuilt, so this migration is fully idempotent and non-destructive. A full DB
-- backup is taken by the operator before applying.
--
-- Apply: python3 -c "import sqlite3;sqlite3.connect('state/trading-intel.sqlite').executescript(open('workspaces/trading-intel/sql/migrations/0008_world_model_calibration.sql').read())"

PRAGMA foreign_keys = OFF;

BEGIN TRANSACTION;

-- ---------------------------------------------------------------------------
-- mechanisms  (the world model — causal links with Beta-distributed strength)
-- ---------------------------------------------------------------------------
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
  -- Beta(alpha,beta) posterior over P(mechanism fires & resolves as predicted)
  prior_alpha             REAL NOT NULL DEFAULT 1.0,
  prior_beta              REAL NOT NULL DEFAULT 1.0,
  observed_hits           REAL NOT NULL DEFAULT 0,   -- decayed effective hit weight (recomputed)
  observed_misses         REAL NOT NULL DEFAULT 0,   -- decayed effective miss weight (recomputed)
  posterior_mean          REAL,
  posterior_ci_low        REAL,
  posterior_ci_high       REAL,
  half_life_days          REAL NOT NULL DEFAULT 180, -- decay/crowding: down-weight old observations
  last_observed_at        TEXT,
  status                  TEXT NOT NULL CHECK (status IN ('candidate','active','deprecated','crowded')),
  notes                   TEXT,
  experiment_id           TEXT
);
CREATE INDEX IF NOT EXISTS idx_mechanisms_status     ON mechanisms(status);
CREATE INDEX IF NOT EXISTS idx_mechanisms_antecedent ON mechanisms(antecedent_class);

-- ---------------------------------------------------------------------------
-- mechanism_observations  (append-only evidence ledger; Beta is recomputed from
--   these rows with half-life decay by calibrate.py)
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- predictions  (probabilistic hypothesis call + calibration record)
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- market_events  (daily market debrief — learn from the market, trade or not)
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- Meta bookkeeping
-- ---------------------------------------------------------------------------
INSERT OR REPLACE INTO meta(key, value) VALUES ('_schema_version', '8');
INSERT OR REPLACE INTO meta(key, value) VALUES ('_world_model', strftime('%Y-%m-%d','now'));

COMMIT;

PRAGMA foreign_keys = ON;
