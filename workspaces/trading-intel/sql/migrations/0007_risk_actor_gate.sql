-- 0007_risk_actor_gate.sql
--
-- Topology v4: adds the dedicated `risk` agent (risk-manager) to the desk.
-- Risk owns the intent->order gate: sizing limits, exposure / concentration /
-- correlation caps, drawdown guardrails, and VETO authority on trade_intents.
--
-- Changes:
--   1. Widen actor CHECK constraints to include 'risk' on:
--        audits.actor, hypotheses.created_by, rule_proposals.proposer.
--   2. Add 'risk_review' to trade_intents.state (the gate between critic_review
--      and approved). A risk VETO reuses the existing 'blocked' state with a
--      blocked_reason; a risk pass moves the intent to 'approved'.
--   3. New risk_reviews table (analogous to critic_reviews) recording the
--      risk verdict + applied limits for every gated intent.
--
-- SQLite cannot ALTER a CHECK constraint in place, so the affected tables are
-- recreated atomically inside one transaction. Idempotency: re-running is safe
-- because the new CHECK sets are supersets; the INSERT...SELECT copies preserve
-- all rows. Full DB backup is taken by the operator before applying.
--
-- Apply: python3 -c "import sqlite3;sqlite3.connect('state/trading-intel.sqlite').executescript(open('workspaces/trading-intel/sql/migrations/0007_risk_actor_gate.sql').read())"

PRAGMA foreign_keys = OFF;

BEGIN TRANSACTION;

-- ---------------------------------------------------------------------------
-- audits  (+ 'risk')
-- ---------------------------------------------------------------------------
CREATE TABLE audits__new (
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
INSERT INTO audits__new SELECT
  id, timestamp, actor, entity_type, entity_id, action,
  before_state, after_state, rationale_concise, journal_ref, experiment_id
FROM audits;
DROP TABLE audits;
ALTER TABLE audits__new RENAME TO audits;
CREATE INDEX IF NOT EXISTS idx_audits_entity ON audits(entity_type, entity_id, timestamp);

-- ---------------------------------------------------------------------------
-- hypotheses  (created_by + 'risk')
-- ---------------------------------------------------------------------------
CREATE TABLE hypotheses__new (
  id                        TEXT PRIMARY KEY,
  created_at                TEXT NOT NULL,
  created_by                TEXT NOT NULL CHECK (created_by IN ('researcher','quant','critic','risk','trader','executor','archivist','developer','overseer','bessent','human')),
  tickers                   TEXT NOT NULL,
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
INSERT INTO hypotheses__new SELECT
  id, created_at, created_by, tickers, thesis_summary, state, confidence,
  time_horizon, quant_score, scored_at, edge_decay_monthly_pct,
  last_critic_review_at, resolved_at, resolved_state, archivist_grade,
  rationale_concise, journal_ref
FROM hypotheses;
DROP TABLE hypotheses;
ALTER TABLE hypotheses__new RENAME TO hypotheses;
CREATE INDEX IF NOT EXISTS idx_hypotheses_state       ON hypotheses(state);
CREATE INDEX IF NOT EXISTS idx_hypotheses_resolved_at ON hypotheses(resolved_at);

-- ---------------------------------------------------------------------------
-- rule_proposals  (proposer + 'risk')
-- ---------------------------------------------------------------------------
CREATE TABLE rule_proposals__new (
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
INSERT INTO rule_proposals__new SELECT
  id, created_at, proposer, target_artifact, current_value, proposed_value,
  rationale, evidence_refs_json, status, decided_by, decided_at, applied_at,
  experiment_id
FROM rule_proposals;
DROP TABLE rule_proposals;
ALTER TABLE rule_proposals__new RENAME TO rule_proposals;
CREATE INDEX IF NOT EXISTS idx_rule_proposals_status ON rule_proposals(status, created_at);

-- ---------------------------------------------------------------------------
-- trade_intents  (state + 'risk_review')
-- ---------------------------------------------------------------------------
CREATE TABLE trade_intents__new (
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
  broker_order_id          TEXT
);
INSERT INTO trade_intents__new SELECT
  id, hypothesis_id, expression_candidate_id, created_by, created_at, action,
  tranche_type, ticker, vehicle, size, entry_price_target, stop_rule,
  time_horizon, triggered_by, edge_scorecard_json, evidence_freshness_status,
  factor_overlap_status, provenance_completeness_pct, counterargument_quality_score,
  explainability_status, experiment_id, max_fillable_size, modeled_slippage_bps,
  modeled_fill_price, state, blocked_reason, submitted_at, executed_at,
  actual_price, actual_size, broker_order_id
FROM trade_intents;
DROP TABLE trade_intents;
ALTER TABLE trade_intents__new RENAME TO trade_intents;
CREATE INDEX IF NOT EXISTS idx_intent_state ON trade_intents(state);
CREATE INDEX IF NOT EXISTS idx_intent_hyp   ON trade_intents(hypothesis_id);
CREATE INDEX IF NOT EXISTS idx_intent_experiment_state ON trade_intents(experiment_id, state);

-- ---------------------------------------------------------------------------
-- risk_reviews  (NEW — risk verdict + applied limits per gated intent)
-- ---------------------------------------------------------------------------
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

-- ---------------------------------------------------------------------------
-- Meta bookkeeping
-- ---------------------------------------------------------------------------
INSERT OR REPLACE INTO meta(key, value) VALUES ('_schema_version', '7');
INSERT OR REPLACE INTO meta(key, value) VALUES ('_topology_version', 'v4');
INSERT OR REPLACE INTO meta(key, value) VALUES ('_risk_actor', strftime('%Y-%m-%d','now'));

COMMIT;

PRAGMA foreign_keys = ON;
