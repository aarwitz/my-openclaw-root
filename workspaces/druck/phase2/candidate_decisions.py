from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from .ats_v6 import DEFAULT_DB_PATH, initialize_database


@dataclass
class CandidateDecision:
    candidate_id: str
    ts: str
    source: str
    strategy_id: str
    ticker: str
    signal_summary: str
    score_components_json: dict[str, Any]
    decision: str
    reject_reason: str | None = None
    watch_until: str | None = None
    price_at_decision: float | None = None
    fwd_return_1d: float | None = None
    fwd_return_7d: float | None = None
    fwd_return_30d: float | None = None
    signal_holdout_7d_pnl: float | None = None
    signal_holdout_30d_pnl: float | None = None
    eventual_trade_id: str | None = None

    def to_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["score_components_json"] = json.dumps(self.score_components_json)
        return row


def _connect(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    initialize_database(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def upsert_candidate_decision(decision: CandidateDecision, db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    row = decision.to_row()
    columns = list(row.keys())
    placeholders = ", ".join("?" for _ in columns)
    updates = ", ".join(f"{col}=excluded.{col}" for col in columns[1:])
    sql = f"""
    INSERT INTO candidate_decisions ({', '.join(columns)})
    VALUES ({placeholders})
    ON CONFLICT(candidate_id) DO UPDATE SET {updates}
    """
    with _connect(db_path) as conn:
        conn.execute(sql, [row[c] for c in columns])
        stored = conn.execute(
            "SELECT candidate_id, decision, eventual_trade_id FROM candidate_decisions WHERE candidate_id=?",
            (decision.candidate_id,),
        ).fetchone()
        conn.commit()
    return dict(stored)


def link_trade(candidate_id: str, trade_id: str, db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    with _connect(db_path) as conn:
        conn.execute(
            "UPDATE candidate_decisions SET eventual_trade_id=? WHERE candidate_id=?",
            (trade_id, candidate_id),
        )
        row = conn.execute(
            "SELECT candidate_id, decision, eventual_trade_id FROM candidate_decisions WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        conn.commit()
    return dict(row) if row else {}


def record_holdout_returns(
    candidate_id: str,
    *,
    fwd_return_1d: float | None = None,
    fwd_return_7d: float | None = None,
    fwd_return_30d: float | None = None,
    signal_holdout_7d_pnl: float | None = None,
    signal_holdout_30d_pnl: float | None = None,
    db_path: Path = DEFAULT_DB_PATH,
) -> dict[str, Any]:
    with _connect(db_path) as conn:
        conn.execute(
            """
            UPDATE candidate_decisions
            SET fwd_return_1d = COALESCE(?, fwd_return_1d),
                fwd_return_7d = COALESCE(?, fwd_return_7d),
                fwd_return_30d = COALESCE(?, fwd_return_30d),
                signal_holdout_7d_pnl = COALESCE(?, signal_holdout_7d_pnl),
                signal_holdout_30d_pnl = COALESCE(?, signal_holdout_30d_pnl)
            WHERE candidate_id = ?
            """,
            (fwd_return_1d, fwd_return_7d, fwd_return_30d, signal_holdout_7d_pnl, signal_holdout_30d_pnl, candidate_id),
        )
        row = conn.execute(
            "SELECT candidate_id, fwd_return_1d, fwd_return_7d, fwd_return_30d, signal_holdout_7d_pnl, signal_holdout_30d_pnl FROM candidate_decisions WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        conn.commit()
    return dict(row) if row else {}


def list_candidate_decisions(db_path: Path = DEFAULT_DB_PATH) -> list[dict[str, Any]]:
    with _connect(db_path) as conn:
        rows = conn.execute(
            "SELECT candidate_id, ts, source, strategy_id, ticker, decision, reject_reason, watch_until, eventual_trade_id FROM candidate_decisions ORDER BY ts"
        ).fetchall()
    return [dict(r) for r in rows]


def replay_sample_candidate_paths(db_path: Path = DEFAULT_DB_PATH) -> dict[str, Any]:
    initialize_database(db_path)

    trade_decision = CandidateDecision(
        candidate_id="replay-trade-001",
        ts="2026-05-23T12:50:00Z",
        source="replay_trade",
        strategy_id="trusted_source_catalyst",
        ticker="NVDA",
        signal_summary="AI demand catalyst with corroboration",
        score_components_json={"catalyst": 30, "liquidity": 18},
        decision="trade",
        price_at_decision=950.0,
    )
    watch_decision = CandidateDecision(
        candidate_id="replay-watch-001",
        ts="2026-05-23T12:51:00Z",
        source="replay_watch",
        strategy_id="x_cashtag_paste",
        ticker="PLTR",
        signal_summary="Social mention awaiting corroboration",
        score_components_json={"catalyst": 8, "liquidity": 15},
        decision="watch",
        watch_until="2026-05-30T12:51:00Z",
        price_at_decision=24.5,
    )
    reject_decision = CandidateDecision(
        candidate_id="replay-reject-001",
        ts="2026-05-23T12:52:00Z",
        source="replay_reject",
        strategy_id="analyst_upgrade_momentum",
        ticker="ABC",
        signal_summary="Upgrade with insufficient liquidity",
        score_components_json={"catalyst": 10, "liquidity": 0},
        decision="reject",
        reject_reason="min_dollar_volume_failed",
        price_at_decision=8.0,
    )

    trade_row = upsert_candidate_decision(trade_decision, db_path)
    watch_row = upsert_candidate_decision(watch_decision, db_path)
    reject_row = upsert_candidate_decision(reject_decision, db_path)

    trade_link = link_trade("replay-trade-001", "trade-opp-001", db_path)
    watch_holdouts = record_holdout_returns(
        "replay-watch-001",
        fwd_return_1d=0.01,
        fwd_return_7d=0.05,
        fwd_return_30d=0.08,
        signal_holdout_7d_pnl=25.0,
        signal_holdout_30d_pnl=40.0,
        db_path=db_path,
    )
    reject_holdouts = record_holdout_returns(
        "replay-reject-001",
        fwd_return_1d=-0.02,
        fwd_return_7d=-0.05,
        fwd_return_30d=-0.10,
        signal_holdout_7d_pnl=-10.0,
        signal_holdout_30d_pnl=-25.0,
        db_path=db_path,
    )

    return {
        "trade": trade_row,
        "watch": watch_row,
        "reject": reject_row,
        "trade_link": trade_link,
        "watch_holdouts": watch_holdouts,
        "reject_holdouts": reject_holdouts,
        "all_rows": list_candidate_decisions(db_path),
    }
