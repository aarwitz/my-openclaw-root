-- 0022_internal_paper_snapshot_provenance
--
-- Future portfolio snapshots come from the owned internal paper ledger. Keep
-- pre-cutover history, but label it explicitly instead of letting the obsolete
-- alpaca_paper default make new internal rows lie about their source.

BEGIN IMMEDIATE;

CREATE TABLE portfolio_snapshots_new (
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
  source             TEXT NOT NULL DEFAULT 'internal_paper'
);

INSERT INTO portfolio_snapshots_new (
  id, captured_at, equity, last_equity, day_pl, cash, buying_power,
  spy_close, spy_as_of, account_status, source
)
SELECT
  id,
  captured_at,
  equity,
  last_equity,
  day_pl,
  cash,
  buying_power,
  spy_close,
  spy_as_of,
  account_status,
  CASE
    WHEN source = 'alpaca_history_backfill' AND substr(captured_at, 1, 10) >= '2026-07-07'
      THEN 'internal_paper_history'
    WHEN source = 'alpaca_history_backfill'
      THEN 'legacy_external_history'
    WHEN source = 'alpaca_paper'
      THEN 'legacy_alpaca_paper'
    ELSE source
  END
FROM portfolio_snapshots;

DROP TABLE portfolio_snapshots;
ALTER TABLE portfolio_snapshots_new RENAME TO portfolio_snapshots;
CREATE INDEX idx_portfolio_snapshots_captured_at
  ON portfolio_snapshots(captured_at);

COMMIT;
