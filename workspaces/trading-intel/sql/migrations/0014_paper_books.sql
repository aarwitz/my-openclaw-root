-- 0014_paper_books — internal paper-trading engine P1 (docs/07, D51).
-- Parallel books: 'desk' (Alpaca-backed, current behavior), 'shadow' (internal
-- ledger mirroring real fills for parity validation), later 'model'/ablations.
-- Additive + backward compatible: every existing row defaults to book='desk'.

ALTER TABLE positions ADD COLUMN book TEXT NOT NULL DEFAULT 'desk';
ALTER TABLE orders    ADD COLUMN book TEXT NOT NULL DEFAULT 'desk';

CREATE TABLE IF NOT EXISTS sim_accounts (
  book           TEXT PRIMARY KEY,
  cash           REAL NOT NULL,
  starting_cash  REAL NOT NULL,
  created_at     TEXT NOT NULL
);

-- Our own equity curve per book (kills the portfolio-history-endpoint bug class):
-- equity = cash + sum(qty * EOD mark) computed by sim_broker.mark_book.
CREATE TABLE IF NOT EXISTS book_equity (
  book    TEXT NOT NULL,
  date    TEXT NOT NULL,             -- YYYY-MM-DD (ET trading date)
  equity  REAL NOT NULL,
  cash    REAL NOT NULL,
  PRIMARY KEY (book, date)
);

-- Corporate actions applied to sim books, one audited row per adjustment —
-- the CRWD-2026-07-02 split-desync class becomes impossible to have silently.
CREATE TABLE IF NOT EXISTS sim_corporate_actions (
  id        TEXT PRIMARY KEY,
  book      TEXT NOT NULL,
  ticker    TEXT NOT NULL,
  action    TEXT NOT NULL CHECK (action IN ('split','dividend')),
  ratio     REAL,                    -- split: numerator/denominator
  amount    REAL,                    -- dividend: per-share cash
  ex_date   TEXT NOT NULL,
  applied_at TEXT NOT NULL,
  UNIQUE (book, ticker, action, ex_date)
);

-- Sim engine gets its OWN position/order tables: desk queries read
-- positions/orders unfiltered, so sim rows must never share those tables
-- (the book columns above remain for the eventual desk cutover).
CREATE TABLE IF NOT EXISTS sim_positions (
  id            TEXT PRIMARY KEY,
  book          TEXT NOT NULL,
  ticker        TEXT NOT NULL,
  qty           REAL NOT NULL,
  cost_basis    REAL NOT NULL DEFAULT 0,
  current_price REAL,
  current_value REAL,
  state         TEXT NOT NULL DEFAULT 'open' CHECK (state IN ('open','closed')),
  opened_at     TEXT NOT NULL,
  closed_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_sim_positions_book ON sim_positions(book, state);

CREATE TABLE IF NOT EXISTS sim_orders (
  order_id       TEXT PRIMARY KEY,
  book           TEXT NOT NULL,
  symbol         TEXT NOT NULL,
  side           TEXT NOT NULL,
  qty            REAL NOT NULL,
  fill_price     REAL NOT NULL,
  source         TEXT NOT NULL,
  filled_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sim_orders_book ON sim_orders(book, filled_at);
