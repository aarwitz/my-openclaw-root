-- 0030: make themes a market-graded research layer with explicit baskets and
-- lineage into hypotheses/intents.  Themes remain observational; they never
-- authorize risk.
BEGIN IMMEDIATE;

DROP INDEX IF EXISTS idx_themes_status_evidence;
DROP INDEX IF EXISTS idx_themes_slug;
DROP INDEX IF EXISTS idx_theme_obs_theme;
DROP INDEX IF EXISTS idx_theme_obs_source;
DROP INDEX IF EXISTS idx_theme_obs_unique_source;

ALTER TABLE themes RENAME TO themes_v1;
ALTER TABLE theme_observations RENAME TO theme_observations_v1;

CREATE TABLE themes (
  id                    TEXT PRIMARY KEY,
  statement             TEXT NOT NULL CHECK (length(statement) <= 1000),
  beneficiaries_json    TEXT NOT NULL DEFAULT '[]',
  victims_json          TEXT NOT NULL DEFAULT '[]',
  status                TEXT NOT NULL CHECK (status IN ('watch','active','fading','dead')),
  source                TEXT NOT NULL CHECK (source IN ('operator','debrief','scanner')),
  created_at            TEXT NOT NULL,
  updated_at            TEXT NOT NULL,
  score                 REAL,
  score_as_of           TEXT,
  last_evidence_at      TEXT,
  evidence_refs_json    TEXT NOT NULL DEFAULT '[]',
  created_by            TEXT NOT NULL DEFAULT 'system',
  experiment_id         TEXT
);
CREATE INDEX idx_themes_status_evidence ON themes(status, last_evidence_at);

INSERT INTO themes (
  id, statement, beneficiaries_json, victims_json, status, source,
  created_at, updated_at, last_evidence_at, evidence_refs_json, created_by,
  experiment_id
)
SELECT id, summary, tickers_json, '[]',
       -- v1's tickers_json did not encode basket side, so it cannot be safely
       -- market-graded as beneficiaries. Preserve it as retired evidence and
       -- require explicit v2 filing for active/watch use.
       'dead',
       CASE created_by WHEN 'human' THEN 'operator'
                       WHEN 'system' THEN 'scanner' ELSE 'debrief' END,
       created_at, updated_at, last_evidence_at, evidence_refs_json,
       created_by, experiment_id
FROM themes_v1;

CREATE TABLE theme_observations (
  id                       TEXT PRIMARY KEY,
  theme_id                 TEXT NOT NULL REFERENCES themes(id),
  observed_at              TEXT NOT NULL,
  source_type              TEXT NOT NULL CHECK (source_type IN ('operator','debrief','scanner','market_event','bar_move','manual')),
  source_id                TEXT,
  ticker                   TEXT,
  move_pct                 REAL,
  outcome                  TEXT NOT NULL CHECK (outcome IN ('support','contradict','mixed','context')),
  beneficiary_return_pct   REAL,
  victim_return_pct        REAL,
  spread_pct               REAL,
  breadth_pct              REAL,
  as_of                    TEXT,
  evidence_ref_json        TEXT NOT NULL DEFAULT '{}',
  notes                    TEXT,
  experiment_id            TEXT
);
CREATE INDEX idx_theme_obs_theme ON theme_observations(theme_id, observed_at);
CREATE INDEX idx_theme_obs_source ON theme_observations(source_type, source_id);
CREATE UNIQUE INDEX idx_theme_obs_unique_source
  ON theme_observations(theme_id, source_type, source_id, IFNULL(ticker,''))
  WHERE source_id IS NOT NULL;

INSERT INTO theme_observations (
  id, theme_id, observed_at, source_type, source_id, ticker, move_pct,
  outcome, as_of, evidence_ref_json, notes, experiment_id
)
SELECT id, theme_id, observed_at, source_type, source_id, ticker, move_pct,
       outcome, substr(observed_at,1,10), evidence_ref_json, notes, experiment_id
FROM theme_observations_v1;

DROP TABLE theme_observations_v1;
DROP TABLE themes_v1;

ALTER TABLE hypotheses ADD COLUMN theme_id TEXT REFERENCES themes(id);
ALTER TABLE trade_intents ADD COLUMN theme_id TEXT REFERENCES themes(id);
CREATE INDEX idx_hypotheses_theme ON hypotheses(theme_id, state);
CREATE INDEX idx_trade_intents_theme ON trade_intents(theme_id, state);

INSERT OR REPLACE INTO meta(key,value) VALUES ('_schema_version','30');
INSERT OR REPLACE INTO meta(key,value) VALUES ('_themes_version','market_graded_v2');

COMMIT;
