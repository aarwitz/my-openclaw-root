BEGIN;

CREATE TABLE IF NOT EXISTS prediction_challengers (
  id                    TEXT PRIMARY KEY,
  experiment_id         TEXT NOT NULL REFERENCES experiments(id),
  protocol_version      TEXT NOT NULL,
  prediction_id         TEXT NOT NULL REFERENCES predictions(id),
  hypothesis_id         TEXT NOT NULL REFERENCES hypotheses(id),
  variant               TEXT NOT NULL,
  p_correct             REAL NOT NULL CHECK (p_correct >= 0 AND p_correct <= 1),
  predicted_at          TEXT NOT NULL,
  entry_date            TEXT,
  realized_outcome      TEXT CHECK (realized_outcome IN ('correct','incorrect') OR realized_outcome IS NULL),
  realized_excess_pct   REAL,
  brier_score           REAL,
  log_loss              REAL,
  resolved_at           TEXT,
  UNIQUE(experiment_id, prediction_id, variant)
);
CREATE INDEX IF NOT EXISTS idx_prediction_challengers_experiment_variant
  ON prediction_challengers(experiment_id, variant, resolved_at);

COMMIT;
