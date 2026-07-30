-- A prediction is one realized experiment. Split its total learning credit
-- across linked mechanisms instead of multiplying sample size by link count.
UPDATE mechanism_observations AS observation
SET weight = 1.0 / (
  SELECT COUNT(*)
  FROM mechanism_observations AS sibling
  WHERE sibling.source_type = 'prediction'
    AND sibling.source_id = observation.source_id
)
WHERE observation.source_type = 'prediction'
  AND observation.source_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS idx_mech_obs_prediction_mechanism
  ON mechanism_observations(source_id, mechanism_id)
  WHERE source_type='prediction' AND source_id IS NOT NULL;
