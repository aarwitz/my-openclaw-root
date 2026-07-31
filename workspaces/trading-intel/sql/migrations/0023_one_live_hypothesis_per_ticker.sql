-- 0023_one_live_hypothesis_per_ticker
--
-- Research agents write directly to hypotheses. Enforce the portfolio-level
-- invariant in SQLite so a forgotten prompt instruction cannot recreate the
-- 2026-07-31 live-board explosion. Run hypothesis_hygiene.py --repair first.

BEGIN IMMEDIATE;

CREATE TRIGGER IF NOT EXISTS hypotheses_one_live_ticker_insert
BEFORE INSERT ON hypotheses
WHEN NEW.state IN ('raw','scored','challenged','ready','active')
  AND EXISTS (
    SELECT 1
    FROM json_each(NEW.tickers) incoming
    JOIN hypotheses h
    JOIN json_each(h.tickers) existing
      ON UPPER(existing.value) = UPPER(incoming.value)
    WHERE h.state IN ('raw','scored','challenged','ready','active')
  )
BEGIN
  SELECT RAISE(ABORT, 'one live hypothesis per ticker; update the existing thesis/evidence');
END;

CREATE TRIGGER IF NOT EXISTS hypotheses_one_live_ticker_update
BEFORE UPDATE OF tickers, state ON hypotheses
WHEN NEW.state IN ('raw','scored','challenged','ready','active')
  AND EXISTS (
    SELECT 1
    FROM json_each(NEW.tickers) incoming
    JOIN hypotheses h
    JOIN json_each(h.tickers) existing
      ON UPPER(existing.value) = UPPER(incoming.value)
    WHERE h.id != NEW.id
      AND h.state IN ('raw','scored','challenged','ready','active')
  )
BEGIN
  SELECT RAISE(ABORT, 'one live hypothesis per ticker; update the existing thesis/evidence');
END;

COMMIT;
