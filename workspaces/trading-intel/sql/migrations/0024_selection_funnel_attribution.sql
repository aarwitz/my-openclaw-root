-- 0024_selection_funnel_attribution
-- Fixed-horizon counterfactual outcomes for every genuine research candidate.
-- Stage flags and outcomes freeze at the first tradable close after decision.

BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS selection_funnel_outcomes (
  id                          TEXT PRIMARY KEY,
  hypothesis_id               TEXT NOT NULL REFERENCES hypotheses(id),
  ticker                      TEXT NOT NULL,
  direction                   TEXT NOT NULL CHECK (direction IN ('long','short')),
  evaluation_horizon          TEXT NOT NULL CHECK (evaluation_horizon IN ('5d','21d','63d')),
  sessions                    INTEGER NOT NULL CHECK (sessions > 0),
  decision_at                 TEXT NOT NULL,
  entry_date                  TEXT,
  exit_date                   TEXT,
  outcome_status              TEXT NOT NULL CHECK (outcome_status IN ('pending','matured','data_blocked')),
  data_reason                 TEXT,
  raw_return_pct              REAL,
  spy_return_pct              REAL,
  directional_excess_pct      REAL,
  quant_scored                INTEGER NOT NULL CHECK (quant_scored IN (0,1)),
  critic_passed               INTEGER NOT NULL CHECK (critic_passed IN (0,1)),
  critic_substantive_passed   INTEGER NOT NULL CHECK (critic_substantive_passed IN (0,1)),
  predicted                   INTEGER NOT NULL CHECK (predicted IN (0,1)),
  intent_authored             INTEGER NOT NULL CHECK (intent_authored IN (0,1)),
  risk_approved               INTEGER NOT NULL CHECK (risk_approved IN (0,1)),
  filled                      INTEGER NOT NULL CHECK (filled IN (0,1)),
  stage_snapshot_json         TEXT NOT NULL,
  computed_at                 TEXT NOT NULL,
  UNIQUE(hypothesis_id, ticker, evaluation_horizon)
);

CREATE INDEX IF NOT EXISTS idx_selection_funnel_horizon_status
  ON selection_funnel_outcomes(evaluation_horizon, outcome_status, entry_date);
CREATE INDEX IF NOT EXISTS idx_selection_funnel_hypothesis
  ON selection_funnel_outcomes(hypothesis_id);

COMMIT;
