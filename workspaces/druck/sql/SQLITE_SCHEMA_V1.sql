PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS signals (
  signal_id TEXT PRIMARY KEY,
  detected_ts TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_name TEXT,
  source_ref TEXT,
  ticker TEXT NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('long', 'short', 'unknown')),
  raw_payload_path TEXT,
  freshness_window_minutes INTEGER,
  expires_ts TEXT,
  confidence_raw REAL,
  strategy_routes_json TEXT,
  status TEXT NOT NULL CHECK (status IN ('new', 'enriched', 'expired', 'discarded', 'promoted')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_signals_ticker_detected_ts ON signals (ticker, detected_ts);
CREATE INDEX IF NOT EXISTS idx_signals_status_expires_ts ON signals (status, expires_ts);

CREATE TABLE IF NOT EXISTS ideas (
  idea_id TEXT PRIMARY KEY,
  opened_ts TEXT NOT NULL,
  ticker TEXT NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('long', 'short')),
  primary_strategy_id TEXT NOT NULL,
  thesis_id TEXT,
  status TEXT NOT NULL CHECK (status IN ('open', 'watch', 'staged', 'expired', 'rejected', 'traded')),
  aggregate_score REAL,
  corroboration_count INTEGER DEFAULT 0,
  cash_hurdle_pass INTEGER NOT NULL DEFAULT 0 CHECK (cash_hurdle_pass IN (0,1)),
  expected_holding_days INTEGER,
  expected_return_low REAL,
  expected_return_base REAL,
  expected_return_high REAL,
  risk_flags_json TEXT,
  expiry_ts TEXT,
  created_from_signal_id TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (created_from_signal_id) REFERENCES signals(signal_id)
);

CREATE INDEX IF NOT EXISTS idx_ideas_ticker_status ON ideas (ticker, status);
CREATE INDEX IF NOT EXISTS idx_ideas_strategy_status ON ideas (primary_strategy_id, status);

CREATE TABLE IF NOT EXISTS evidence_items (
  evidence_id TEXT PRIMARY KEY,
  signal_id TEXT,
  idea_id TEXT,
  evidence_type TEXT NOT NULL,
  source TEXT NOT NULL,
  source_ts TEXT,
  summary TEXT,
  sentiment TEXT,
  strength_score REAL,
  file_path_or_url TEXT,
  structured_json TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (signal_id) REFERENCES signals(signal_id),
  FOREIGN KEY (idea_id) REFERENCES ideas(idea_id)
);

CREATE INDEX IF NOT EXISTS idx_evidence_signal_id ON evidence_items (signal_id);
CREATE INDEX IF NOT EXISTS idx_evidence_idea_id ON evidence_items (idea_id);
CREATE INDEX IF NOT EXISTS idx_evidence_type_source_ts ON evidence_items (evidence_type, source_ts);

CREATE TABLE IF NOT EXISTS trade_intents (
  intent_id TEXT PRIMARY KEY,
  created_ts TEXT NOT NULL,
  idea_id TEXT NOT NULL,
  strategy_id TEXT NOT NULL,
  variant_id TEXT,
  ticker TEXT NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('long', 'short')),
  target_size_pct REAL NOT NULL,
  target_shares REAL,
  entry_style TEXT NOT NULL CHECK (entry_style IN ('limit_mid', 'limit_passive', 'market', 'marketable_limit')),
  entry_limit_price REAL,
  stop_loss REAL,
  targets_json TEXT,
  trigger_text TEXT,
  invalidator_text TEXT,
  time_stop_days INTEGER,
  regime_tag_json TEXT,
  expected_edge_pct REAL,
  cash_hurdle_pass INTEGER NOT NULL DEFAULT 0 CHECK (cash_hurdle_pass IN (0,1)),
  approval_reason_json TEXT,
  blocked_reason TEXT,
  status TEXT NOT NULL CHECK (status IN ('pending', 'blocked', 'ready', 'submitted', 'canceled', 'filled')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (idea_id) REFERENCES ideas(idea_id)
);

CREATE INDEX IF NOT EXISTS idx_trade_intents_strategy_status ON trade_intents (strategy_id, status);
CREATE INDEX IF NOT EXISTS idx_trade_intents_ticker_created_ts ON trade_intents (ticker, created_ts);

CREATE TABLE IF NOT EXISTS trades (
  trade_id TEXT PRIMARY KEY,
  intent_id TEXT NOT NULL,
  idea_id TEXT,
  strategy_id TEXT NOT NULL,
  variant_id TEXT,
  ticker TEXT NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('long', 'short')),
  shares REAL NOT NULL,
  entry_price REAL,
  entry_ts TEXT,
  exit_price REAL,
  exit_ts TEXT,
  stop_loss REAL,
  stop_type TEXT,
  targets_json TEXT,
  target_type TEXT,
  thesis_id TEXT,
  regime_tag_json TEXT,
  catalyst TEXT,
  conviction TEXT,
  size_pct REAL,
  paper_fill_pnl REAL,
  conservative_fill_pnl REAL,
  exit_reason TEXT,
  entry_conditions_json TEXT,
  data_quality_flags_json TEXT,
  thematic_cluster TEXT,
  engine TEXT CHECK (engine IN ('conviction', 'opportunistic')),
  entry_order_id TEXT,
  exit_order_id TEXT,
  source_origin TEXT CHECK (source_origin IN ('user', 'system', 'trusted_source', 'hybrid')),
  sector_tag TEXT,
  factor_tags_json TEXT,
  holding_days REAL,
  slippage_vs_mid_bps REAL,
  slippage_vs_arrival_bps REAL,
  status TEXT NOT NULL CHECK (status IN ('submitted', 'partially_filled', 'filled', 'managing', 'exited', 'reconciled', 'evaluated', 'archived')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (intent_id) REFERENCES trade_intents(intent_id),
  FOREIGN KEY (idea_id) REFERENCES ideas(idea_id)
);

CREATE INDEX IF NOT EXISTS idx_trades_ticker_entry_ts ON trades (ticker, entry_ts);
CREATE INDEX IF NOT EXISTS idx_trades_strategy_status ON trades (strategy_id, status);
CREATE INDEX IF NOT EXISTS idx_trades_exit_ts ON trades (exit_ts);

CREATE TABLE IF NOT EXISTS positions (
  position_id TEXT PRIMARY KEY,
  ticker TEXT NOT NULL,
  direction TEXT NOT NULL CHECK (direction IN ('long', 'short')),
  strategy_id TEXT NOT NULL,
  variant_id TEXT,
  opened_trade_id TEXT NOT NULL,
  shares_open REAL NOT NULL,
  avg_cost REAL,
  market_value REAL,
  unrealized_pnl REAL,
  stop_loss REAL,
  targets_json TEXT,
  health_status TEXT,
  add_count INTEGER NOT NULL DEFAULT 0,
  last_review_ts TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (opened_trade_id) REFERENCES trades(trade_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_positions_trade ON positions (opened_trade_id);
CREATE INDEX IF NOT EXISTS idx_positions_ticker_strategy ON positions (ticker, strategy_id);

CREATE TABLE IF NOT EXISTS strategy_performance (
  perf_id TEXT PRIMARY KEY,
  strategy_id TEXT NOT NULL,
  variant_id TEXT,
  as_of_date TEXT NOT NULL,
  n INTEGER NOT NULL DEFAULT 0,
  sample_tier TEXT CHECK (sample_tier IN ('debug', 'provisional', 'usable', 'strong')),
  win_rate REAL,
  avg_win REAL,
  avg_loss REAL,
  expectancy REAL,
  total_return REAL,
  alpha_vs_spy REAL,
  alpha_vs_qqq REAL,
  alpha_vs_sector REAL,
  alpha_vs_random_entry_baseline REAL,
  max_drawdown REAL,
  avg_holding_days REAL,
  sharpe_30d REAL,
  sharpe_90d REAL,
  regime_breakdown_json TEXT,
  cluster_breakdown_json TEXT,
  source_breakdown_json TEXT,
  long_short_breakdown_json TEXT,
  clean_vs_flagged_breakdown_json TEXT,
  active_flag INTEGER NOT NULL DEFAULT 1 CHECK (active_flag IN (0,1)),
  pause_reason TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_strategy_performance_unique ON strategy_performance (strategy_id, IFNULL(variant_id, ''), as_of_date);

CREATE TABLE IF NOT EXISTS cost_ledger (
  cost_event_id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  vendor TEXT NOT NULL,
  endpoint TEXT NOT NULL,
  units REAL,
  estimated_cost REAL NOT NULL,
  hard_cost REAL,
  call_purpose TEXT,
  strategy_id TEXT,
  ticker TEXT,
  run_id TEXT,
  status TEXT,
  latency_ms INTEGER,
  metadata_json TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_cost_ledger_vendor_ts ON cost_ledger (vendor, ts);
CREATE INDEX IF NOT EXISTS idx_cost_ledger_run_id ON cost_ledger (run_id);

CREATE TABLE IF NOT EXISTS regime_states (
  as_of_date TEXT PRIMARY KEY,
  spy_vs_50dma REAL,
  spy_vs_200dma REAL,
  spy_dist_from_ath_pct REAL,
  vix_level REAL,
  vix_term_structure TEXT,
  us2y REAL,
  us10y REAL,
  us30y REAL,
  curve_shape TEXT,
  ig_spread REAL,
  hy_spread REAL,
  tech_vs_spy REAL,
  semis_vs_spy REAL,
  pct_spx_above_200dma REAL,
  revision_breadth REAL,
  dxy REAL,
  gold REAL,
  copper REAL,
  oil REAL,
  regime_classification TEXT,
  tech_heat_score REAL,
  macro_stress_score REAL,
  breadth_score REAL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS portfolio_risk_snapshots (
  snapshot_ts TEXT PRIMARY KEY,
  net_exposure_pct REAL,
  gross_exposure_pct REAL,
  long_gross_pct REAL,
  short_gross_pct REAL,
  cash_pct REAL,
  top_name_pct REAL,
  top_sector_pct REAL,
  factor_exposure_json TEXT,
  theme_exposure_json TEXT,
  open_risk_pct REAL,
  daily_drawdown_pct REAL,
  weekly_drawdown_pct REAL,
  monthly_drawdown_pct REAL,
  opportunity_cost_pct REAL,
  breach_flags_json TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_portfolio_risk_snapshot_ts ON portfolio_risk_snapshots (snapshot_ts);

CREATE TABLE IF NOT EXISTS candidate_decisions (
  candidate_id TEXT PRIMARY KEY,
  ts TEXT NOT NULL,
  source TEXT,
  strategy_id TEXT,
  ticker TEXT NOT NULL,
  signal_summary TEXT,
  score_components_json TEXT,
  decision TEXT NOT NULL CHECK (decision IN ('trade', 'watch', 'reject')),
  reject_reason TEXT,
  watch_until TEXT,
  price_at_decision REAL,
  fwd_return_1d REAL,
  fwd_return_7d REAL,
  fwd_return_30d REAL,
  signal_holdout_7d_pnl REAL,
  signal_holdout_30d_pnl REAL,
  eventual_trade_id TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (eventual_trade_id) REFERENCES trades(trade_id)
);

CREATE INDEX IF NOT EXISTS idx_candidate_decisions_ticker_ts ON candidate_decisions (ticker, ts);
CREATE INDEX IF NOT EXISTS idx_candidate_decisions_strategy_decision ON candidate_decisions (strategy_id, decision);

CREATE TABLE IF NOT EXISTS trade_attribution (
  trade_id TEXT PRIMARY KEY,
  signal_return REAL,
  management_return REAL,
  sizing_return REAL,
  computed_ts TEXT NOT NULL,
  attribution_window TEXT DEFAULT 'nightly',
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (trade_id) REFERENCES trades(trade_id)
);

CREATE TABLE IF NOT EXISTS thesis_versions (
  thesis_id TEXT NOT NULL,
  version INTEGER NOT NULL,
  created_ts TEXT NOT NULL,
  ticker TEXT NOT NULL,
  market_consensus TEXT,
  bot_assumption TEXT,
  disagreement_reason TEXT,
  testable_predictions_json TEXT,
  invalidation_conditions TEXT,
  target_timeframe TEXT,
  position_size_pct REAL,
  pre_mortem TEXT,
  supersedes_version INTEGER,
  change_reason TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (thesis_id, version)
);

CREATE TABLE IF NOT EXISTS order_events (
  order_event_id TEXT PRIMARY KEY,
  trade_id TEXT,
  intent_id TEXT,
  broker TEXT NOT NULL,
  broker_order_id TEXT,
  event_ts TEXT NOT NULL,
  event_type TEXT NOT NULL,
  status TEXT,
  side TEXT,
  qty REAL,
  filled_qty REAL,
  avg_fill_price REAL,
  raw_payload_json TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  FOREIGN KEY (trade_id) REFERENCES trades(trade_id),
  FOREIGN KEY (intent_id) REFERENCES trade_intents(intent_id)
);

CREATE INDEX IF NOT EXISTS idx_order_events_trade_id ON order_events (trade_id);
CREATE INDEX IF NOT EXISTS idx_order_events_broker_order_id ON order_events (broker_order_id);

CREATE TABLE IF NOT EXISTS reconciliation_runs (
  recon_id TEXT PRIMARY KEY,
  run_ts TEXT NOT NULL,
  venue TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('clean', 'warning', 'failed')),
  positions_match INTEGER NOT NULL DEFAULT 0 CHECK (positions_match IN (0,1)),
  orders_match INTEGER NOT NULL DEFAULT 0 CHECK (orders_match IN (0,1)),
  ledger_match INTEGER NOT NULL DEFAULT 0 CHECK (ledger_match IN (0,1)),
  notes TEXT,
  mismatch_json TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_reconciliation_runs_run_ts ON reconciliation_runs (run_ts);

CREATE TABLE IF NOT EXISTS system_pauses (
  pause_id TEXT PRIMARY KEY,
  started_ts TEXT NOT NULL,
  ended_ts TEXT,
  reason TEXT NOT NULL,
  scope TEXT NOT NULL,
  blocks_json TEXT NOT NULL,
  source_ref TEXT,
  active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
  notes TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_system_pauses_active ON system_pauses (active, started_ts);
