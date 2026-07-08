-- 0017_exit_quality.sql — post-exit rebound tracking (D56)
-- Measures what the market did AFTER every exit so "sold too early" is a
-- measured, learnable quantity instead of an anecdote (AVGO 2026-07-07).
CREATE TABLE IF NOT EXISTS exit_quality (
  id              TEXT PRIMARY KEY,
  intent_id       TEXT NOT NULL UNIQUE REFERENCES trade_intents(id),
  hypothesis_id   TEXT,
  ticker          TEXT NOT NULL,
  exit_reason     TEXT,               -- triggered_by lane (stop/horizon/swap/...)
  exited_at       TEXT NOT NULL,
  exit_price      REAL,
  qty             REAL,
  ret_1d          REAL,               -- ticker return vs exit price after 1 trading day
  ret_3d          REAL,
  ret_5d          REAL,
  spy_ret_5d      REAL,               -- SPY over the same 5d window (context)
  premature_5d    INTEGER,            -- 1 when ret_5d >= +3% (exited into a rebound)
  regret_usd_5d   REAL,               -- qty * exit_price * max(ret_5d, 0)
  computed_at     TEXT NOT NULL,
  final           INTEGER NOT NULL DEFAULT 0  -- 1 once the 5d window has matured
);
CREATE INDEX IF NOT EXISTS idx_exit_quality_reason ON exit_quality(exit_reason, premature_5d);
