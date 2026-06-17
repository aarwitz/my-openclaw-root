-- 0011_valuations.sql  (schema v11)
--
-- Intrinsic-value layer. The desk reasoned about catalysts (the world model) but
-- had no notion of what a company is *worth*. `valuations` stores the output of
-- the deterministic valuation engine (workspaces/trading-intel/scripts/valuation.py):
-- a two-stage FCFF DCF + reverse-DCF market-implied growth + a growth-justified
-- earnings-multiple cross-check, blended into a fair value, margin of safety, and
-- zone (cheap/fair/rich). Per-name realized volatility is stored here too so the
-- predictor can width the P10/P90 band by the actual name instead of a generic
-- per-horizon constant.
--
-- Fundamentals: SEC EDGAR (free). Price/vol/beta: Alpaca bars (broker data).
-- Risk-free: FRED. All deterministic + cached; never the browser.

PRAGMA foreign_keys = ON;

BEGIN;

CREATE TABLE IF NOT EXISTS valuations (
  id                  TEXT PRIMARY KEY,
  ticker              TEXT NOT NULL,
  as_of               TEXT NOT NULL,
  applicable          INTEGER NOT NULL DEFAULT 1,   -- 0 for ETFs / unvaluable names
  price               REAL,
  fair_value          REAL,
  margin_of_safety    REAL,                         -- fair_value/price - 1
  zone                TEXT CHECK (zone IN ('cheap','fair','rich','n/a') OR zone IS NULL),
  confidence          REAL,                         -- 0..1, data completeness + method agreement
  dcf_value           REAL,
  eps_multiple_value  REAL,
  implied_growth      REAL,                         -- reverse-DCF: growth the price assumes
  growth_assumed      REAL,                         -- our DCF growth (revenue CAGR, capped)
  wacc                REAL,
  beta                REAL,
  realized_vol_annual REAL,
  pe                  REAL,
  ev_sales            REAL,
  ev_ebitda           REAL,
  p_fcf               REAL,
  reason              TEXT,                          -- why not applicable, when applicable=0
  method_json         TEXT,                          -- full engine output for audit
  created_at          TEXT NOT NULL,
  experiment_id       TEXT
);

CREATE INDEX IF NOT EXISTS idx_valuations_ticker_asof ON valuations(ticker, as_of);
CREATE INDEX IF NOT EXISTS idx_valuations_asof ON valuations(as_of);

INSERT OR REPLACE INTO meta(key, value) VALUES ('_schema_version', '11');
INSERT OR REPLACE INTO meta(key, value) VALUES ('_valuations', strftime('%Y-%m-%d','now'));

COMMIT;

PRAGMA foreign_keys = ON;
