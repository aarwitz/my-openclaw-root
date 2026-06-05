-- Migration 0003 - Topology v2 (Bessent + horizon-aware + self-improvement)
-- Effective: 2026-06-03
-- See: workspaces/trading-intel/DECISION_LOG.md D37..D41
--
-- This migration is additive only. It is safe to run repeatedly. Existing
-- CHECK constraints on `audits.actor` and `hypotheses.created_by` cannot be
-- altered in SQLite without table rewrite; for now the `bessent` actor is
-- written through new tables only (rule_proposals, experiments, attribution,
-- benchmarks). A future migration (0004) will rebuild audits/hypotheses
-- with the widened CHECK if needed.

BEGIN;

CREATE TABLE IF NOT EXISTS rule_proposals (
  id                 TEXT PRIMARY KEY,
  created_at         TEXT NOT NULL,
  proposer           TEXT NOT NULL CHECK (proposer IN ('researcher','quant','critic','archivist','trader','executor','bessent','human','system')),
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

INSERT OR REPLACE INTO meta(key, value) VALUES ('_schema_version', '3');
INSERT OR REPLACE INTO meta(key, value) VALUES ('_topology_version', 'v2');

COMMIT;
