-- 0020_rotation_snapshots — basket-vs-basket rotation observable (D64).
-- Created idempotently by rotation_monitor.py; DDL canonicalized here.
CREATE TABLE IF NOT EXISTS rotation_snapshots (
  axis           TEXT NOT NULL,
  date           TEXT NOT NULL,
  corr_21d       REAL,
  spread_5d_pct  REAL,     -- basket A minus basket B, percent
  spread_21d_pct REAL,
  spread_z       REAL,
  corr_pctile    REAL,
  seesaw         INTEGER NOT NULL DEFAULT 0,
  computed_at    TEXT NOT NULL,
  PRIMARY KEY (axis, date)
);
