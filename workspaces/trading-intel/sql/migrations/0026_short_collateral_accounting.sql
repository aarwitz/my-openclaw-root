BEGIN;

ALTER TABLE sim_cash_yield_events ADD COLUMN original_cash_start REAL;
ALTER TABLE sim_cash_yield_events ADD COLUMN original_credit REAL;
ALTER TABLE sim_cash_yield_events ADD COLUMN corrected_at TEXT;
ALTER TABLE sim_cash_yield_events ADD COLUMN correction_reason TEXT;

CREATE TABLE sim_ledger_repairs (
  id           TEXT PRIMARY KEY,
  applied_at   TEXT NOT NULL,
  kind         TEXT NOT NULL,
  book         TEXT NOT NULL,
  cash_delta   REAL NOT NULL,
  details_json TEXT NOT NULL
);

ALTER TABLE capital_efficiency_snapshots
  ADD COLUMN method TEXT NOT NULL DEFAULT 'legacy_net_cash_v1';

COMMIT;
