-- 0013: trade_intents.direction — first-class long/short on the intent itself.
-- Before this, direction lived only in hypothesis prose (author_intents._infer_direction)
-- and the executor mapped action=open to BUY unconditionally, so the trader agent
-- self-blocked every short ("executor v1 maps action=open to buy"). With this column:
--   author (open, direction=short) -> executor submits SELL (sell-to-open);
--   (exit/trim of a short)         -> executor submits BUY (buy-to-cover).
-- Default 'long' keeps every existing row and code path unchanged.
-- Operator-approved 2026-07-02 (paper account; shorting_enabled on Alpaca).
ALTER TABLE trade_intents ADD COLUMN direction TEXT NOT NULL DEFAULT 'long';
