-- 0016_closed_loop_capital — D55: horizon exits + cash-yield attribution +
-- capital-efficiency history + ML-evidence trust ledger (rp-horizon-exit-20260708,
-- rp-cash-yield-20260708, rp-sizing-2pct-20260708 as amended; approved by Aaron 2026-07-08).

-- Idle-cash yield credits (SGOV-proxy APY, one credit per book per day).
CREATE TABLE IF NOT EXISTS sim_cash_yield_events (
  id           TEXT PRIMARY KEY,
  book         TEXT NOT NULL,
  as_of_date   TEXT NOT NULL,
  annual_yield REAL NOT NULL,
  cash_start   REAL NOT NULL,
  credit       REAL NOT NULL,
  applied_at   TEXT NOT NULL,
  UNIQUE(book, as_of_date)
);

-- Daily return attribution: trading P&L vs cash-yield P&L vs total, per book.
CREATE TABLE IF NOT EXISTS book_return_attribution (
  book                  TEXT NOT NULL,
  date                  TEXT NOT NULL,
  equity                REAL NOT NULL,
  last_equity           REAL,
  trading_pl            REAL NOT NULL,
  cash_yield_pl         REAL NOT NULL,
  total_pl              REAL NOT NULL,
  trading_return_pct    REAL,
  cash_yield_return_pct REAL,
  total_return_pct      REAL,
  created_at            TEXT NOT NULL,
  PRIMARY KEY (book, date)
);

-- Capital-efficiency snapshots (one row per audit run; trend source for the
-- alpha metrics panel).
CREATE TABLE IF NOT EXISTS capital_efficiency_snapshots (
  as_of         TEXT PRIMARY KEY,
  equity        REAL NOT NULL,
  cash          REAL NOT NULL,
  deployed      REAL NOT NULL,
  pct_deployed  REAL,
  pct_blocked   REAL,
  pct_idle      REAL,
  pct_stale     REAL,
  pct_waiting   REAL,
  usd_blocked   REAL,
  usd_idle      REAL,
  usd_stale     REAL,
  usd_waiting   REAL,
  edge_rate     REAL,
  loss_json     TEXT
);

-- Advisory ML-ranker trust ledger: was ml evidence cited, did it agree with the
-- final thesis, and did cited cases outperform once resolved. The ranker stays
-- quarantined (advisory) until its model book proves positive 30-day alpha.
CREATE TABLE IF NOT EXISTS ml_evidence_tracking (
  hypothesis_id          TEXT PRIMARY KEY,
  ticker                 TEXT,
  cited_ml               INTEGER NOT NULL,
  ml_as_of               TEXT,
  ml_rank                INTEGER,
  ml_score               REAL,
  ml_model               TEXT,
  thesis_direction       TEXT,
  ml_direction           TEXT,
  agreement              TEXT,
  resolved_state         TEXT,
  resolved_outperformed  INTEGER,
  updated_at             TEXT NOT NULL
);
