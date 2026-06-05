-- 0005_topology_v3_actors.sql
--
-- Topology v3 (Phase E): chat front door + pipeline orchestration moves off
-- the "bessent" agent onto two new agents:
--   * developer  - autonomous engineering / queue mutator (replaces bessent)
--   * overseer   - chat front door + cron orchestrator + queue manager
--
-- This migration widens the actor CHECK constraints on `audits` and
-- `hypotheses` to allow the new actors while keeping the legacy
-- "bessent" value valid for historical rows (we wiped data tables in this
-- same change, so there should be zero "bessent" rows, but we keep the
-- value in the CHECK so older backups can be restored without breaking).
--
-- Run via: python3 workspaces/trading-intel/sql/apply_migration.py 0005

BEGIN TRANSACTION;

------------------------------------------------------------------------
-- audits
------------------------------------------------------------------------
CREATE TABLE audits_new (
  id                  TEXT PRIMARY KEY,
  timestamp           TEXT NOT NULL,
  actor               TEXT NOT NULL CHECK (actor IN (
                          'researcher','quant','critic','trader',
                          'archivist','executor','developer','overseer',
                          'bessent','human','system')),
  entity_type         TEXT NOT NULL,
  entity_id           TEXT NOT NULL,
  action              TEXT NOT NULL,
  before_state        TEXT,
  after_state         TEXT,
  rationale_concise   TEXT CHECK (rationale_concise IS NULL OR length(rationale_concise) <= 500),
  journal_ref         TEXT,
  experiment_id       TEXT
);

INSERT INTO audits_new SELECT * FROM audits;
DROP TABLE audits;
ALTER TABLE audits_new RENAME TO audits;

------------------------------------------------------------------------
-- hypotheses
------------------------------------------------------------------------
CREATE TABLE hypotheses_new (
  id                        TEXT PRIMARY KEY,
  created_at                TEXT NOT NULL,
  created_by                TEXT NOT NULL CHECK (created_by IN (
                                'researcher','quant','critic','trader',
                                'archivist','executor','developer','overseer',
                                'bessent','human')),
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

INSERT INTO hypotheses_new SELECT * FROM hypotheses;
DROP TABLE hypotheses;
ALTER TABLE hypotheses_new RENAME TO hypotheses;

------------------------------------------------------------------------
-- Meta bookkeeping
------------------------------------------------------------------------
INSERT OR REPLACE INTO meta(key, value) VALUES ('_topology_version', 'v3');
INSERT OR REPLACE INTO meta(key, value) VALUES ('_schema_version',  '5');
INSERT OR REPLACE INTO meta(key, value) VALUES ('_actor_check_v3', strftime('%Y-%m-%d','now'));

COMMIT;
