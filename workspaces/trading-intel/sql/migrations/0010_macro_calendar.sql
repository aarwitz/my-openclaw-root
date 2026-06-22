-- 0010_macro_calendar.sql  (schema v10)
--
-- Macro expectations / surprise layer. The desk's biggest blind spot (per the
-- operator's May-2026 jobs-report example) was being SURPRISED by scheduled
-- macro events it could have seen coming. High-impact releases are knowable in
-- advance — the jobs report is the first Friday of the month, CPI is mid-month,
-- FOMC dates are published a year ahead.
--
-- `macro_releases` is a forward calendar + realized-surprise ledger:
--   * scheduled rows let the desk PRE-POSITION duration/risk before a print
--     (and pull the relevant episode + mechanism via retrieve_episodes.py).
--   * after the release, macro_calendar.py --pull-actuals fills actual_value
--     from FRED (keyless), computes the surprise vs prior/consensus, and (on a
--     large surprise) writes a market_event + mechanism_observation so the world
--     model learns the macro->repricing link.

PRAGMA foreign_keys = ON;

BEGIN;

CREATE TABLE IF NOT EXISTS macro_releases (
  id                       TEXT PRIMARY KEY,
  series                   TEXT NOT NULL,             -- NFP / CPI_YOY / CORE_CPI_YOY / UNRATE / FOMC / PCE_YOY
  label                    TEXT NOT NULL,             -- human-readable
  release_date             TEXT NOT NULL,             -- scheduled date (knowable in advance)
  period                   TEXT,                      -- which data period (e.g. '2026-05')
  status                   TEXT NOT NULL DEFAULT 'scheduled' CHECK (status IN ('scheduled','released','skipped')),
  impact                   TEXT NOT NULL DEFAULT 'high' CHECK (impact IN ('high','medium','low')),
  fred_series_id           TEXT,                      -- source series for the actual (NULL for FOMC)
  prior_value              REAL,
  consensus_value          REAL,                      -- nullable: free consensus is scarce
  actual_value             REAL,
  surprise                 REAL,                      -- actual - (consensus or prior)
  surprise_basis           TEXT CHECK (surprise_basis IN ('vs_consensus','vs_prior','vs_trend') OR surprise_basis IS NULL),
  rate_path_lean           TEXT CHECK (rate_path_lean IN ('hawkish','dovish','neutral') OR rate_path_lean IS NULL),
  linked_mechanism_ids_json TEXT,                     -- mechanisms this release tends to fire
  notes                    TEXT,
  created_at               TEXT NOT NULL,
  updated_at               TEXT,
  experiment_id            TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_macro_releases_series_date ON macro_releases(series, release_date);
CREATE INDEX IF NOT EXISTS idx_macro_releases_date ON macro_releases(release_date);
CREATE INDEX IF NOT EXISTS idx_macro_releases_status ON macro_releases(status);

INSERT OR REPLACE INTO meta(key, value) VALUES ('_schema_version', '10');
INSERT OR REPLACE INTO meta(key, value) VALUES ('_macro_calendar', strftime('%Y-%m-%d','now'));

COMMIT;

PRAGMA foreign_keys = ON;
