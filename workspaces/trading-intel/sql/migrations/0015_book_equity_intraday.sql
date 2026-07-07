-- 0015_book_equity_intraday — intraday equity samples per book (D53).
-- book_equity stays the daily EOD curve; this powers the 1D/1W chart ranges.
CREATE TABLE IF NOT EXISTS book_equity_intraday (
  book   TEXT NOT NULL,
  ts     INTEGER NOT NULL,          -- epoch ms
  equity REAL NOT NULL,
  PRIMARY KEY (book, ts)
);
