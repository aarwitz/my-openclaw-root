-- 0029: first durable theme/evidence store.  This migration records the
-- exact v1 shape applied during TM-293 so old databases can be reproduced.
BEGIN IMMEDIATE;

CREATE TABLE IF NOT EXISTS themes (
  id                    TEXT PRIMARY KEY,
  created_at            TEXT NOT NULL,
  updated_at            TEXT NOT NULL,
  created_by            TEXT NOT NULL CHECK (created_by IN ('researcher','quant','critic','risk','trader','executor','archivist','developer','overseer','bessent','human','system')),
  title                 TEXT NOT NULL,
  slug                  TEXT NOT NULL UNIQUE,
  status                TEXT NOT NULL CHECK (status IN ('active','dormant','retired')),
  summary               TEXT NOT NULL CHECK (length(summary) <= 1000),
  tickers_json          TEXT NOT NULL DEFAULT '[]',
  evidence_refs_json    TEXT NOT NULL DEFAULT '[]',
  market_grade          TEXT NOT NULL DEFAULT 'ungraded' CHECK (market_grade IN ('supportive','mixed','contradicted','ungraded')),
  last_evidence_at      TEXT,
  last_market_event_id  TEXT REFERENCES market_events(id),
  experiment_id         TEXT
);
CREATE INDEX IF NOT EXISTS idx_themes_status_evidence
  ON themes(status, last_evidence_at);
CREATE INDEX IF NOT EXISTS idx_themes_slug ON themes(slug);

CREATE TABLE IF NOT EXISTS theme_observations (
  id                 TEXT PRIMARY KEY,
  theme_id           TEXT NOT NULL REFERENCES themes(id),
  observed_at        TEXT NOT NULL,
  source_type        TEXT NOT NULL CHECK (source_type IN ('market_event','manual','bar_move')),
  source_id          TEXT,
  ticker             TEXT,
  move_pct           REAL,
  outcome            TEXT NOT NULL CHECK (outcome IN ('support','contradict','mixed','context')),
  evidence_ref_json  TEXT NOT NULL DEFAULT '{}',
  notes              TEXT,
  experiment_id      TEXT
);
CREATE INDEX IF NOT EXISTS idx_theme_obs_theme
  ON theme_observations(theme_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_theme_obs_source
  ON theme_observations(source_type, source_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_theme_obs_unique_source
  ON theme_observations(theme_id, source_type, source_id, IFNULL(ticker,''))
  WHERE source_id IS NOT NULL;

COMMIT;
