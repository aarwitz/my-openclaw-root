from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from . import db

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
SQL_SCHEMA_PATH = ROOT / "sql" / "SQLITE_SCHEMA_V1.sql"
DEFAULT_DB_PATH = ROOT / "sql" / "ats_v6.db"
IMPLEMENTATION_NOTES_PATH = ROOT / "ATS_V6_IMPLEMENTATION_NOTES.md"

REQUIRED_TABLES = {
    "trades",
    "candidate_decisions",
    "trade_attribution",
    "positions",
    "trade_intents",
    "checkpoint_runs",
    "signals",
    "ideas",
    "regime_states",
    "portfolio_risk_snapshots",
}

REQUIRED_TRADE_COLUMNS = {
    "trade_id", "intent_id", "idea_id", "strategy_id", "variant_id", "ticker",
    "direction", "shares", "entry_price", "entry_ts", "exit_price", "exit_ts",
    "stop_loss", "stop_type", "targets_json", "target_type", "thesis_id", "regime_tag_json", "catalyst",
    "conviction", "size_pct", "paper_fill_pnl", "conservative_fill_pnl", "entry_conditions_json",
    "data_quality_flags_json", "thematic_cluster", "engine", "entry_order_id", "exit_order_id",
    "source_origin", "sector_tag", "factor_tags_json", "holding_days", "slippage_vs_mid_bps",
    "slippage_vs_arrival_bps", "status",
}

REQUIRED_CANDIDATE_COLUMNS = {
    "candidate_id", "ts", "source", "strategy_id", "ticker", "signal_summary",
    "score_components_json", "decision", "reject_reason", "watch_until",
    "price_at_decision", "fwd_return_1d", "fwd_return_7d", "fwd_return_30d",
    "signal_holdout_7d_pnl", "signal_holdout_30d_pnl", "eventual_trade_id",
}

REQUIRED_ATTRIBUTION_COLUMNS = {
    "trade_id", "signal_return", "management_return", "sizing_return", "computed_ts"
}


@dataclass
class ValidationResult:
    db_path: str
    tables: list[str]
    sample_trade_id: str
    sample_candidate_id: str
    sample_attribution_trade_id: str
    config_summary: dict[str, Any]
    conviction_trade_id: str
    opportunistic_trade_id: str
    report_preview: dict[str, Any]


def _load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_runtime_config() -> dict[str, Any]:
    risk = _load_yaml(CONFIG_DIR / "risk.yaml")
    strategies = _load_yaml(CONFIG_DIR / "strategies.yaml")
    runtime_risk = risk.get("runtime", {})
    runtime = {
        "schema_version": 1,
        "portfolio_size_usd": runtime_risk.get("portfolio_size_usd"),
        "broker_mode": runtime_risk.get("broker_mode"),
        "allowed_instruments": runtime_risk.get("allowed_instruments", []),
        "engine_types": runtime_risk.get("engine_types", []),
        "storage_paths": {
            "sqlite_db": str(DEFAULT_DB_PATH),
            "schema_sql": str(SQL_SCHEMA_PATH),
            "raw_cache": str(ROOT / "phase2_cache" / "raw"),
            "reports": str(ROOT / "reports"),
            "notes": str(IMPLEMENTATION_NOTES_PATH),
        },
        "risk": risk,
        "strategies": strategies,
    }
    return runtime


def _connect(db_path: Path) -> sqlite3.Connection:
    return db.connect(db_path)


def initialize_database(db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    schema_sql = SQL_SCHEMA_PATH.read_text(encoding="utf-8")
    with db.connect(db_path, write=True) as conn:
        conn.executescript(schema_sql)
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        conn.commit()
    return {"db_path": str(db_path), "tables": tables}


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _reset_runtime_rows(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM trade_attribution")
    conn.execute("DELETE FROM order_events")
    conn.execute("DELETE FROM reconciliation_runs")
    conn.execute("DELETE FROM system_pauses")
    conn.execute("DELETE FROM positions")
    conn.execute("DELETE FROM candidate_decisions")
    conn.execute("DELETE FROM trades")
    conn.execute("DELETE FROM trade_intents")
    conn.execute("DELETE FROM evidence_items")
    conn.execute("DELETE FROM thesis_versions")
    conn.execute("DELETE FROM ideas")
    conn.execute("DELETE FROM signals")
    conn.execute("DELETE FROM regime_states")
    conn.execute("DELETE FROM portfolio_risk_snapshots")


def _insert_common_market_state(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO regime_states (as_of_date, spy_vs_50dma, spy_vs_200dma, spy_dist_from_ath_pct, vix_level, vix_term_structure, us2y, us10y, us30y, curve_shape, ig_spread, hy_spread, tech_vs_spy, semis_vs_spy, pct_spx_above_200dma, revision_breadth, dxy, gold, copper, oil, regime_classification, tech_heat_score, macro_stress_score, breadth_score) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("2026-05-22", 0.05, 0.12, -0.03, 17.8, "contango", 4.9, 4.4, 4.7, "mild_inversion", 1.1, 3.4, 0.02, 0.03, 0.67, 0.58, 104.2, 2380.0, 4.95, 78.5, "risk_on", 7.0, 3.0, 7.0),
    )
    conn.execute(
        "INSERT INTO portfolio_risk_snapshots (snapshot_ts, net_exposure_pct, gross_exposure_pct, long_gross_pct, short_gross_pct, cash_pct, top_name_pct, top_sector_pct, factor_exposure_json, theme_exposure_json, open_risk_pct, daily_drawdown_pct, weekly_drawdown_pct, monthly_drawdown_pct, opportunity_cost_pct, breach_flags_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("2026-05-22T20:40:00Z", 0.42, 0.48, 0.45, 0.03, 0.58, 0.10, 0.22, json.dumps({"ai_semis": 0.10, "software": 0.05}), json.dumps({"ai_capex_cycle": 0.10}), 0.08, -0.002, -0.006, -0.01, 0.0015, json.dumps([])),
    )


def _insert_opportunistic_trade_path(conn: sqlite3.Connection) -> tuple[str, str, str]:
    conn.execute(
        "INSERT INTO signals (signal_id, detected_ts, source_type, source_name, ticker, direction, status, strategy_routes_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("sig-opp-001", "2026-05-22T20:40:00Z", "paste_in", "trusted_source", "NVDA", "long", "promoted", json.dumps(["trusted_source_catalyst"])),
    )
    conn.execute(
        "INSERT INTO ideas (idea_id, opened_ts, ticker, direction, primary_strategy_id, status, aggregate_score, corroboration_count, cash_hurdle_pass, expected_holding_days, expected_return_low, expected_return_base, expected_return_high, risk_flags_json, created_from_signal_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("idea-opp-001", "2026-05-22T20:41:00Z", "NVDA", "long", "trusted_source_catalyst", "traded", 82.5, 2, 1, 14, 0.03, 0.07, 0.12, json.dumps([]), "sig-opp-001"),
    )
    conn.execute(
        "INSERT INTO evidence_items (evidence_id, signal_id, idea_id, evidence_type, source, source_ts, summary, sentiment, strength_score, file_path_or_url, structured_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("evid-opp-001", "sig-opp-001", "idea-opp-001", "news", "trusted_source", "2026-05-22T20:40:30Z", "Trusted catalyst confirmed", "positive", 0.9, "https://example.com/nvda", json.dumps({"corroborations": 2})),
    )
    conn.execute(
        "INSERT INTO trade_intents (intent_id, created_ts, idea_id, strategy_id, variant_id, ticker, direction, target_size_pct, target_shares, entry_style, entry_limit_price, stop_loss, targets_json, trigger_text, invalidator_text, time_stop_days, regime_tag_json, expected_edge_pct, cash_hurdle_pass, approval_reason_json, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("intent-opp-001", "2026-05-22T20:42:00Z", "idea-opp-001", "trusted_source_catalyst", "base", "NVDA", "long", 2.0, 10, "limit_mid", 950.0, 900.0, json.dumps([980.0, 1020.0]), "trusted catalyst confirmed", "gap failure", 10, json.dumps(["risk_on"]), 0.08, 1, json.dumps({"gate": "manual_or_trusted_source"}), "submitted"),
    )
    conn.execute(
        "INSERT INTO trades (trade_id, intent_id, idea_id, strategy_id, variant_id, ticker, direction, shares, entry_price, entry_ts, exit_price, exit_ts, stop_loss, stop_type, targets_json, target_type, thesis_id, regime_tag_json, catalyst, conviction, size_pct, paper_fill_pnl, conservative_fill_pnl, exit_reason, entry_conditions_json, data_quality_flags_json, thematic_cluster, engine, entry_order_id, exit_order_id, source_origin, sector_tag, factor_tags_json, holding_days, slippage_vs_mid_bps, slippage_vs_arrival_bps, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("trade-opp-001", "intent-opp-001", "idea-opp-001", "trusted_source_catalyst", "base", "NVDA", "long", 10, 950.0, "2026-05-22T20:43:00Z", 980.0, "2026-05-29T20:43:00Z", 900.0, "atr_guardrail", json.dumps([980.0, 1020.0]), "ladder", None, json.dumps(["risk_on"]), "trusted_source", "high", 2.0, 300.0, 250.0, "target_hit", json.dumps({"spread_bps": 8, "atr_pct": 0.025, "rsi14": 61}), json.dumps([]), "ai_capex_cycle", "opportunistic", "alpaca-entry-1", "alpaca-exit-1", "trusted_source", "Technology", json.dumps(["ai_semis"]), 7, 4.2, 6.0, "evaluated"),
    )
    conn.execute(
        "INSERT INTO candidate_decisions (candidate_id, ts, source, strategy_id, ticker, signal_summary, score_components_json, decision, reject_reason, watch_until, price_at_decision, fwd_return_1d, fwd_return_7d, fwd_return_30d, signal_holdout_7d_pnl, signal_holdout_30d_pnl, eventual_trade_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("cand-opp-001", "2026-05-22T20:42:30Z", "paste_in", "trusted_source_catalyst", "NVDA", "AI demand catalyst", json.dumps({"catalyst": 30, "liquidity": 20}), "trade", None, None, 948.5, 0.02, 0.05, 0.11, 40.0, 110.0, "trade-opp-001"),
    )
    conn.execute(
        "INSERT INTO positions (position_id, ticker, direction, strategy_id, variant_id, opened_trade_id, shares_open, avg_cost, market_value, unrealized_pnl, stop_loss, targets_json, health_status, add_count, last_review_ts) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("pos-opp-001", "NVDA", "long", "trusted_source_catalyst", "base", "trade-opp-001", 0.0, 950.0, 0.0, 300.0, 900.0, json.dumps([980.0, 1020.0]), "healthy", 0, "2026-05-29T20:43:00Z"),
    )
    conn.execute(
        "INSERT INTO order_events (order_event_id, trade_id, intent_id, broker, broker_order_id, event_ts, event_type, status, side, qty, filled_qty, avg_fill_price, raw_payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("ord-opp-submitted", "trade-opp-001", "intent-opp-001", "alpaca", "alpaca-entry-1", "2026-05-22T20:42:05Z", "submitted", "accepted", "buy", 10, 0, None, json.dumps({"limit_price": 950.0})),
    )
    conn.execute(
        "INSERT INTO order_events (order_event_id, trade_id, intent_id, broker, broker_order_id, event_ts, event_type, status, side, qty, filled_qty, avg_fill_price, raw_payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("ord-opp-filled", "trade-opp-001", "intent-opp-001", "alpaca", "alpaca-entry-1", "2026-05-22T20:43:00Z", "filled", "filled", "buy", 10, 10, 950.0, json.dumps({"fill_price": 950.0})),
    )
    conn.execute(
        "INSERT INTO trade_attribution (trade_id, signal_return, management_return, sizing_return, computed_ts) VALUES (?, ?, ?, ?, ?)",
        ("trade-opp-001", 0.09, 0.015, 0.005, "2026-05-29T21:00:00Z"),
    )
    return "trade-opp-001", "cand-opp-001", "intent-opp-001"


def _insert_conviction_trade_path(conn: sqlite3.Connection) -> str:
    conn.execute(
        "INSERT INTO signals (signal_id, detected_ts, source_type, source_name, ticker, direction, status, strategy_routes_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("sig-conv-001", "2026-05-22T19:00:00Z", "manual", "aaron", "GOOG", "long", "promoted", json.dumps(["high_conviction_thesis"])),
    )
    conn.execute(
        "INSERT INTO ideas (idea_id, opened_ts, ticker, direction, primary_strategy_id, thesis_id, status, aggregate_score, corroboration_count, cash_hurdle_pass, expected_holding_days, expected_return_low, expected_return_base, expected_return_high, risk_flags_json, created_from_signal_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("idea-conv-001", "2026-05-22T19:05:00Z", "GOOG", "long", "high_conviction_thesis", "thesis-goog-001", "traded", 74.0, 3, 1, 180, 0.06, 0.16, 0.28, json.dumps(["monitor_margin_pressure"]), "sig-conv-001"),
    )
    conn.execute(
        "INSERT INTO thesis_versions (thesis_id, version, created_ts, ticker, market_consensus, bot_assumption, disagreement_reason, testable_predictions_json, invalidation_conditions, target_timeframe, position_size_pct, pre_mortem, supersedes_version, change_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("thesis-goog-001", 1, "2026-05-22T19:04:00Z", "GOOG", "Cloud margins stay under pressure", "Cloud margin expands and AI capex monetizes faster than priced", "Street is underestimating operating leverage from AI infrastructure and cloud attach.", json.dumps([
            {"metric": "cloud_margin", "date": "2026-Q3"},
            {"metric": "search_revenue_growth", "date": "2026-Q3"},
            {"metric": "capex_efficiency", "date": "2026-Q4"}
        ]), "Cloud margins contract for two straight quarters", "18_months", 8.0, "AI capex fails to monetize and cloud margins do not inflect.", None, "initial"),
    )
    conn.execute(
        "INSERT INTO evidence_items (evidence_id, signal_id, idea_id, evidence_type, source, source_ts, summary, sentiment, strength_score, file_path_or_url, structured_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("evid-conv-001", "sig-conv-001", "idea-conv-001", "fundamental", "manual_thesis", "2026-05-22T19:06:00Z", "Thesis seeded with explicit disagreement and predictions", "positive", 0.8, None, json.dumps({"prediction_count": 3})),
    )
    conn.execute(
        "INSERT INTO trade_intents (intent_id, created_ts, idea_id, strategy_id, variant_id, ticker, direction, target_size_pct, target_shares, entry_style, entry_limit_price, stop_loss, targets_json, trigger_text, invalidator_text, time_stop_days, regime_tag_json, expected_edge_pct, cash_hurdle_pass, approval_reason_json, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("intent-conv-001", "2026-05-22T19:10:00Z", "idea-conv-001", "high_conviction_thesis", "base", "GOOG", "long", 5.0, 20, "limit_mid", 176.0, 158.0, json.dumps([]), "Thesis starter with positive evidence skew", "Cloud margin deterioration", 180, json.dumps(["risk_on_late_cycle"]), 0.14, 1, json.dumps({"thesis_version": 1}), "submitted"),
    )
    conn.execute(
        "INSERT INTO trades (trade_id, intent_id, idea_id, strategy_id, variant_id, ticker, direction, shares, entry_price, entry_ts, exit_price, exit_ts, stop_loss, stop_type, targets_json, target_type, thesis_id, regime_tag_json, catalyst, conviction, size_pct, paper_fill_pnl, conservative_fill_pnl, exit_reason, entry_conditions_json, data_quality_flags_json, thematic_cluster, engine, entry_order_id, exit_order_id, source_origin, sector_tag, factor_tags_json, holding_days, slippage_vs_mid_bps, slippage_vs_arrival_bps, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("trade-conv-001", "intent-conv-001", "idea-conv-001", "high_conviction_thesis", "base", "GOOG", "long", 20, 176.0, "2026-05-22T19:15:00Z", 188.0, "2026-11-20T19:15:00Z", 158.0, "time_exit_only", json.dumps([]), "none", "thesis-goog-001", json.dumps(["risk_on_late_cycle"]), "conviction_thesis", "C4", 5.0, 240.0, 180.0, "time_exit", json.dumps({"spread_bps": 5, "atr_pct": 0.018, "rsi14": 54}), json.dumps([]), "ai_capex_cycle", "conviction", "alpaca-entry-2", "alpaca-exit-2", "user", "Communication Services", json.dumps(["mega_cap_tech"]), 182, 2.0, 3.5, "evaluated"),
    )
    conn.execute(
        "INSERT INTO candidate_decisions (candidate_id, ts, source, strategy_id, ticker, signal_summary, score_components_json, decision, reject_reason, watch_until, price_at_decision, fwd_return_1d, fwd_return_7d, fwd_return_30d, signal_holdout_7d_pnl, signal_holdout_30d_pnl, eventual_trade_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("cand-conv-001", "2026-05-22T19:11:00Z", "thesis_seed", "high_conviction_thesis", "GOOG", "Valuation dislocation plus AI monetization thesis", json.dumps({"thesis_quality": 28, "valuation_gap": 18}), "trade", None, None, 175.5, 0.01, 0.03, 0.08, 55.0, 140.0, "trade-conv-001"),
    )
    conn.execute(
        "INSERT INTO trade_attribution (trade_id, signal_return, management_return, sizing_return, computed_ts) VALUES (?, ?, ?, ?, ?)",
        ("trade-conv-001", 0.11, 0.01, -0.005, "2026-11-21T21:00:00Z"),
    )
    return "trade-conv-001"


def _insert_reconciliation_and_pause_smoke(conn: sqlite3.Connection) -> None:
    conn.execute(
        "INSERT INTO reconciliation_runs (recon_id, run_ts, venue, status, positions_match, orders_match, ledger_match, notes, mismatch_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("recon-001", "2026-05-29T21:05:00Z", "alpaca", "clean", 1, 1, 1, "Post-trade reconciliation clean", json.dumps({})),
    )
    conn.execute(
        "INSERT INTO system_pauses (pause_id, started_ts, ended_ts, reason, scope, blocks_json, source_ref, active, notes) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("pause-001", "2026-05-30T14:00:00Z", "2026-05-30T14:15:00Z", "order_rejection_spike", "strategy_only", json.dumps(["new_entries"]), "trusted_source_catalyst", 0, "Smoke test pause row"),
    )


def _report_preview(conn: sqlite3.Connection) -> dict[str, Any]:
    strategy_rows = conn.execute(
        "SELECT engine, COUNT(*) AS n_trades, ROUND(SUM(COALESCE(paper_fill_pnl, 0)), 2) AS total_paper_pnl, ROUND(SUM(COALESCE(conservative_fill_pnl, 0)), 2) AS total_conservative_pnl FROM trades GROUP BY engine ORDER BY engine"
    ).fetchall()
    candidate_rows = conn.execute(
        "SELECT decision, COUNT(*) AS n FROM candidate_decisions GROUP BY decision ORDER BY decision"
    ).fetchall()
    attribution_rows = conn.execute(
        "SELECT ROUND(SUM(signal_return), 4) AS signal_return_sum, ROUND(SUM(management_return), 4) AS management_return_sum, ROUND(SUM(sizing_return), 4) AS sizing_return_sum FROM trade_attribution"
    ).fetchone()
    json_probe = conn.execute(
        """
        SELECT
          trade_id,
          CAST(json_extract(entry_conditions_json, '$.spread_bps') AS REAL) AS spread_bps,
          CAST(json_extract(entry_conditions_json, '$.atr_pct') AS REAL) AS atr_pct,
          EXISTS (
            SELECT 1
            FROM json_each(data_quality_flags_json)
            WHERE value = 'stale_quote'
          ) AS has_stale_quote_flag
        FROM trades
        WHERE trade_id = 'trade-opp-001'
        """
    ).fetchone()
    return {
        "engine_summary": [dict(r) for r in strategy_rows],
        "candidate_decision_summary": [dict(r) for r in candidate_rows],
        "attribution_summary": dict(attribution_rows),
        "json_query_probe": dict(json_probe) if json_probe else {},
    }


def validate_schema(db_path: Path = DEFAULT_DB_PATH) -> ValidationResult:
    config = load_runtime_config()
    initialize_database(db_path)
    with db.connect(db_path, write=True) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = REQUIRED_TABLES - tables
        if missing:
            raise AssertionError(f"Missing tables: {sorted(missing)}")
        if not REQUIRED_TRADE_COLUMNS.issubset(_table_columns(conn, "trades")):
            raise AssertionError("trades table missing required columns")
        if not REQUIRED_CANDIDATE_COLUMNS.issubset(_table_columns(conn, "candidate_decisions")):
            raise AssertionError("candidate_decisions table missing required columns")
        if not REQUIRED_ATTRIBUTION_COLUMNS.issubset(_table_columns(conn, "trade_attribution")):
            raise AssertionError("trade_attribution table missing required columns")

        _reset_runtime_rows(conn)
        _insert_common_market_state(conn)
        opportunistic_trade_id, sample_candidate_id, _ = _insert_opportunistic_trade_path(conn)
        conviction_trade_id = _insert_conviction_trade_path(conn)
        _insert_reconciliation_and_pause_smoke(conn)
        conn.commit()

        opp_trade = conn.execute("SELECT trade_id, ticker, status FROM trades WHERE trade_id=?", (opportunistic_trade_id,)).fetchone()
        conv_trade = conn.execute("SELECT trade_id, engine, thesis_id FROM trades WHERE trade_id=?", (conviction_trade_id,)).fetchone()
        candidate = conn.execute("SELECT candidate_id, decision, eventual_trade_id FROM candidate_decisions WHERE candidate_id=?", (sample_candidate_id,)).fetchone()
        attribution = conn.execute("SELECT trade_id, signal_return FROM trade_attribution WHERE trade_id=?", (opportunistic_trade_id,)).fetchone()
        reconciliation = conn.execute("SELECT recon_id, status FROM reconciliation_runs WHERE recon_id='recon-001'").fetchone()
        pause = conn.execute("SELECT pause_id, reason FROM system_pauses WHERE pause_id='pause-001'").fetchone()
        thesis = conn.execute("SELECT thesis_id, version FROM thesis_versions WHERE thesis_id='thesis-goog-001'").fetchone()
        report_preview = _report_preview(conn)
        json_validity = conn.execute(
            """
            SELECT
              json_valid(entry_conditions_json) AS entry_conditions_valid,
              json_valid(data_quality_flags_json) AS data_quality_flags_valid
            FROM trades
            WHERE trade_id = ?
            """,
            (opportunistic_trade_id,),
        ).fetchone()

        assert opp_trade and candidate and attribution, "opportunistic roundtrip inserts missing"
        assert conv_trade and thesis, "conviction roundtrip inserts missing"
        assert reconciliation and pause, "ops smoke rows missing"
        assert json_validity, "json validity probe missing"
        assert json_validity["entry_conditions_valid"] == 1, "entry_conditions_json must be valid JSON"
        assert json_validity["data_quality_flags_valid"] == 1, "data_quality_flags_json must be valid JSON"

    return ValidationResult(
        db_path=str(db_path),
        tables=sorted(REQUIRED_TABLES),
        sample_trade_id=opp_trade["trade_id"],
        sample_candidate_id=candidate["candidate_id"],
        sample_attribution_trade_id=attribution["trade_id"],
        config_summary={
            "portfolio_size_usd": config.get("portfolio_size_usd"),
            "broker_mode": config.get("broker_mode"),
            "allowed_instruments": config.get("allowed_instruments"),
            "engine_types": config.get("engine_types"),
            "storage_paths": config.get("storage_paths"),
            "startup_mode": config["risk"].get("runtime", {}).get("startup_mode"),
            "capital_allocation": config["strategies"].get("allocation_policy", {}).get("capital_allocation_pct"),
            "correlation_thresholds": config["risk"].get("portfolio_controls", {}).get("correlation"),
            "reports": config["risk"].get("reporting"),
        },
        conviction_trade_id=conv_trade["trade_id"],
        opportunistic_trade_id=opp_trade["trade_id"],
        report_preview=report_preview,
    )


def validate_to_dict(db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    result = validate_schema(db_path)
    return {
        "db_path": result.db_path,
        "tables": result.tables,
        "sample_trade_id": result.sample_trade_id,
        "sample_candidate_id": result.sample_candidate_id,
        "sample_attribution_trade_id": result.sample_attribution_trade_id,
        "config_summary": result.config_summary,
        "conviction_trade_id": result.conviction_trade_id,
        "opportunistic_trade_id": result.opportunistic_trade_id,
        "report_preview": result.report_preview,
    }
