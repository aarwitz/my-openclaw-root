-- 0009_episodes_library.sql  (schema v9)
--
-- Replaces the abandoned anonymized "validation_corpus" with a NAMED, DATED
-- episode library the desk can actually reason over and learn from.
--
-- Why the corpus failed: masking tickers/dates to "prevent overfitting" stripped
-- the very signal that makes a case instructive. The honest fix is walk-forward
-- discipline, not anonymization:
--
--   knowable_at  — the earliest moment the signal was available from primary
--                  sources. Retrieval at decision time may ONLY surface episodes
--                  with knowable_at strictly before the decision. This is how we
--                  prevent look-ahead leakage while keeping real names/dates.
--   resolved_at  — when the outcome materialized. Backtest grading uses this.
--
-- Each episode ties a real catalyst -> mechanism -> repricing -> outcome -> lesson,
-- and links to the world-model `mechanisms` table so resolved episodes can fold
-- into the Beta posteriors (see seed_episodes.py --seed-observations).
--
-- Negative controls (correct action = no_trade despite an apparent signal) are
-- kept as first-class named cases, not hidden — `is_negative_control = 1`.

PRAGMA foreign_keys = ON;

BEGIN;

CREATE TABLE IF NOT EXISTS episodes (
  id                    TEXT PRIMARY KEY,
  created_at            TEXT NOT NULL,
  created_by            TEXT NOT NULL CHECK (created_by IN ('researcher','quant','critic','risk','trader','executor','archivist','developer','overseer','human','system')),
  title                 TEXT NOT NULL,                 -- human-readable, named
  tickers_json          TEXT NOT NULL,                 -- JSON array of real tickers
  theme                 TEXT,                          -- ai_disruption / macro_rates / supply_cycle / energy_demand / political_signal / speculative_narrative / launch_risk
  catalyst              TEXT NOT NULL,                 -- what actually happened
  catalyst_class        TEXT CHECK (catalyst_class IN ('macro_release','earnings','policy','product','geopolitical','supply_chain','sentiment','corporate_action','liquidity','other') OR catalyst_class IS NULL),
  knowable_at           TEXT NOT NULL,                 -- as-of: earliest primary-source availability (walk-forward gate)
  resolved_at           TEXT,                          -- when the outcome materialized
  mechanism_id          TEXT REFERENCES mechanisms(id),
  direction             TEXT CHECK (direction IN ('long','short','neutral','risk_off','risk_on') OR direction IS NULL),
  correct_action        TEXT NOT NULL,                 -- what a disciplined desk SHOULD have done
  naive_trap            TEXT,                          -- the tempting wrong action (esp. negative controls)
  observed_moves_json   TEXT,                          -- {"TICKER": pct_move, ...} the realized repricing
  outcome               TEXT CHECK (outcome IN ('thesis_confirmed','thesis_refuted','inconclusive') OR outcome IS NULL),
  horizon               TEXT CHECK (horizon IN ('intraday','swing_1_5d','position_1_4w','trend_1_3m','long_6m_plus') OR horizon IS NULL),
  regime_context        TEXT,
  lesson_concise        TEXT CHECK (lesson_concise IS NULL OR length(lesson_concise) <= 1200),
  is_negative_control   INTEGER NOT NULL DEFAULT 0 CHECK (is_negative_control IN (0,1)),
  confidence            TEXT CHECK (confidence IN ('low','medium','high') OR confidence IS NULL),
  source_refs_json      TEXT,
  experiment_id         TEXT
);

CREATE INDEX IF NOT EXISTS idx_episodes_knowable_at ON episodes(knowable_at);
CREATE INDEX IF NOT EXISTS idx_episodes_mechanism   ON episodes(mechanism_id);
CREATE INDEX IF NOT EXISTS idx_episodes_theme        ON episodes(theme);

-- FTS5 retrieval surface over the prose fields (theme/title/catalyst/lesson).
-- Kept in sync by triggers below. Used by retrieve_episodes.py for similarity
-- search at hypothesis time.
CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
  title, theme, catalyst, lesson_concise,
  content='episodes', content_rowid='rowid'
);

CREATE TRIGGER IF NOT EXISTS episodes_ai AFTER INSERT ON episodes BEGIN
  INSERT INTO episodes_fts(rowid, title, theme, catalyst, lesson_concise)
  VALUES (new.rowid, new.title, new.theme, new.catalyst, new.lesson_concise);
END;
CREATE TRIGGER IF NOT EXISTS episodes_ad AFTER DELETE ON episodes BEGIN
  INSERT INTO episodes_fts(episodes_fts, rowid, title, theme, catalyst, lesson_concise)
  VALUES ('delete', old.rowid, old.title, old.theme, old.catalyst, old.lesson_concise);
END;
CREATE TRIGGER IF NOT EXISTS episodes_au AFTER UPDATE ON episodes BEGIN
  INSERT INTO episodes_fts(episodes_fts, rowid, title, theme, catalyst, lesson_concise)
  VALUES ('delete', old.rowid, old.title, old.theme, old.catalyst, old.lesson_concise);
  INSERT INTO episodes_fts(rowid, title, theme, catalyst, lesson_concise)
  VALUES (new.rowid, new.title, new.theme, new.catalyst, new.lesson_concise);
END;

INSERT OR REPLACE INTO meta(key, value) VALUES ('_schema_version', '9');
INSERT OR REPLACE INTO meta(key, value) VALUES ('_episodes_library', strftime('%Y-%m-%d','now'));

COMMIT;

PRAGMA foreign_keys = ON;
