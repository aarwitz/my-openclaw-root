BEGIN;

ALTER TABLE predictions ADD COLUMN thesis_direction TEXT
  CHECK (thesis_direction IN ('long','short') OR thesis_direction IS NULL);
ALTER TABLE predictions ADD COLUMN prediction_policy_version TEXT;
ALTER TABLE predictions ADD COLUMN prediction_policy_hash TEXT;

-- Legacy rows cannot acquire the code version that actually authored them,
-- but their currently canonical direction can be frozen so later thesis edits
-- no longer change grading semantics. Keep the policy hash NULL rather than
-- inventing provenance that was never recorded.
UPDATE predictions
SET thesis_direction = CASE
      WHEN lower(trim((SELECT h.thesis_summary FROM hypotheses h WHERE h.id=predictions.hypothesis_id))) LIKE 'short%'
        OR lower(substr(trim((SELECT h.thesis_summary FROM hypotheses h WHERE h.id=predictions.hypothesis_id)),1,40)) LIKE '%bearish%'
      THEN 'short' ELSE 'long' END,
    prediction_policy_version = 'legacy_unversioned'
WHERE thesis_direction IS NULL;

INSERT OR REPLACE INTO meta(key,value)
VALUES ('_prediction_lineage_cutover','2026-08-02T10:13:58Z');

COMMIT;
