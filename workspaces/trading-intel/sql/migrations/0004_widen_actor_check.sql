-- Migration 0004: Widen actor / created_by CHECK constraints to include 'bessent' and 'executor'.
-- Background: Topology v2 (DECISION_LOG D37) adds 'bessent' (deterministic dev/infra agent) and
-- 'executor' (deterministic broker lane). Both must be valid values for actor/created_by columns.
-- SQLite cannot ALTER a CHECK constraint in place; we recreate the affected tables atomically.
-- Safe to re-run: noop if the CHECK already includes the new actors.

PRAGMA foreign_keys = OFF;

BEGIN;

-- ---- audits ----
CREATE TABLE audits__new (
  id                  TEXT PRIMARY KEY,
  timestamp           TEXT NOT NULL,
  actor               TEXT NOT NULL CHECK (actor IN ('researcher','quant','critic','trader','archivist','executor','bessent','human','system')),
  entity_type         TEXT NOT NULL,
  entity_id           TEXT NOT NULL,
  action              TEXT NOT NULL,
  before_state        TEXT,
  after_state         TEXT,
  rationale_concise   TEXT CHECK (rationale_concise IS NULL OR length(rationale_concise) <= 500),
  journal_ref         TEXT,
  experiment_id       TEXT
);
INSERT INTO audits__new SELECT * FROM audits;
DROP TABLE audits;
ALTER TABLE audits__new RENAME TO audits;
CREATE INDEX IF NOT EXISTS idx_audits_entity ON audits(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_audits_actor_time ON audits(actor, timestamp);

-- ---- hypotheses ----
CREATE TABLE hypotheses__new (
  id                        TEXT PRIMARY KEY,
  created_at                TEXT NOT NULL,
  created_by                TEXT NOT NULL CHECK (created_by IN ('researcher','quant','critic','trader','archivist','executor','bessent','human')),
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
INSERT INTO hypotheses__new SELECT * FROM hypotheses;
DROP TABLE hypotheses;
ALTER TABLE hypotheses__new RENAME TO hypotheses;
CREATE INDEX IF NOT EXISTS idx_hypotheses_state ON hypotheses(state);
CREATE INDEX IF NOT EXISTS idx_hypotheses_created_by ON hypotheses(created_by);

INSERT OR REPLACE INTO meta(key, value) VALUES ('_schema_version', '4');
INSERT OR REPLACE INTO meta(key, value) VALUES ('_actor_check_v2', '2026-06-03');

COMMIT;

PRAGMA foreign_keys = ON;
