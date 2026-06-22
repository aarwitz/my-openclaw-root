-- 0006_portfolio_snapshots.sql
--
-- Adds deterministic portfolio snapshots for app equity curve + SPY comparison.
-- Safe to run repeatedly.

BEGIN TRANSACTION;

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
  id                 TEXT PRIMARY KEY,
  captured_at        TEXT NOT NULL,
  equity             REAL NOT NULL,
  last_equity        REAL,
  day_pl             REAL,
  cash               REAL,
  buying_power       REAL,
  spy_close          REAL,
  spy_as_of          TEXT,
  account_status     TEXT,
  source             TEXT NOT NULL DEFAULT 'alpaca_paper'
);

CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_captured_at
  ON portfolio_snapshots(captured_at);

INSERT OR REPLACE INTO meta(key, value) VALUES ('_schema_version', '6');

COMMIT;
