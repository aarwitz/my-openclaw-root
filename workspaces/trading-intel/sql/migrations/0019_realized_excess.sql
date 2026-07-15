-- 0019_realized_excess — numeric SPY-relative excess on resolved predictions.
-- rp-payoff-aware-grading-20260715: the grader computed excess but stored it only
-- inside a prose string; the dead-band (|excess| <= 50bps => 'inconclusive', no
-- mechanism observation) and per-mechanism expectancy both need it as a number.

ALTER TABLE predictions ADD COLUMN realized_excess_pct REAL;
