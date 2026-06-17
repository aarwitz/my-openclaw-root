-- 0012_portfolio_risk.sql  (schema v12)
--
-- Covariance/factor risk layer. The risk gate enforced rule-based caps (per-name,
-- gross, count, drawdown, regime) but was blind to CORRELATION — eight names that
-- are all the same AI-beta bet satisfied every cap. `portfolio_risk` stores the
-- output of workspaces/trading-intel/scripts/risk_model.py each pass: portfolio
-- volatility, parametric VaR/CVaR, per-name risk contributions, the effective
-- number of independent bets, correlation clusters, and univariate factor betas
-- (market/tech/small-cap/momentum/semis/energy/rates/gold proxy ETFs).
--
-- The same module also feeds the risk gate a correlated-cluster exposure cap so a
-- new name highly correlated with existing holdings can't quietly concentrate the
-- book into a single bet. Returns-based, Alpaca bars, deterministic + cached.

PRAGMA foreign_keys = ON;

BEGIN;

CREATE TABLE IF NOT EXISTS portfolio_risk (
  id                     TEXT PRIMARY KEY,
  as_of                  TEXT NOT NULL,
  equity                 REAL,
  n_positions            INTEGER,
  gross_exposure         REAL,
  gross_pct              REAL,
  portfolio_vol_annual   REAL,          -- annualized portfolio volatility
  var_1d_95              REAL,          -- parametric 1-day 95% VaR ($, on gross)
  var_1d_99              REAL,
  cvar_1d_95             REAL,          -- expected shortfall beyond VaR95
  var_1d_95_pct          REAL,          -- VaR95 as % of equity
  effective_bets         REAL,          -- 1 / HHI of risk-contribution shares
  factor_betas_json      TEXT,          -- {market, tech, smallcap, momentum, ...}
  risk_contributions_json TEXT,         -- top names by share of total risk
  clusters_json          TEXT,          -- correlation clusters (>=0.70) + weights
  method_json            TEXT,          -- full snapshot for audit
  created_at             TEXT NOT NULL,
  experiment_id          TEXT
);

CREATE INDEX IF NOT EXISTS idx_portfolio_risk_asof ON portfolio_risk(as_of);

INSERT OR REPLACE INTO meta(key, value) VALUES ('_schema_version', '12');
INSERT OR REPLACE INTO meta(key, value) VALUES ('_portfolio_risk', strftime('%Y-%m-%d','now'));

COMMIT;

PRAGMA foreign_keys = ON;
