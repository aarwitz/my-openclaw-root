-- 0018_system_era_horizon — add 'system_era' to the benchmarks horizon enum.
-- Attribution honesty (operator, 2026-07-15): pre-2026-07-07 equity includes the
-- operator's manually ported portfolio and operator-directed trades; the
-- autonomous track record starts at the D52 sim cutover. The scoreboard now
-- writes a 'system_era' row anchored at that epoch so the app never presents
-- the manual era as system alpha. benchmarks is a derived-stats table
-- (recomputed every pass) — rebuild is safe.

BEGIN;

CREATE TABLE benchmarks_new (
  id                     TEXT PRIMARY KEY,
  captured_at            TEXT NOT NULL,
  horizon                TEXT NOT NULL CHECK (horizon IN ('intraday','swing_1_5d','position_1_4w','trend_1_3m','long_6m_plus','all','system_era')),
  period_start           TEXT NOT NULL,
  period_end             TEXT NOT NULL,
  portfolio_return_pct   REAL,
  spy_return_pct         REAL,
  alpha_pct              REAL,
  sharpe_estimate        REAL,
  turnover_pct           REAL,
  source_run_id          TEXT
);

INSERT INTO benchmarks_new SELECT * FROM benchmarks;
DROP TABLE benchmarks;
ALTER TABLE benchmarks_new RENAME TO benchmarks;
CREATE INDEX IF NOT EXISTS idx_benchmarks_horizon_captured ON benchmarks(horizon, captured_at);

COMMIT;
