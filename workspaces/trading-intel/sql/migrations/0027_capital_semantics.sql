BEGIN;

ALTER TABLE capital_efficiency_snapshots ADD COLUMN ledger_cash REAL;
ALTER TABLE capital_efficiency_snapshots ADD COLUMN short_collateral REAL;
ALTER TABLE capital_efficiency_snapshots ADD COLUMN gross_exposure REAL;
ALTER TABLE capital_efficiency_snapshots ADD COLUMN pct_cash_no_edge REAL;
ALTER TABLE capital_efficiency_snapshots ADD COLUMN usd_cash_no_edge REAL;

COMMIT;
